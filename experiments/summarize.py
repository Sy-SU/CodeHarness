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
        if state.get("configuration_fingerprint"):
            variant = f"{state.get('experiment_strategy') or variant}|{state['configuration_fingerprint']}|{state.get('actual_feedback_mode') or 'unknown'}|{state.get('feedback_mode_status') or 'unobserved'}"
            if state.get("feedback_policy"):
                variant += f"|effective={state.get('effective_feedback_mode') or 'unknown'}|{state['feedback_policy']}"
        group = f"{mode}:{variant}" if variant else mode
        groups[group].append(state)
    result: Dict[str, Dict[str, Any]] = {}
    for mode, rows in groups.items():
        count = len(rows)
        solved = sum(bool(row.get("solved")) for row in rows)
        def uncertain(row):
            return all(field in row for field in ("llm_call_count", "llm_success_count", "llm_failure_count")) and (
                row["llm_call_count"] > row["llm_success_count"] + row["llm_failure_count"])

        def average(field: str) -> Optional[float]:
            if any(row.get(field) is None or (
                field in {"input_tokens", "output_tokens"} and (row.get("llm_usage_missing_count", 0) or uncertain(row))
            ) for row in rows):
                return None
            return round(sum(float(row[field]) for row in rows) / count, 4)

        profile_calls: Dict[str, int] = defaultdict(int)
        failure_kinds: Dict[str, int] = defaultdict(int)
        terminal_statuses: Dict[str, int] = defaultdict(int)
        termination_reasons: Dict[str, int] = defaultdict(int)
        final_verdicts: Dict[str, int] = defaultdict(int)
        task_error_kinds: Dict[str, int] = defaultdict(int)
        for row in rows:
            for profile, calls in (row.get("calls_per_model_profile") or {}).items():
                profile_calls[profile] += int(calls)
            for kind, calls in (row.get("model_failures_by_kind") or {}).items():
                failure_kinds[kind] += int(calls)
            terminal_statuses[str(row.get("terminal_status") or "unrecorded")] += 1
            termination_reasons[str(row.get("termination_reason") or "unrecorded")] += 1
            final_verdicts[str(row.get("last_verdict") or "none")] += 1
            if row.get("error_kind"):
                task_error_kinds[str(row["error_kind"])] += 1
        known_costs = [
            float(row["estimated_cost"])
            for row in rows
            if row.get("estimated_cost") is not None and not uncertain(row)
        ]
        all_costs_known = len(known_costs) == count
        cost_currencies = sorted(
            {
                str(row["cost_currency"])
                for row in rows
                if row.get("cost_currency")
            }
        )
        result[mode] = {
            "tasks": count,
            "solved": solved,
            "solve_rate": round(solved / count, 4),
            "average_llm_calls": average("llm_call_count"),
            "average_llm_successes": average("llm_success_count"),
            "average_llm_failures": average("llm_failure_count"),
            "average_llm_usage_missing": average("llm_usage_missing_count"),
            "average_submission_attempts": average("submission_attempt_count"),
            "average_submissions": average("submission_count"),
            "average_debug_iterations": average("debug_iterations"),
            "average_input_tokens": average("input_tokens"),
            "average_output_tokens": average("output_tokens"),
            "unknown_usage_tasks": sum(bool(row.get("llm_usage_missing_count"))
                or uncertain(row) or row.get("input_tokens") is None or row.get("output_tokens") is None for row in rows),
            "average_estimated_cost": (
                round(sum(known_costs) / count, 4) if all_costs_known else None
            ),
            "known_estimated_cost_tasks": len(known_costs),
            "unknown_estimated_cost_tasks": count - len(known_costs),
            "cost_currencies": cost_currencies,
            "average_wall_clock_seconds": average("wall_clock_seconds"),
            "calls_per_model_profile": dict(sorted(profile_calls.items())),
            "model_failures_by_kind": dict(sorted(failure_kinds.items())),
            "terminal_statuses": dict(sorted(terminal_statuses.items())),
            "termination_reasons": dict(sorted(termination_reasons.items())),
            "final_verdicts": dict(sorted(final_verdicts.items())),
            "task_error_kinds": dict(sorted(task_error_kinds.items())),
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
