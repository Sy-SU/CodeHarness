"""Per-task files, atomic state updates, and append-only JSONL events."""

from __future__ import annotations

import json
import os
import re
import enum
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
MANAGED_FILES = {"task.json", "state.json", "events.jsonl"}
SECRET_KEYS = {"authorization", "api_key", "api_token", "password", "secret", "token"}
BEARER_PATTERN = re.compile(r"(?i)bearer\s+[^\s,;]+")


def _redact(value: Any, key: str = "") -> Any:
    normalized = key.lower()
    if normalized in SECRET_KEYS or normalized.endswith("_token") or normalized.endswith("_key"):
        return "<redacted>"
    if isinstance(value, str):
        return BEARER_PATTERN.sub("Bearer <redacted>", value)
    if isinstance(value, dict):
        return {str(item_key): _redact(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


class EventType(str, enum.Enum):
    LLM_CALL = "LLM_CALL"
    LLM_RESPONSE = "LLM_RESPONSE"
    TOOL_CALL = "TOOL_CALL"
    TOOL_RESULT = "TOOL_RESULT"
    SUBMISSION = "SUBMISSION"
    JUDGE_RESULT = "JUDGE_RESULT"
    STATE_CHANGE = "STATE_CHANGE"
    MODEL_ESCALATION = "MODEL_ESCALATION"


@dataclass(frozen=True)
class TraceEvent:
    timestamp: str
    type: str
    payload: Dict[str, Any]
    event_id: str = ""
    task_id: str = ""
    phase: Optional[str] = None
    schema_version: str = "phase1-v1"

    @property
    def known_type(self) -> Optional[EventType]:
        try:
            return EventType(self.type)
        except ValueError:
            return None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "task_id": self.task_id,
            "phase": self.phase,
            "type": self.type,
            "payload": _redact(self.payload),
        }


@dataclass
class TaskState:
    task_id: str
    problem_id: str
    current_phase: str = "PLAN"
    attempt_count: int = 0
    submission_count: int = 0
    last_submission_id: Optional[str] = None
    last_verdict: Optional[str] = None
    last_outcome_kind: Optional[str] = None
    current_model_profile: Optional[str] = None
    llm_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    calls_per_model_profile: Dict[str, int] = field(default_factory=dict)
    debug_iterations: int = 0
    wall_clock_seconds: float = 0.0
    solved: bool = False
    mode: str = ""
    experiment_variant: str = ""


class TraceWriter:
    def __init__(
        self,
        path: Path,
        *,
        task_id: str = "",
        phase_getter=None,
    ):
        self.path = path
        self.task_id = task_id
        self.phase_getter = phase_getter

    def append(
        self,
        event_type: str,
        payload: Dict[str, Any],
        *,
        correlation_id: Optional[str] = None,
    ) -> str:
        event_id = f"evt_{uuid.uuid4().hex}"
        safe_payload = dict(payload)
        if correlation_id is not None:
            safe_payload["correlation_id"] = correlation_id
        record = TraceEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            type=event_type,
            payload=safe_payload,
            event_id=event_id,
            task_id=self.task_id,
            phase=self.phase_getter() if self.phase_getter is not None else None,
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.as_dict(), ensure_ascii=False, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event_id


class TaskWorkspace:
    """Own all files for one isolated agent task."""

    def __init__(self, root: Path, state: TaskState):
        self.root = root.resolve()
        self.state = state
        self.trace = TraceWriter(
            self.root / "events.jsonl",
            task_id=state.task_id,
            phase_getter=lambda: self.state.current_phase,
        )

    @classmethod
    def create(
        cls, workspace_root: Path, task_id: str, problem_id: str, mode: str
    ) -> "TaskWorkspace":
        if not TASK_ID_PATTERN.fullmatch(task_id):
            raise ValueError("Invalid task ID")
        root = (workspace_root.resolve() / task_id).resolve()
        if workspace_root.resolve() not in root.parents:
            raise ValueError("Task path escapes workspace root")
        root.mkdir(parents=True, exist_ok=False)
        (root / "artifacts").mkdir()
        state = TaskState(task_id=task_id, problem_id=problem_id, mode=mode)
        workspace = cls(root, state)
        workspace.write_json("task.json", {"task_id": task_id, "problem_id": problem_id, "mode": mode})
        workspace.save_state()
        (root / "events.jsonl").touch()
        return workspace

    def _safe_path(self, relative: str) -> Path:
        if not isinstance(relative, str) or not relative:
            raise ValueError("Workspace path must be a non-empty string")
        requested = Path(relative)
        if requested.is_absolute():
            raise ValueError("Absolute workspace paths are not allowed")
        lexical = self.root / requested
        current = lexical
        while current != self.root:
            if current.is_symlink():
                raise ValueError("Workspace symbolic links are not allowed")
            current = current.parent
        path = lexical.resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("Workspace path traversal is not allowed")
        return path

    def write_text(self, relative: str, content: str, *, overwrite: bool = True) -> None:
        if not isinstance(content, str):
            raise TypeError("Workspace text content must be a string")
        path = self._safe_path(relative)
        if path == self.root:
            raise ValueError("Cannot write to the workspace directory")
        if path.exists() and not overwrite:
            raise FileExistsError(f"Workspace file already exists: {relative}")
        if path.exists() and not path.is_file():
            raise ValueError(f"Workspace path is not a file: {relative}")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(path.parent),
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                temporary_path = Path(handle.name)
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def write_tool_text(
        self,
        relative: str,
        content: str,
        overwrite: bool = False,
    ) -> None:
        resolved_relative = self._safe_path(relative).relative_to(self.root).as_posix()
        if resolved_relative in MANAGED_FILES:
            raise ValueError(f"Tool cannot overwrite managed workspace file: {relative}")
        self.write_text(relative, content, overwrite=overwrite)

    def read_text(self, relative: str) -> str:
        return self._safe_path(relative).read_text(encoding="utf-8")

    def list_files(self, relative: str = ".") -> List[str]:
        base = self._safe_path(relative)
        if not base.exists() or not base.is_dir():
            raise ValueError(f"Workspace directory does not exist: {relative}")
        return sorted(
            path.relative_to(self.root).as_posix()
            for path in base.rglob("*")
            if path.is_file() and not path.is_symlink()
        )

    def write_json(self, relative: str, value: Dict[str, Any]) -> None:
        self.write_text(relative, json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")

    def read_json(self, relative: str) -> Dict[str, Any]:
        value = json.loads(self.read_text(relative))
        if not isinstance(value, dict):
            raise ValueError(f"Workspace JSON must contain an object: {relative}")
        return value

    def save_state(self) -> None:
        self.write_json("state.json", asdict(self.state))
