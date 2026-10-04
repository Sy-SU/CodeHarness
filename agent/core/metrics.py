"""Read-only causal metrics over ordered Trace, without judging program output."""
from __future__ import annotations

import json
import re

from .checker import PROGRAM_FAILURES, EXECUTION_FAILURES

HASH = re.compile(r"[0-9a-f]{64}")


def trace_events(text):
    events, invalid = [], 0
    for line in text.splitlines():
        try:
            value = json.loads(line)
            if not isinstance(value, dict) or not isinstance(value.get("payload"), dict):
                raise ValueError("Invalid event")
            events.append(value)
        except (ValueError, TypeError):
            invalid += 1
    return events, invalid


def recovery_metrics(events, state, *, invalid_lines=0):
    candidates, submissions, results, samples, failures = {}, {}, {}, [], []
    calls, responses, replans, invalid_outputs, custom_runs = {}, {}, 0, 0, 0
    reviews, checker_runs, issues = set(), 0, []
    reuses = {}
    checker_generation, checker_sanity, checker_decisions, checker_execution, checker_stops = [], [], [], [], []
    for index, event in enumerate(events):
        payload = event.get("payload", {})
        kind, correlation = event.get("type"), payload.get("correlation_id")
        if kind == "LLM_CALL" and correlation:
            calls[correlation] = (index, payload)
        elif kind == "LLM_RESPONSE" and correlation:
            responses[correlation] = (index, payload)
        elif kind == "CODE_VERSION":
            version, digest = payload.get("solution_version"), payload.get("code_sha256")
            if isinstance(version, str) and isinstance(digest, str) and HASH.fullmatch(digest):
                candidates.setdefault(version, (index, payload))
        elif kind == "SUBMISSION" and payload.get("submission_id"):
            submissions.setdefault(str(payload["submission_id"]), (index, payload))
        elif kind == "JUDGE_RESULT" and payload.get("submission_id"):
            identifier = str(payload["submission_id"])
            created = submissions.get(identifier)
            version = payload.get("solution_version")
            candidate = candidates.get(version)
            if (created and candidate and created[0] < index and candidate[0] < created[0]
                    and all(payload.get(key) == created[1].get(key) == candidate[1].get(key)
                            for key in ("solution_version", "code_sha256", "model_call_id"))):
                results[identifier] = (index, payload)
                if payload.get("verdict") in PROGRAM_FAILURES:
                    failures.append((index, "formal_" + payload["verdict"], payload))
            else:
                issues.append("judge_candidate_association_invalid")
        elif kind == "SAMPLE_RESULT":
            version = payload.get("solution_version")
            candidate = candidates.get(version)
            if (candidate and candidate[0] < index
                    and payload.get("code_sha256") == candidate[1].get("code_sha256")):
                samples.append((index, payload))
                # Old token mismatches without an explicit checker are not new
                # evidence of algorithm failure on a special/unknown problem.
                verified_wrong = (payload.get("status") == "sample_wrong_answer"
                    and (payload.get("checker") or {}).get("kind") in {"exact", "token", "float", "special"}
                    and (payload.get("checker") or {}).get("source") != "legacy_whitespace_tokens_v1")
                verified_program = (payload.get("status") == "sample_program_failure"
                    and payload.get("verdict") in PROGRAM_FAILURES)
                if (payload.get("sample_policy") in {"semantic_check", "execution_only"}
                        and payload.get("status") == "sample_execution_failure"
                        and payload.get("passed") is False and payload.get("verdict") in EXECUTION_FAILURES):
                    failures.append((index, "sample_execution_failure", payload))
                elif (payload.get("passed") is False and (verified_wrong or verified_program)
                        and payload.get("reason") != "llm_generated_checker_unverified"):
                    failures.append((index, "sample_failure", payload))
        elif kind == "FORMAL_RESULT_REUSED":
            version = payload.get("solution_version")
            candidate = candidates.get(version)
            identifier = str(payload.get("source_submission_id"))
            source = results.get(identifier)
            identity = payload.get("evaluation_identity") or {}
            observed = (payload.get("observation") or {}).get("final") or {}
            if (candidate and source and candidate[0] < index and source[0] < index
                    and version not in reuses and payload.get("formal_submission_reused") is True
                    and payload.get("observation_source") == "cached_formal_result"
                    and payload.get("source_sha256") == payload.get("code_sha256")
                        == candidate[1].get("code_sha256") == source[1].get("code_sha256")
                        == identity.get("source_sha256")
                    and payload.get("model_call_id") == candidate[1].get("model_call_id")
                    and payload.get("source_solution_version") == source[1].get("solution_version")
                    and payload.get("source_model_call_id") == source[1].get("model_call_id")
                    and observed.get("submission_id") == identifier
                    and observed.get("verdict") == source[1].get("verdict")
                    and identity.get("task_id") == state.get("task_id")
                    and identity.get("problem_id") == state.get("problem_id")
                    and payload.get("candidate_duplicate") == (version != source[1].get("solution_version"))):
                reuses[version] = (index, payload)
            else:
                issues.append("cached_formal_candidate_association_invalid")
        elif kind == "TOOL_CALL" and payload.get("tool") == "run_code":
            custom_runs += 1
        elif kind == "REPLAN":
            replans += 1
        elif kind == "CHECKER_RUN":
            checker_runs += 1
        elif kind == "CHECKER_GENERATION_RESULT":
            checker_generation.append(payload)
        elif kind == "CHECKER_SANITY_RESULT":
            checker_sanity.append(payload)
        elif kind == "CHECKER_DECISION":
            checker_decisions.append(payload)
        elif kind == "CHECKER_EXECUTION_RESULT":
            checker_execution.append(payload)
        elif kind == "UNVERIFIED_CHECKER_STOP":
            checker_stops.append(payload)
        elif kind == "TASK_TERMINATED" and payload.get("terminal_status") == "invalid_model_output":
            invalid_outputs += 1
        elif kind == "REVIEW_RESULT" and payload.get("matched") is True:
            identifier = str(payload.get("submission_id"))
            accepted_review = results.get(identifier)
            if (accepted_review and accepted_review[0] < index
                    and accepted_review[1].get("verdict") == "AC"
                    and all(payload.get(key) == accepted_review[1].get(key)
                            for key in ("solution_version", "code_sha256", "model_call_id"))):
                reviews.add(identifier)
            elif (accepted_review and accepted_review[1].get("verdict") == "AC"
                    and payload.get("formal_submission_reused") is True
                    and payload.get("solution_version") in reuses
                    and reuses[payload["solution_version"]][0] < index
                    and reuses[payload["solution_version"]][1].get("source_submission_id") == identifier
                    and all(payload.get(key) == reuses[payload["solution_version"]][1].get(key)
                            for key in ("solution_version", "code_sha256", "model_call_id"))):
                reviews.add(identifier)

    for field, count in (("submission_count", len(submissions)), ("llm_call_count", len(calls)),
                         ("llm_success_count", sum(p.get("status") == "succeeded" for _, p in responses.values())),
                         ("llm_failure_count", sum(p.get("status") == "failed" for _, p in responses.values())),
                         ("custom_run_count", custom_runs)):
        if field in state and state[field] != count:
            issues.append(field + "_trace_mismatch")
    duplicates = sum(payload.get("candidate_duplicate") is True for _, payload in reuses.values())
    if "duplicate_candidate_count" in state and state["duplicate_candidate_count"] != duplicates:
        issues.append("duplicate_candidate_count_trace_mismatch")

    ordered = list(submissions)
    first = results.get(ordered[0]) if ordered else None
    first_ac = first[1].get("verdict") == "AC" if first else (False if not ordered else None)
    first_candidate = next(iter(candidates), None)
    initial_samples = [p for _, p in samples if p.get("solution_version") == first_candidate]
    # A prefix of passed samples before an interruption is not an all-samples pass.
    expected_count = state.get("public_sample_count")
    first_sample_pass = None
    if initial_samples:
        if any(p.get("passed") is False and p.get("status") != "sample_execution_failure" for p in initial_samples):
            first_sample_pass = False
        elif (isinstance(expected_count, int) and not isinstance(expected_count, bool)
              and len(initial_samples) == expected_count
              and {p.get("sample_index") for p in initial_samples} == set(range(1, expected_count + 1))
              and all(p.get("passed") is True and p.get("reason") != "llm_generated_checker_unverified"
                      for p in initial_samples)):
            first_sample_pass = True

    final_id = str(state.get("last_submission_id"))
    accepted = results.get(final_id)
    final_ac = (state.get("solved") is True and state.get("last_verdict") == "AC"
                and accepted is not None and accepted[1].get("verdict") == "AC"
                and accepted[1].get("solution_version") == state.get("solution_version")
                and accepted[1].get("code_sha256") == state.get("solution_sha256"))
    # A reused acceptance is a real observation, but not a fresh formal result
    # or proof that the repeated candidate repaired an algorithm.
    accepted_reuse = reuses.get(state.get("solution_version"))
    relevant, evidence = [], []
    successful_debug = 0
    if final_ac:
        _, accepted_payload = accepted
        candidate_index, candidate = candidates[accepted_payload["solution_version"]]
        call_id = candidate.get("model_call_id")
        call = calls.get(call_id)
        response = responses.get(call_id)
        generated = (call is not None and response is not None
            and call[0] < response[0] < candidate_index
            and response[1].get("status") == "succeeded"
            and response[1].get("role") == call[1].get("role")
            and call[1].get("purpose") != "sample_checker_generation")
        debug = generated and call[1].get("role") == "DEBUG"
        for failure_index, failure_type, failure in failures:
            if (failure_index >= candidate_index or failure.get("solution_version") == candidate.get("solution_version")
                    or not generated or call[0] <= failure_index):
                continue
            relevant.append(failure_type)
            evidence.append({"failure_type": failure_type, "failure": {
                    key: failure.get(key) for key in ("solution_version", "code_sha256", "model_call_id",
                                                     "submission_id", "verdict", "sample_index")},
                "debug_model_call_id": call_id if debug and call[0] > failure_index else None,
                "accepted_candidate": candidate, "accepted_submission": accepted_payload,
                "review_completed": final_id in reviews})
        successful_debug = int(debug and any(e["debug_model_call_id"] for e in evidence))

    recovery_types = sorted(set(relevant))
    formal_recovery = any(e["failure_type"].startswith("formal_") and e["debug_model_call_id"]
                          and e["review_completed"] for e in evidence)
    metrics = {"schema_version": "recovery_v1",
        "metrics_status": "incomplete_trace" if invalid_lines or issues else "derived",
        "metrics_warnings": issues,
        "first_candidate_sample_pass": first_sample_pass,
        "first_formal_submission_ac": first_ac, "first_try_ac": first_ac,
        "recovered_to_ac": bool(recovery_types),
        "recovered_after_sample_failure": "sample_failure" in recovery_types,
        "recovered_after_formal_failure": any(t.startswith("formal_") for t in recovery_types),
        "formal_recovery_to_ac": formal_recovery,
        "recovery_type": recovery_types,
        "debug_count": sum(p.get("role") == "DEBUG" for _, p in calls.values()),
        "successful_debug_count": successful_debug, "replan_count": replans,
        "sample_gate_reject_count": sum(p.get("passed") is False and p.get("status") in {
            "sample_wrong_answer", "sample_program_failure"} for _, p in samples),
        "sample_check_unverifiable_count": sum(p.get("status") == "sample_check_unverifiable" for _, p in samples),
        "invalid_model_output_count": invalid_outputs,
        "formal_submission_count": len(submissions), "custom_run_count": custom_runs,
        "candidate_version_count": len(candidates),
        "duplicate_candidate_count": duplicates,
        "formal_result_reuse_count": len(reuses),
        "final_formal_result_reused": accepted_reuse is not None,
        "candidate_versions": [payload for _, payload in candidates.values()],
        "recovery_evidence": evidence,
        "llm_checker_call_count": sum(p.get("purpose") == "sample_checker_generation" for _, p in calls.values()),
        "checker_custom_run_count": checker_runs,
        "contestant_custom_run_count": custom_runs - checker_runs,
        "llm_checker_reject_count": sum((p.get("generated_checker") or {}).get("decision") is False for _, p in samples)}
    # Additive v2 checker counters describe its path, never candidate failures.
    # Historical traces without these events keep unknown detail counts.
    v2 = state.get("checker_schema_version") in {"checker_state_v2", "checker_state_v3"} or bool(checker_generation)
    metrics.update({
        "llm_checker_generation_count": metrics["llm_checker_call_count"],
        "llm_checker_generation_failure_count": sum(p.get("generation_status") == "failed" for p in checker_generation) if v2 else None,
        "llm_checker_sanity_pass_count": sum(p.get("sanity_status") == "pass" for p in checker_sanity) if v2 else None,
        "llm_checker_sanity_failure_count": sum(p.get("sanity_status") == "failed" for p in checker_sanity) if v2 else None,
        "llm_checker_candidate_pass_count": sum(p.get("stage") == "candidate" and p.get("checker_decision") == "pass_unverified" for p in checker_decisions) if v2 else None,
        "llm_checker_candidate_reject_count": sum(p.get("stage") == "candidate" and p.get("checker_decision") == "rejected_unverified" for p in checker_decisions) if v2 else None,
        "llm_checker_execution_failure_count": sum(p.get("execution_status") != "success" and p.get("failure_status") is not None for p in checker_execution) if v2 else None,
        "unverified_checker_stop_count": len(checker_stops) if v2 else None})
    v3_samples = [(index, p) for index, p in samples if p.get("sample_policy") in {"semantic_check", "execution_only"}]
    # code-only never samples, so its four counts are known zero in every version.
    v3 = state.get("checker_schema_version") == "checker_state_v3" or bool(v3_samples) or state.get("mode") == "code-only"
    output_unverifiable = [(index, p) for index, p in v3_samples
        if p.get("status") == "execution_pass_output_unverifiable" and p.get("passed") is None
        and p.get("semantic_verification") == "unavailable"]
    metrics.update({
        "sample_semantic_verified_count": sum(p.get("semantic_verification") in {"client_verified", "remote_verified"}
            and p.get("status") in {"sample_pass", "sample_wrong_answer"} and isinstance(p.get("passed"), bool)
            for _, p in v3_samples) if v3 else None,
        "sample_output_unverifiable_count": len(output_unverifiable) if v3 else None,
        "sample_execution_failure_count": sum(p.get("status") == "sample_execution_failure"
            and p.get("passed") is False and p.get("verdict") in EXECUTION_FAILURES for _, p in v3_samples) if v3 else None,
        "formal_submit_after_unverifiable_sample_count": sum(any(sample_index < submission_index
            and all(sample.get(key) == submission.get(key) for key in ("solution_version", "code_sha256", "model_call_id"))
            for sample_index, sample in output_unverifiable) for submission_index, submission in submissions.values()) if v3 else None,
        "sample_execution_recovery": "sample_execution_failure" in recovery_types if v3 else None})
    if invalid_lines or issues:
        for name in ("first_try_ac", "first_formal_submission_ac", "first_candidate_sample_pass",
                     "recovered_to_ac", "recovered_after_sample_failure", "recovered_after_formal_failure",
                     "formal_recovery_to_ac", "sample_execution_recovery"):
            metrics[name] = None
        metrics["successful_debug_count"] = None
        metrics["recovery_type"] = []
        metrics["recovery_evidence"] = []
    return metrics


def workspace_metrics(workspace, state):
    events, invalid = trace_events(workspace.read_text("events.jsonl"))
    return recovery_metrics(events, state, invalid_lines=invalid)
