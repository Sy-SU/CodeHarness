"""Contest projections using the existing bounded, no-follow filesystem reader."""
from __future__ import annotations

import json
import os

from agent.workspace.task import TASK_ID_PATTERN
from experiments.config import IDENTIFIER
from agent.oj_client.contests import contest_identifier
from experiments.contest import enrich_report


class ContestRepository:
    def __init__(self, repository):
        self.repository = repository

    def detail(self, run_id):
        if not isinstance(run_id, str) or not IDENTIFIER.fullmatch(run_id):
            raise ValueError("Invalid contest run ID")
        value = json.loads(self.repository.read_bytes(".contests", f"{run_id}/report.json"))
        if (not isinstance(value, dict) or value.get("schema_version") != "contest-test-v1"
                or value.get("run_id") != run_id
                or value.get("status") not in {"queued", "fetching", "running", "completed",
                                               "blocked", "failed", "stopped", "interrupted"}):
            raise ValueError("Invalid contest report")
        contest_identifier(value.get("contest_id"))
        problems = value.get("problems")
        if not isinstance(problems, list) or len(problems) > 1000:
            raise ValueError("Invalid contest problem records")
        for problem in problems:
            if (not isinstance(problem, dict) or not isinstance(problem.get("problem_id"), str)
                    or not isinstance(problem.get("label"), str)
                    or (problem.get("task_id") is not None and (
                        not isinstance(problem["task_id"], str)
                        or not TASK_ID_PATTERN.fullmatch(problem["task_id"])
                        or problem["task_id"] in {".", ".."}))):
                raise ValueError("Invalid contest task reference")
        try:
            metadata = json.loads(self.repository.read_bytes(".contests", f"{run_id}/performance.json"))
            if not isinstance(metadata, dict):
                raise ValueError("Invalid Performance metadata")
        except FileNotFoundError:
            metadata = None
        except (OSError, ValueError):
            metadata = {"run_id": None}  # malformed/symlink metadata cannot display a value
        value = enrich_report(value, metadata)
        return self.repository.sanitizer.value(value)

    def list(self, limit=100):
        try:
            base = self.repository.path(".contests", "report-probe").parent
            with os.scandir(base) as entries:
                candidates = []
                for entry in entries:
                    if len(candidates) >= 1000:
                        break
                    if entry.is_dir(follow_symlinks=False) and IDENTIFIER.fullmatch(entry.name):
                        candidates.append((entry.stat(follow_symlinks=False).st_mtime_ns, entry.name))
        except (OSError, ValueError):
            return []
        reports = []
        for _, run_id in sorted(candidates, reverse=True):
            if len(reports) >= limit:
                break
            try:
                value = self.detail(run_id)
                reports.append({key: value.get(key) for key in
                    ("run_id", "contest_id", "title", "status", "reason", "accepted", "total_problems",
                     "official_performance", "official_performance_status")})
            except (OSError, ValueError, TypeError, KeyError, RecursionError):
                continue
        return reports
