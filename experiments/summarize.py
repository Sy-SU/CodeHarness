"""Aggregate durable task states by experiment mode."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


def summarize(workspace_root: Path) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for path in sorted(workspace_root.glob("*/state.json")):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        mode = str(state.get("mode") or "unknown")
        variant = str(state.get("experiment_variant") or "")
        group = f"{mode}:{variant}" if variant else mode
        groups[group].append(state)
    result: Dict[str, Dict[str, Any]] = {}
    for mode, rows in groups.items():
        count = len(rows)
        solved = sum(bool(row.get("solved")) for row in rows)

        def average(field: str) -> float:
            return round(sum(float(row.get(field) or 0) for row in rows) / count, 4)

        profile_calls: Dict[str, int] = defaultdict(int)
        for row in rows:
            for profile, calls in (row.get("calls_per_model_profile") or {}).items():
                profile_calls[profile] += int(calls)
        result[mode] = {
            "tasks": count,
            "solved": solved,
            "solve_rate": round(solved / count, 4),
            "average_llm_calls": average("llm_call_count"),
            "average_submissions": average("submission_count"),
            "average_debug_iterations": average("debug_iterations"),
            "average_input_tokens": average("input_tokens"),
            "average_output_tokens": average("output_tokens"),
            "average_estimated_cost": average("estimated_cost"),
            "average_wall_clock_seconds": average("wall_clock_seconds"),
            "calls_per_model_profile": dict(sorted(profile_calls.items())),
        }
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize CodeHarness task results")
    parser.add_argument("workspace_root", nargs="?", default="workspace")
    args = parser.parse_args(argv)
    print(json.dumps(summarize(Path(args.workspace_root)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
