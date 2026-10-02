"""Bounded read-only files and shallow, mtime-based summary discovery."""
from __future__ import annotations

import json
import math
import os
import stat
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from agent.workspace.task import TASK_ID_PATTERN
from dashboard.models import ArtifactView, TaskSummary
from dashboard.security import Sanitizer


ROOT_FILES = {"task.json", "state.json", "problem.json", "problem.md", "solution.cpp", "result.json"}
SAFE_ARTIFACTS = {
    "result.json", "code-version.json", "submission-created.json", "submission-final.json",
    "submission.json", "feedback.json", "model-response.json", "plan.md", "review.md",
    "execution-config.json",
}
MAX_FILE_BYTES = 2 * 1024 * 1024


class UnsafePath(ValueError):
    pass


def number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) and value >= 0 else None
    except (OverflowError, ValueError):
        return None


def integer(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def string(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


class WorkspaceRepository:
    """Never creates Workspace objects, files, directories, or Agent clients."""
    def __init__(self, root: Path, sanitizer: Optional[Sanitizer] = None):
        self.root = root.resolve()
        self.sanitizer = sanitizer or Sanitizer()
        self._last_json: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def validate_task(task_id: str) -> None:
        if not TASK_ID_PATTERN.fullmatch(task_id) or task_id in {".", ".."}:
            raise UnsafePath("Invalid task ID")

    def path(self, task_id: str, relative: str) -> Path:
        self.validate_task(task_id)
        parts = PurePosixPath(relative).parts
        if not parts or PurePosixPath(relative).is_absolute() or ".." in parts or "\\" in relative:
            raise UnsafePath("Invalid workspace path")
        lexical = self.root / task_id
        for part in ("", *parts):
            lexical = lexical / part
            if lexical.is_symlink():
                raise UnsafePath("Workspace symbolic links are not allowed")
        resolved = lexical.resolve()
        task = self.root / task_id
        if task not in resolved.parents:
            raise UnsafePath("Workspace path escapes task")
        return lexical

    def open_file(self, task_id: str, relative: str) -> int:
        self.path(task_id, relative)
        # dir_fd + O_NOFOLLOW also reject symlinks swapped after path validation.
        current = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in (task_id, *PurePosixPath(relative).parts[:-1]):
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                os.close(current)
                current = child
            fd = os.open(PurePosixPath(relative).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=current)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                os.close(fd)
                raise UnsafePath("Only regular files are readable")
            return fd
        finally:
            os.close(current)

    def read_bytes(self, task_id: str, relative: str, maximum: int = MAX_FILE_BYTES) -> bytes:
        fd = self.open_file(task_id, relative)
        with os.fdopen(fd, "rb") as handle:
            if os.fstat(handle.fileno()).st_size > maximum:
                raise ValueError("File exceeds Dashboard size limit")
            value = handle.read(maximum + 1)
        if len(value) > maximum:
            raise ValueError("File exceeds Dashboard size limit")
        return value

    def read_text(self, task_id: str, relative: str) -> str:
        return self.sanitizer.text(self.read_bytes(task_id, relative).decode("utf-8"))

    def read_json(self, task_id: str, relative: str) -> Tuple[Dict[str, Any], Optional[str]]:
        with self._lock:
            key = (task_id, relative)
            try:
                value = json.loads(self.read_bytes(task_id, relative))
                if not isinstance(value, dict):
                    raise ValueError("Expected JSON object")
                value = self.sanitizer.value(value)
                self._last_json[key] = value
                return value, None
            except (OSError, ValueError, UnicodeError, RecursionError):
                previous = self._last_json.get(key, {})
                suffix = "; showing last valid data" if previous else ""
                return previous, f"{relative}: unavailable / partial data{suffix}"

    def fingerprint(self, task_id: str, relative: str) -> Optional[Tuple[int, int, int]]:
        try:
            info = self.path(task_id, relative).lstat()
            return info.st_ino, info.st_size, info.st_mtime_ns
        except (OSError, ValueError):
            return None

    def discover(self) -> List[str]:
        try:
            with os.scandir(self.root) as entries:
                return sorted(entry.name for entry in entries
                              if not entry.name.startswith(".") and entry.is_dir(follow_symlinks=False)
                              and TASK_ID_PATTERN.fullmatch(entry.name))
        except OSError:
            return []

    def artifacts(self, task_id: str) -> List[ArtifactView]:
        names = sorted(ROOT_FILES | {f"artifacts/{name}" for name in SAFE_ARTIFACTS})
        result = []
        for name in names:
            try:
                fd = self.open_file(task_id, name)
                info = os.fstat(fd)
                os.close(fd)
                if info.st_size <= MAX_FILE_BYTES:
                    result.append(ArtifactView(name, info.st_size))
            except (OSError, ValueError):
                continue
        return result

    def artifact(self, task_id: str, name: str) -> str:
        if name not in ROOT_FILES and name not in {f"artifacts/{item}" for item in SAFE_ARTIFACTS}:
            raise UnsafePath("File is outside the artifact allowlist")
        if name.endswith(".json"):
            value = json.loads(self.read_bytes(task_id, name))
            return json.dumps(self.sanitizer.value(value), ensure_ascii=False, indent=2)
        return self.read_text(task_id, name)

    def summary(self, task_id: str) -> TaskSummary:
        task, task_warning = self.read_json(task_id, "task.json")
        state, state_warning = self.read_json(task_id, "state.json")
        result = {}
        result_warning = None
        if self.fingerprint(task_id, "artifacts/result.json") is not None:
            result, result_warning = self.read_json(task_id, "artifacts/result.json")
        warnings = [warning for warning in (task_warning, state_warning, result_warning) if warning]
        phase = string(state.get("current_phase"))
        terminal_status = string(state.get("terminal_status") or result.get("terminal_status"))
        terminal = bool(terminal_status) or phase == "DONE"
        verdict = string(state.get("last_verdict") or result.get("verdict"))
        fingerprints = [self.fingerprint(task_id, name) for name in
                        ("task.json", "state.json", "events.jsonl", "artifacts/result.json")]
        mtimes = [item[2] / 1e9 for item in fingerprints if item]
        created = string(task.get("created_at"))
        if created is None and fingerprints[0]:
            try:
                info = self.path(task_id, "task.json").stat()
                created = iso(getattr(info, "st_birthtime", info.st_mtime))
            except (OSError, ValueError):
                pass
        updated = iso(max(mtimes)) if mtimes else None
        calls = integer(state.get("llm_call_count"))
        missing_usage = integer(state.get("llm_usage_missing_count"))
        cost_status = string(state.get("cost_estimate_status")) or "unknown"
        currency = string(state.get("cost_currency"))
        amount = number(state.get("estimated_cost"))
        successes, failures = integer(state.get("llm_success_count")), integer(state.get("llm_failure_count"))
        uncertain_call = (calls is not None and successes is not None and failures is not None
                          and calls > successes + failures)
        if uncertain_call:
            cost_status, amount = "pending_usage", None
        if cost_status not in {"known", "not_applicable"} or (cost_status == "known" and not currency):
            amount = None
        if cost_status == "not_applicable" and calls not in {0, None}:
            cost_status, amount = "unknown", None
        usage_known = (missing_usage == 0 or calls == 0) and not uncertain_call
        return TaskSummary(
            task_id=task_id, problem_id=string(state.get("problem_id") or task.get("problem_id")),
            mode=string(state.get("mode") or task.get("mode")),
            profile=string(state.get("current_model_profile")),
            policy=string(state.get("experiment_variant")), current_phase=phase,
            status=terminal_status or ("completed" if terminal else "running" if phase else "unknown"),
            terminal_reason=string(state.get("termination_reason") or result.get("termination_reason")),
            final_verdict=verdict, terminal=terminal, solved=verdict == "AC" and state.get("solved") is True,
            created_at=created, updated_at=updated, finished_at=updated if terminal else None,
            duration=number(state.get("wall_clock_seconds")), llm_call_count=calls,
            submission_attempt_count=integer(state.get("submission_attempt_count")),
            submission_count=integer(state.get("submission_count")),
            debug_iterations=integer(state.get("debug_iterations")),
            input_tokens=integer(state.get("input_tokens")) if usage_known else None,
            output_tokens=integer(state.get("output_tokens")) if usage_known else None,
            usage_missing_count=missing_usage, estimated_cost=amount, cost_status=cost_status,
            cost_currency=currency, data_status="partial" if warnings else "ok", warnings=warnings,
            configuration_fingerprint=string(state.get("configuration_fingerprint")),
            experiment_id=string(state.get("experiment_id")),
            experiment_strategy=string(state.get("experiment_strategy")),
            actual_feedback_mode=string(state.get("actual_feedback_mode")),
            effective_feedback_mode=string(state.get("effective_feedback_mode")),
            feedback_policy=string(state.get("feedback_policy")),
            feedback_mode_status=string(state.get("feedback_mode_status")) or "unobserved",
            sample_gate_status=string(state.get("sample_gate_status")),
        )


class WorkspaceIndex:
    """Shallow discovery and summary-only caching, independent of Trace reads."""
    def __init__(self, repository: WorkspaceRepository, refresh_seconds: float = 1.0):
        self.repository = repository
        self.refresh_seconds = refresh_seconds
        self._checked = float("-inf")
        self._cache: Dict[str, Tuple[Any, TaskSummary]] = {}
        self._lock = threading.RLock()

    def tasks(self, *, force: bool = False) -> List[TaskSummary]:
        with self._lock:
            now = time.monotonic()
            if force or now - self._checked >= self.refresh_seconds:
                found = self.repository.discover()
                for task_id in found:
                    signature = tuple(self.repository.fingerprint(task_id, name) for name in
                                      ("task.json", "state.json", "artifacts/result.json", "events.jsonl"))
                    if task_id not in self._cache or signature != self._cache[task_id][0]:
                        self._cache[task_id] = (signature, self.repository.summary(task_id))
                self._cache = {task_id: self._cache[task_id] for task_id in found}
                self._checked = now
            return [replace(value[1], warnings=list(value[1].warnings)) for value in self._cache.values()]

    def task(self, task_id: str) -> TaskSummary:
        self.repository.validate_task(task_id)
        # Detail reads are immediately fresh, including during Agent atomic writes.
        for task in self.tasks(force=True):
            if task.task_id == task_id:
                return task
        raise FileNotFoundError("Task unavailable")
