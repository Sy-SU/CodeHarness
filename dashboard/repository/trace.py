"""Incremental JSONL reader: complete lines only, unknown events are preserved."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from agent.workspace.task import EventType
from dashboard.models import TimelineEvent
from .workspace import WorkspaceRepository, string


MAX_TRACE_BYTES = 16 * 1024 * 1024
KNOWN_EVENTS = {item.value for item in EventType} | {"TASK_CREATED"}


@dataclass
class TraceSnapshot:
    events: List[TimelineEvent] = field(default_factory=list)
    generation: int = 0
    warnings: List[str] = field(default_factory=list)


@dataclass
class _Cursor:
    inode: Optional[int] = None
    offset: int = 0
    mtime: int = 0
    size: int = 0
    snapshot: TraceSnapshot = field(default_factory=TraceSnapshot)


class TraceReader:
    def __init__(self, repository: WorkspaceRepository):
        self.repository = repository
        self._cursors: Dict[str, _Cursor] = {}
        self._lock = threading.RLock()

    def _event(self, raw: Any) -> TimelineEvent:
        if not isinstance(raw, dict) or not isinstance(raw.get("type"), str):
            raise ValueError("Invalid event")
        raw = self.repository.sanitizer.value(raw)
        payload = raw.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        safe = self.repository.sanitizer.value(payload, compact=True)
        # Prompts and response bodies have dedicated code/file views, not timeline blobs.
        for name in ("messages", "content"):
            if name in safe:
                safe[name] = "<body omitted>"
        event_type = raw["type"]
        summary = safe.get("tool") or safe.get("solution_version") or safe.get("terminal_status")
        if event_type == "STATE_CHANGE":
            summary = f"{safe.get('from', 'Unknown')} → {safe.get('to', 'Unknown')}"
        elif event_type in {"LLM_CALL", "LLM_RESPONSE"}:
            summary = " · ".join(str(safe[key]) for key in ("role", "profile", "model", "status") if safe.get(key))
        elif event_type in {"SUBMISSION", "JUDGE_RESULT"}:
            summary = " · ".join(str(safe[key]) for key in ("submission_id", "verdict", "status") if safe.get(key))
        return TimelineEvent(
            timestamp=string(raw.get("timestamp")),
            event_type=event_type if event_type in KNOWN_EVENTS else "UNKNOWN EVENT",
            original_type=event_type if event_type not in KNOWN_EVENTS else None,
            phase=string(raw.get("phase")), correlation_id=string(safe.get("correlation_id")),
            summary=str(summary or event_type), metadata=safe, event_id=string(raw.get("event_id")),
        )

    def read(self, task_id: str) -> TraceSnapshot:
        with self._lock:
            cursor = self._cursors.setdefault(task_id, _Cursor())
            try:
                fd = self.repository.open_file(task_id, "events.jsonl")
                with os.fdopen(fd, "rb") as handle:
                    info = os.fstat(handle.fileno())
                    if info.st_size > MAX_TRACE_BYTES:
                        raise ValueError("Trace exceeds size limit")
                    reset = (cursor.inode != info.st_ino or info.st_size < cursor.size or
                             (info.st_size == cursor.size and cursor.mtime != info.st_mtime_ns))
                    if reset:
                        cursor.snapshot = TraceSnapshot(generation=cursor.snapshot.generation + 1)
                        cursor.offset = 0
                    cursor.inode, cursor.size, cursor.mtime = info.st_ino, info.st_size, info.st_mtime_ns
                    handle.seek(cursor.offset)
                    data = handle.read(MAX_TRACE_BYTES + 1)
                    if len(data) > MAX_TRACE_BYTES:
                        raise ValueError("Trace exceeds size limit")
                end = data.rfind(b"\n") + 1
                for line in data[:end].splitlines():
                    if not line.strip():
                        continue
                    try:
                        cursor.snapshot.events.append(self._event(json.loads(line)))
                    except (ValueError, UnicodeError, RecursionError):
                        if "Invalid JSONL line skipped" not in cursor.snapshot.warnings:
                            cursor.snapshot.warnings.append("Invalid JSONL line skipped")
                cursor.offset += end
                warnings = [item for item in cursor.snapshot.warnings if item != "Trace unavailable; showing last valid events"]
                if end < len(data):
                    warnings.append("Incomplete JSONL tail; awaiting append")
                return TraceSnapshot(list(cursor.snapshot.events), cursor.snapshot.generation, warnings)
            except (OSError, ValueError):
                return TraceSnapshot(list(cursor.snapshot.events), cursor.snapshot.generation,
                                     [*cursor.snapshot.warnings, "Trace unavailable; showing last valid events"])
