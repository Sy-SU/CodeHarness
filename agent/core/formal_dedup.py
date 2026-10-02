"""Task-local formal evaluation identity and frozen-policy cache projection."""
from __future__ import annotations

import hashlib
import json

from agent.oj_client.feedback import VERDICT_ONLY_POLICY, restrict_formal_result
from agent.oj_client.types import JudgeFeedback, SubmissionRecord, SubmissionStatus


FORMAL_DEDUP_POLICY = {
    "version": "task_source_bytes_v1",
    "scope": "task",
    "source_identity": "sha256_exact_submitted_utf8",
    "problem_identity": "sha256_frozen_sanitized_problem",
    "language": "cpp20",
    "unknown_submission": "stop_without_repost",
    "observation": "original_formal_result_under_frozen_feedback_policy",
}


def stable_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def evaluation_identity(task_id, problem_id, problem, code, *, language="cpp20"):
    if problem.get("problem_id") != problem_id:
        raise ValueError("Formal evaluation problem identity mismatch")
    return {"task_id": task_id, "problem_id": problem_id,
        "frozen_problem_sha256": stable_hash(problem), "language": language,
        "source_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest()}


def cached_observation(entry, identity, feedback_policy):
    """Validate associations and project again; disk artifacts are not trusted views."""
    if entry.get("identity") != identity or entry.get("feedback_policy") != feedback_policy:
        raise ValueError("Cached formal evaluation identity/policy mismatch")
    final = SubmissionRecord.from_dict(entry["final"])
    if (final.submission_id != entry.get("submission_id")
            or final.known_status is not SubmissionStatus.FINISHED
            or final.outcome_kind is None):
        raise ValueError("Cached formal evaluation is not a confirmed final result")
    links = entry.get("submission_links", {})
    if links.get("code_sha256") != identity["source_sha256"] or not links.get("solution_version"):
        raise ValueError("Cached formal source association mismatch")
    if feedback_policy == VERDICT_ONLY_POLICY:
        final = restrict_formal_result("wait_for_submission", final)
    feedback = None
    if final.verdict not in {"AC", "IE"}:
        saved = entry.get("feedback")
        # A confirmed final verdict already contains the complete verdict-only
        # observation, including across a crash before the optional feedback GET.
        if saved is None and feedback_policy == VERDICT_ONLY_POLICY:
            feedback = JudgeFeedback(final.verdict, None)
        elif saved is not None:
            feedback = JudgeFeedback.from_dict(saved)
        if feedback is not None:
            if feedback.verdict != final.verdict:
                raise ValueError("Cached feedback verdict differs from formal result")
            if feedback_policy == VERDICT_ONLY_POLICY:
                feedback = restrict_formal_result("get_feedback", feedback)
    return final, feedback
