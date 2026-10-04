"""Fixed, journaled single-task state machine. All execution stays on MiniOJ."""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

import yaml

from agent.models.runtime import ModelCallFailed
from agent.models.types import AgentRole, ChatMessage
from agent.oj_client.client import OJClientError, OJProtocolError
from agent.oj_client.types import ClientErrorKind, SubmissionStatus
from agent.workspace.task import TaskWorkspace

from .agent import AgentResult, AgentTerminalStatus, extract_cpp, problem_markdown
from .budget import MODEL_BUDGET_ACCOUNTING, BudgetStopped, reserve_model_cost, validate_usage
from .budget_diagnostics import prompt_breakdown
from .checker import (SAMPLE_POLICY, GENERATED_SAMPLE_POLICY, EXECUTION_FAILURES, CheckerSpec,
                      SampleGatePolicy, SampleCheck, check_run, check_execution, checker_dimensions, sample_mode)
from .generated_checker import legacy_checker_messages, checker_stdin, generated_decision
from .formal_dedup import FORMAL_DEDUP_POLICY, cached_observation, evaluation_identity, stable_hash


@dataclass(frozen=True)
class HarnessPolicy:
    max_submissions: int = 10
    max_cost_cny: float = 1.0
    debug_before_replan: int = 3
    # A finite backstop for zero-priced routes and never-passing public samples.
    max_llm_calls: int = 80
    max_wall_clock_seconds: Optional[float] = None
    # Additional format-correction calls per CODE/DEBUG candidate.
    max_code_extraction_retries: int = 3

    def __post_init__(self):
        for name in ("max_submissions", "debug_before_replan", "max_llm_calls"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_submissions > 100:
            raise ValueError("max_submissions cannot exceed the authorized 100")
        if self.debug_before_replan != 3:
            raise ValueError("The current policy replans after 3 failed DEBUG candidates")
        if (isinstance(self.max_code_extraction_retries, bool)
                or not isinstance(self.max_code_extraction_retries, int)
                or self.max_code_extraction_retries < 0):
            raise ValueError("max_code_extraction_retries must be a non-negative integer")
        try:
            valid_cost = (not isinstance(self.max_cost_cny, bool)
                          and isinstance(self.max_cost_cny, (int, float))
                          and math.isfinite(self.max_cost_cny) and self.max_cost_cny > 0)
        except OverflowError:
            valid_cost = False
        if not valid_cost:
            raise ValueError("max_cost_cny must be finite and positive (default: 1 CNY)")
        if self.max_wall_clock_seconds is not None and (
            isinstance(self.max_wall_clock_seconds, bool)
            or not math.isfinite(self.max_wall_clock_seconds)
            or self.max_wall_clock_seconds <= 0
        ):
            raise ValueError("max_wall_clock_seconds must be finite and positive")

    @classmethod
    def from_yaml(cls, path: Optional[Path]):
        if path is None:
            return cls()
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError("Harness policy YAML is invalid") from exc
        if not isinstance(data, dict):
            raise ValueError("Harness policy must be a mapping")
        return cls(**data)


class HarnessStopped(BudgetStopped):
    pass


TRANSITIONS = {
    "FETCH_PROBLEM": {"PLAN", "DONE"},
    "PLAN": {"CODE", "DONE"},
    "CODE": {"TEST", "DONE"},
    "TEST": {"DEBUG", "PLAN", "REVIEW", "DONE"},
    "DEBUG": {"CODE", "DONE"},
    "REVIEW": {"DONE"},
    "DONE": set(),
}


class HarnessLoop:
    def __init__(self, agent, policy: HarnessPolicy):
        self.agent = agent
        self.workspace = agent.workspace
        self.state = self.workspace.state
        self.policy = policy
        self.base_seconds = self.state.wall_clock_seconds
        self.started = time.monotonic()
        self.agent.started = self.started - self.base_seconds
        self.checkpoint: Dict[str, Any] = {}
        self.formal_dedup_enabled = True
        self.budget_settlement_enabled = True

    def _snapshot_config(self):
        routes = {}
        for role, profile in self.agent.policy.mapping.items():
            if role is AgentRole.TEST_GENERATION:
                continue
            routes[role.value] = {
                "profile": profile.value,
                **asdict(self.agent.router.route(profile)),
            }
        submit_handler = self.agent.tools.handlers.get("submit_solution")
        client = getattr(submit_handler, "__self__", None)
        base_url = getattr(client, "base_url", "test-double")
        config = {
            "policy": asdict(self.policy),
            "routes": routes,
            "model_escalation": False,
            "review": "local_read_only",
            "sample_comparison": "whitespace_tokens",
            "oj_endpoint_sha256": hashlib.sha256(base_url.encode("utf-8")).hexdigest(),
            "custom_run_code_alias": getattr(client, "send_custom_run_code_alias", False),
        }
        # Historical checkpoints keep their original fail-fast policy and shape.
        if (self.checkpoint and "max_code_extraction_retries"
                not in self.checkpoint["config"]["policy"]):
            config["policy"].pop("max_code_extraction_retries")
        if self.state.feedback_policy is not None:
            config["feedback_policy"] = self.state.feedback_policy
        if getattr(client, "contest_id", None) is not None:
            config["contest_id"] = client.contest_id
        if self.agent.sample_checking is not None:
            config["sample_comparison"] = self.agent.sample_checking.version
            config["sample_checking"] = self.agent.sample_checking.as_dict()
        if self.formal_dedup_enabled:
            config["formal_submission_dedup"] = dict(FORMAL_DEDUP_POLICY)
        if self.budget_settlement_enabled:
            config["model_budget_accounting"] = MODEL_BUDGET_ACCOUNTING
        return config

    def _resolve_checker(self):
        policy = self.agent.sample_checking
        problem = self.checkpoint["problem"]
        if policy is None:
            spec = CheckerSpec("token", source="legacy_whitespace_tokens_v1")
        elif self.state.problem_id in policy.overrides:
            spec = CheckerSpec.from_dict(policy.overrides[self.state.problem_id])
        elif "checker" in problem:
            spec = CheckerSpec.from_metadata(problem["checker"])
        elif "get_checker_metadata" in self.agent.tools.handlers:
            try:
                metadata = self.agent.tools.call("get_checker_metadata", problem_id=self.state.problem_id)
                spec = CheckerSpec.from_metadata(metadata.get("checker"), source=metadata["source"])
            except OJClientError:
                spec = CheckerSpec(source="checker_metadata_unavailable")
        else:
            spec = CheckerSpec()
        self.checkpoint["sample_checker"] = self.state.sample_checker = asdict(spec)
        dimensions = {}
        if policy is not None and policy.version in {GENERATED_SAMPLE_POLICY, SAMPLE_POLICY}:
            self.state.checker_schema_version = "checker_state_v3" if policy.version == SAMPLE_POLICY else "checker_state_v2"
            dimensions = checker_dimensions(spec, source="explicit_config" if self.state.problem_id in policy.overrides else "problem_metadata")
        self.workspace.trace.append("CHECKER_RESOLVED", {**asdict(spec), **dimensions})

    def _save(self):
        self.state.current_phase = self.checkpoint["phase"]
        self.state.wall_clock_seconds = round(
            self.base_seconds + time.monotonic() - self.started, 3
        )
        self.checkpoint["state"] = asdict(self.state)
        # Checkpoint is the authoritative atomic commit; State is its dashboard projection.
        self.workspace.write_json("checkpoint.json", self.checkpoint)
        self.workspace.save_state()

    def _transition(self, phase: str, reason: Optional[str] = None):
        previous = self.checkpoint["phase"]
        if phase not in TRANSITIONS[previous]:
            raise ValueError(f"Invalid harness transition: {previous} -> {phase}")
        self.checkpoint["phase"] = phase
        self.checkpoint["completed_model"] = None
        self.checkpoint["code_extraction_retries"] = 0
        self._save()
        self.workspace.trace.append(
            "STATE_CHANGE", {"from": previous, "to": phase, "reason": reason}
        )

    def _guard(self):
        self._save()
        maximum = self.policy.max_wall_clock_seconds
        if maximum is not None and self.state.wall_clock_seconds >= maximum:
            raise HarnessStopped("budget_exhausted", "wall_clock_limit")

    def _reserve_model(self, role, messages, *, components=None):
        self._guard()
        if self.state.llm_call_count >= self.policy.max_llm_calls:
            raise HarnessStopped("budget_exhausted", "llm_call_limit")
        # User disabled escalation, but retained the configured base Role mapping.
        profile = self.agent.policy.choose(role, allow_escalation=False)
        route = self.agent.router.route(profile)
        input_limit, output_limit, bound = reserve_model_cost(
            route, messages, self.state, self.policy.max_cost_cny, components=components,
            settle_actual=self.budget_settlement_enabled,
            audit=lambda data: self.workspace.trace.append("MODEL_BUDGET_CHECK",
                {"role": role.value, "profile": profile.value, **data}))
        if role is AgentRole.DEBUG:
            self.state.debug_iterations += 1
        self.checkpoint["pending_operation"] = {"kind": "model", "role": role.value}
        self._save()
        self.workspace.trace.append(
            "BUDGET_RESERVATION",
            {"role": role.value, "profile": profile.value, "reserved_cny": float(bound),
             "committed_cny": self.state.budget_committed_cny, "max_cost_cny": self.policy.max_cost_cny},
        )
        return profile, input_limit, output_limit

    def _generate(self, role):
        completed = self.checkpoint.get("completed_model")
        if completed is not None:
            if completed["role"] != role.value:
                raise ValueError("Cached model response role does not match checkpoint")
            return completed["content"]
        problem = self.checkpoint["problem"]
        code = self._code() if self.state.solution_version else ""
        messages = self.agent.context_builder.build(
            problem=problem,
            role=role,
            plan=self.checkpoint["plan"],
            current_solution=code,
            feedback=self.checkpoint["feedback"],
            recent_history=self.checkpoint["history"],
        )
        retries = self.checkpoint.get("code_extraction_retries", 0)
        if retries and role in {AgentRole.CODE, AgentRole.DEBUG}:
            messages[0] = ChatMessage("system", messages[0].content + (
                f"\nFormat correction retry {retries}: your previous response did not contain "
                "a complete C++20 program. Return only one fenced ```cpp code block containing "
                "the entire program, including headers and main. Do not return analysis, "
                "tool calls, XML commands, or requests to search files. No filesystem tools "
                "are available; implement the solution from the provided problem and context."
            ))
        components = prompt_breakdown(messages, role=role.value, plan=self.checkpoint["plan"],
            current_solution=code, feedback=self.checkpoint["feedback"], recent_history=self.checkpoint["history"])
        profile, input_limit, output_limit = self._reserve_model(role, messages, components=components)
        # Runtime increments the attempt before network. Checkpoint recovery reconciles it.
        response = self.agent.model_runtime.complete(role, profile, messages)
        self.workspace.write_json(
            f"artifacts/models/{self.state.last_model_call_id}.json",
            {"model_call_id": self.state.last_model_call_id, **asdict(response)},
        )
        self.checkpoint["pending_operation"] = None
        try:
            validate_usage(response, input_limit, output_limit)
        except BudgetStopped:
            self._save()
            raise
        self.checkpoint["completed_model"] = {"role": role.value, "content": response.content}
        self._save()
        return response.content

    def _retry_code_extraction(self, role):
        maximum = self.checkpoint["config"]["policy"].get("max_code_extraction_retries", 0)
        retries = self.checkpoint.get("code_extraction_retries", 0)
        if retries >= maximum:
            return False
        failed_call_id = self.state.last_model_call_id
        # Commit the counter and invalidate both response caches together. Resume
        # must neither replay the malformed response nor reset the retry allowance.
        self.checkpoint["code_extraction_retries"] = retries + 1
        self.checkpoint["generated_code"] = None
        self.checkpoint["completed_model"] = None
        self._save()
        self.workspace.trace.append("CODE_EXTRACTION_RETRY", {
            "role": role.value, "retry": retries + 1, "max_retries": maximum,
            "reason": "code_extraction_failed", "failed_model_call_id": failed_call_id,
        }, correlation_id=failed_call_id)
        return True

    def _code(self):
        version = self.state.solution_version
        code = self.workspace.read_text(f"artifacts/solutions/{version}.cpp")
        digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
        if digest != self.state.solution_sha256:
            raise ValueError("Saved candidate hash does not match checkpoint")
        return code

    def _record_candidate(self, code: str):
        number = self.state.attempt_count + 1
        version = f"solution-v{number}"
        digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
        self.workspace.write_text(f"artifacts/solutions/{version}.cpp", code)
        self.workspace.write_text("solution.cpp", code)
        self.state.attempt_count = number
        self.state.solution_version = version
        self.state.solution_sha256 = digest
        self.state.solution_model_call_id = self.state.last_model_call_id
        # A new candidate must never inherit the old code's verdict or solved flag.
        self.state.last_verdict = None
        self.state.last_outcome_kind = None
        self.state.solved = False
        self.state.sample_gate_status = None
        self.checkpoint["test_stage"] = "samples"
        self.checkpoint["sample_index"] = 0
        self.checkpoint["sample_results"] = []
        self.checkpoint["pending_sample_run"] = None
        self.checkpoint["active_formal_identity"] = None
        self.workspace.write_json("artifacts/code-version.json", self._links())
        self.workspace.trace.append("CODE_VERSION", self._links(),
                                    correlation_id=self.state.solution_model_call_id)

    def _links(self):
        return {"solution_version": self.state.solution_version,
                "code_sha256": self.state.solution_sha256,
                "model_call_id": self.state.solution_model_call_id}

    def _failure(self, feedback):
        self.checkpoint["feedback"] = feedback
        self.checkpoint["history"].append(
            f"{self.state.solution_version}: {feedback.get('verdict', 'failure')}"
        )
        self.checkpoint["history"] = self.checkpoint["history"][-5:]
        if self.checkpoint["candidate_from_debug"]:
            self.state.consecutive_debug_failures += 1
        if self.state.submission_attempt_count >= self.policy.max_submissions:
            raise HarnessStopped("budget_exhausted", "submission_limit")
        if self.state.consecutive_debug_failures >= self.policy.debug_before_replan:
            self.state.replan_count += 1
            self.state.consecutive_debug_failures = 0
            reason = "three_debug_candidates_failed"
            self._transition("PLAN", reason)
            self.workspace.trace.append("REPLAN", {"reason": reason, **self._links()})
        else:
            self._transition("DEBUG", self._failure_reason(feedback))

    def _failure_reason(self, feedback):
        if self.agent.sample_checking and self.agent.sample_checking.version == SAMPLE_POLICY:
            if feedback.get("source") == "public_sample":
                return feedback["failure_reason"]
            return "formal_verdict_" + feedback["verdict"]
        return "candidate_failed"

    def _samples(self):
        code = self._code()
        samples = self.checkpoint["problem"].get("samples", [])
        spec = CheckerSpec.from_dict(self.checkpoint.get("sample_checker") or {
            "kind": "token", "source": "legacy_whitespace_tokens_v1"})
        policy = self.agent.sample_checking
        v3 = policy is not None and policy.version == SAMPLE_POLICY
        mode = sample_mode(spec, policy)
        while self.checkpoint["sample_index"] < len(samples):
            self._guard()
            index = self.checkpoint["sample_index"]
            sample = samples[index]
            saved_run = self.checkpoint.get("pending_sample_run")
            if saved_run is None:
                self.state.custom_run_count += 1
                if v3:
                    self.checkpoint["pending_operation"] = {"kind": "sample_run", "sample_index": index + 1, **self._links()}
                self._save()
                result = self.agent.tools.call("run_code", code=code, stdin=sample["input"])
                self.checkpoint["pending_sample_run"] = result.as_dict()
                if v3:
                    self.checkpoint["pending_operation"] = None
                self._save()
            else:
                from agent.oj_client.types import CustomRunResult
                result = CustomRunResult.from_dict(saved_run)
            verdict = result.status
            if verdict == "IE":
                raise HarnessStopped("remote_infrastructure_failure", "sample_ie")
            allowed_statuses = {"OK", *EXECUTION_FAILURES} if mode == "execution_only" else {"OK", "AC", "WA", "CE", "RE", "TLE", "MLE", "OLE"}
            if verdict not in allowed_statuses:
                raise HarnessStopped("client_failure", "unknown_custom_run_status")
            if mode == "execution_only" and verdict == "OK" and (not isinstance(result.exit_code, int) or isinstance(result.exit_code, bool)):
                raise HarnessStopped("client_failure", "custom_run_exit_code_unknown")
            if mode != "execution_only" and verdict in {"OK", "AC"} and result.stdout is None:
                raise HarnessStopped("client_failure", "custom_run_stdout_missing")
            execution_failed = v3 and (verdict in EXECUTION_FAILURES or result.exit_code not in {None, 0})
            if execution_failed and verdict not in EXECUTION_FAILURES:
                checked = SampleCheck("sample_execution_failure", False, "sample_execution_failure", "RE")
            else:
                checked = check_execution(result) if mode == "execution_only" or execution_failed else check_run(spec, sample, result)
            generated = None
            if (checked.passed is None and checked.reason != "stdout_truncated"
                    and policy and not v3 and policy.llm_checker != "disabled"):
                generated = self._llm_check(sample, result.stdout, index)
                if generated["decision"] is True and self.agent.sample_checking.llm_checker == "submit_on_pass":
                    # Unverifiable remains unverifiable, but explicit policy may submit.
                    generated["allow_formal_submit"] = True
            payload = {"sample_index": index + 1, **asdict(checked),
                       "comparison": spec.kind, "checker": asdict(spec), **self._links(),
                       "result": asdict(result)}
            if generated is not None:
                payload["generated_checker"] = generated
            if policy and policy.version in {GENERATED_SAMPLE_POLICY, SAMPLE_POLICY}:
                payload.update(checker_dimensions(spec, source="explicit_config" if self.state.problem_id in self.agent.sample_checking.overrides else "problem_metadata",
                                                 generated=generated is not None))
                payload["sample_check_status"] = (generated or {}).get("sample_check_status", checked.status)
                if v3:
                    execution_failed = checked.status in {"sample_execution_failure", "sample_program_failure"}
                    payload.update(sample_policy=mode,
                        semantic_verification="unavailable" if mode == "execution_only" else "not_evaluated" if execution_failed else payload["checker_verification"],
                        execution_status="execution_failed" if execution_failed else "execution_pass",
                        formal_fallback="enabled" if mode == "execution_only" else "disabled",
                        allow_formal_submit=checked.status == "execution_pass_output_unverifiable")
                    if payload["allow_formal_submit"]:
                        payload["sample_check_status"] = "output_unverifiable"
                self.state.sample_check_status = payload["sample_check_status"]
            self.workspace.write_json(
                f"artifacts/samples/{self.state.solution_version}-{index + 1}.json", payload
            )
            self.workspace.trace.append("SAMPLE_RESULT", payload)
            self.checkpoint["sample_results"].append(payload)
            self.checkpoint["sample_index"] = index + 1
            self.checkpoint["pending_sample_run"] = None
            if checked.passed is False:
                if checked.status != "sample_execution_failure":
                    self.state.sample_gate_reject_count += 1
                self.state.sample_gate_status = checked.status
                # Persist a failure stage, never a completed sample stage. Recovery
                # must process this feedback before it can advance to submission.
                self.checkpoint["test_stage"] = "sample_failure"
                self.checkpoint["sample_feedback"] = {
                    "verdict": checked.verdict,
                    "source": "public_sample", "sample_index": index + 1,
                    "input": sample["input"], "expected": sample["output"],
                    "actual": result.stdout, "stderr": result.stderr,
                    "exit_code": result.exit_code,
                }
                if v3:
                    self.checkpoint["sample_feedback"]["failure_reason"] = (
                        "sample_execution_failure" if payload["execution_status"] == "execution_failed" else checked.status)
                self._save()
                self._sample_failure()
                return
            if checked.passed is None:
                if not v3 or not payload["allow_formal_submit"]:
                    self.state.sample_check_unverifiable_count += 1
                self.state.sample_gate_status = "output_unverifiable" if v3 and payload["allow_formal_submit"] else "sample_check_unverifiable"
                # Persist the stopped stage before terminating or allowing submit.
                self.checkpoint["test_stage"] = "sample_unverifiable"
                self._save()
                legacy_allow = not v3 and ((generated or {}).get("allow_formal_submit") or
                    policy is not None and policy.on_unverifiable == "submit")
                if not self._execution_fallback(payload) and not legacy_allow:
                    self._stop_unverified(generated, checked.reason)
                self.checkpoint["test_stage"] = "samples"
            self._save()
        if self.state.sample_gate_status is None:
            self.state.sample_gate_status = "sample_pass" if samples else "no_public_samples"
        self.checkpoint["test_stage"] = "submit"
        self._save()

    def _checker_run(self, code, stdin):
        self._guard()
        self.state.custom_run_count += 1
        self.workspace.trace.append("CHECKER_RUN", {**self._links(),
            "checker_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
            "checker_model_call_id": self.checkpoint["generated_checker"]["model_call_id"]})
        self.checkpoint["pending_operation"] = {"kind": "checker_run"}
        self._save()
        result = self.agent.tools.call("run_code", code=code, stdin=stdin)
        self.checkpoint["pending_operation"] = None
        self._save()
        if result.status == "IE":
            raise HarnessStopped("remote_infrastructure_failure", "checker_run_ie")
        if result.status not in {"OK", "CE", "RE", "TLE", "MLE", "OLE"}:
            raise HarnessStopped("client_failure", "unknown_checker_run_status")
        return result

    def _llm_check(self, sample, stdout, index):
        if self.agent.sample_checking.version == GENERATED_SAMPLE_POLICY:
            from .checker_session import GeneratedCheckerSession
            return GeneratedCheckerSession(self).check(sample, stdout, index)
        saved = self.checkpoint.get("generated_checker")
        if saved is None:
            messages = legacy_checker_messages(self.agent.context_builder, self.checkpoint["problem"])
            profile, input_limit, output_limit = self._reserve_model(AgentRole.CODE, messages)
            response = self.agent.model_runtime.complete(AgentRole.CODE, profile, messages,
                                                         purpose="sample_checker_generation")
            call_id = self.state.last_model_call_id
            self.workspace.write_json(f"artifacts/models/{call_id}.json", {"model_call_id": call_id,
                "purpose": "sample_checker_generation", **asdict(response)})
            validate_usage(response, input_limit, output_limit)
            self.checkpoint["pending_operation"] = None
            try:
                source = extract_cpp(response.content)
            except ValueError:
                saved = {"status": "invalid_checker_output", "model_call_id": call_id}
            else:
                digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
                path = "artifacts/checkers/checker-v1.cpp"
                self.workspace.write_text(path, source)
                saved = {"status": "llm_generated_unverified", "path": path,
                         "code_sha256": digest, "model_call_id": call_id, "sanity": {}}
            self.checkpoint["generated_checker"] = saved
            self._save()
            self.workspace.trace.append("GENERATED_CHECKER", saved)
        if saved["status"] == "invalid_checker_output":
            return {**saved, "decision": None}
        source = self.workspace.read_text(saved["path"])
        if hashlib.sha256(source.encode("utf-8")).hexdigest() != saved["code_sha256"]:
            raise ValueError("Saved checker hash does not match checkpoint")
        sanity_key = str(index)
        if sanity_key not in saved["sanity"]:
            sanity_run = self._checker_run(source, checker_stdin(sample["input"], sample["output"]))
            saved["sanity"][sanity_key] = {"decision": generated_decision(sanity_run), "result": sanity_run.as_dict()}
            self._save()
        sanity = saved["sanity"][sanity_key]
        if sanity["decision"] is not True:
            return {"status": "llm_generated_unverified", "model_call_id": saved["model_call_id"],
                    "code_sha256": saved["code_sha256"], "sanity_passed": False, "decision": None}
        run = self._checker_run(source, checker_stdin(sample["input"], stdout))
        observation = {"status": "llm_generated_unverified", "model_call_id": saved["model_call_id"],
            "code_sha256": saved["code_sha256"], "sanity_passed": True,
            "decision": generated_decision(run), "result": run.as_dict()}
        record = {**self._links(), "sample_index": index + 1, "generated_checker": observation}
        self.workspace.write_json(f"artifacts/checkers/{self.state.solution_version}-{index + 1}.json", record)
        self.workspace.trace.append("GENERATED_CHECKER_RESULT", record)
        return observation

    def _sample_failure(self):
        self._failure(self.checkpoint["sample_feedback"])

    def _stop_unverified(self, generated, reason):
        status = (generated or {}).get("failure_status")
        if status and self.agent.sample_checking.version == GENERATED_SAMPLE_POLICY:
            self.workspace.trace.append("UNVERIFIED_CHECKER_STOP", {**generated, "terminal_status": status})
            raise HarnessStopped(status, (generated or {}).get("reason") or status)
        raise HarnessStopped("sample_check_unverifiable", reason)

    def _sample_unverifiable(self):
        generated = self.checkpoint["sample_results"][-1].get("generated_checker") or {}
        policy = self.agent.sample_checking
        legacy_allow = (policy is None or policy.version != SAMPLE_POLICY) and (generated.get("allow_formal_submit") or
                policy is not None and policy.on_unverifiable == "submit")
        if self._execution_fallback(self.checkpoint["sample_results"][-1]) or legacy_allow:
            self.checkpoint["test_stage"] = "samples"
            self._save()
        else:
            self._stop_unverified(generated, self.checkpoint["sample_results"][-1]["reason"])

    def _execution_fallback(self, payload):
        """Resume only a committed, known execution pass under the frozen v3 policy."""
        from agent.oj_client.types import CustomRunResult
        policy = self.agent.sample_checking
        if not policy or policy.version != SAMPLE_POLICY:
            return False
        spec = CheckerSpec.from_dict(self.checkpoint["sample_checker"])
        return (sample_mode(spec, policy) == "execution_only"
            and payload.get("status") == "execution_pass_output_unverifiable"
            and payload.get("passed") is None
            and check_execution(CustomRunResult.from_dict(payload["result"])).status == "execution_pass_output_unverifiable")

    def _formal_identity(self, code):
        identity = evaluation_identity(self.state.task_id, self.state.problem_id,
            self.checkpoint["problem"], code)
        frozen = self.checkpoint.setdefault("formal_problem_sha256", identity["frozen_problem_sha256"])
        if frozen != identity["frozen_problem_sha256"]:
            raise ValueError("Frozen formal problem identity changed")
        return identity, stable_hash(identity)

    def _formal_entry(self):
        return self.checkpoint.get("formal_evaluations", {}).get(
            self.checkpoint.get("active_formal_identity")) if self.formal_dedup_enabled else None

    def _bind_formal_submission(self, entry):
        self.state.last_submission_id = entry["submission_id"]
        self.state.last_submission_solution_version = entry["submission_links"]["solution_version"]
        self.state.last_submission_code_sha256 = entry["identity"]["source_sha256"]

    def _reuse_formal_result(self, entry, identity):
        final, feedback = cached_observation(entry, identity, self.state.feedback_policy)
        if final.verdict not in {"AC", "IE"} and feedback is None:
            raise HarnessStopped("result_unknown", "cached_formal_feedback_missing")
        self._bind_formal_submission(entry)
        self.state.last_verdict = final.verdict
        self.state.last_outcome_kind = final.outcome_kind.value
        version = self.state.solution_version
        reuses = self.checkpoint.setdefault("formal_result_reuses", {})
        duplicate = version != entry["submission_links"]["solution_version"]
        previous = reuses.get(version)
        if previous is not None and (previous.get("evaluation_identity") != identity
                or previous.get("source_submission_id") != final.submission_id):
            raise ValueError("Cached formal reuse association mismatch")
        if version not in reuses:
            self.state.duplicate_candidate_count += int(duplicate)
        # Persist only the permitted view when reading an older/richer local cache.
        entry["final"] = final.as_dict()
        entry["feedback"] = feedback.as_dict() if feedback else None
        # Rebuild the visible receipt from the newly projected view even when a
        # previous receipt survived a crash; never copy richer disk observations.
        reuses[version] = {"candidate_duplicate": duplicate, "formal_submission_reused": True,
            "observation_source": "cached_formal_result", "source_sha256": identity["source_sha256"],
            "evaluation_identity": identity, "source_submission_id": final.submission_id,
            "source_solution_version": entry["submission_links"]["solution_version"],
            "source_model_call_id": entry["submission_links"].get("model_call_id"),
            **self._links(), "observation": {"final": final.as_dict(),
                "feedback": feedback.as_dict() if feedback else None}}
        payload = reuses[version]
        self._save()  # Counter and reuse decision survive interruption before Trace.
        # Checkpoint is authoritative. Reconcile an append completed just before
        # a crash without duplicating the same iteration's reuse event/counter.
        from .metrics import trace_events
        events, _ = trace_events(self.workspace.read_text("events.jsonl"))
        if not any(event["type"] == "FORMAL_RESULT_REUSED"
                   and event["payload"].get("solution_version") == version for event in events):
            self.workspace.trace.append("FORMAL_RESULT_REUSED", payload,
                correlation_id=self.state.solution_model_call_id)
        self.workspace.write_json(f"artifacts/formal-reuses/{version}.json", payload)
        if final.verdict == "AC":
            self.checkpoint["review_reason"] = "cached_judge_accepted"
            self._transition("REVIEW")
        elif final.verdict == "IE":
            raise HarnessStopped("remote_infrastructure_failure", "judge_ie")
        else:
            self._failure(feedback.as_dict())

    def _submit(self):
        self._guard()
        code = self._code()
        entry = None
        if self.formal_dedup_enabled:
            identity, key = self._formal_identity(code)
            entries = self.checkpoint.setdefault("formal_evaluations", {})
            entry = entries.get(key)
            self.checkpoint["active_formal_identity"] = key
            if entry is not None:
                if entry.get("identity") != identity or entry.get("feedback_policy") != self.state.feedback_policy:
                    raise ValueError("Cached formal evaluation identity/policy mismatch")
                if entry.get("final") is not None:
                    return self._reuse_formal_result(entry, identity)
                if entry.get("submission_id"):
                    if entry["submission_links"]["solution_version"] != self.state.solution_version:
                        raise HarnessStopped("result_unknown", "cached_formal_result_pending")
                    self._bind_formal_submission(entry)
                    self.checkpoint["test_stage"] = "wait"
                    self._save()
                    return
                raise HarnessStopped("result_unknown", "previous_formal_submission_unknown_not_reissued")
        if self.state.submission_attempt_count >= self.policy.max_submissions:
            raise HarnessStopped("budget_exhausted", "submission_limit")
        if self.formal_dedup_enabled:
            entry = {"identity": identity, "submission_links": self._links(),
                "feedback_policy": self.state.feedback_policy, "submission_id": None,
                "created": None, "final": None, "feedback": None}
            entries[key] = entry
        self.state.submission_attempt_count += 1
        self.checkpoint["pending_operation"] = {"kind": "submission", **self._links()}
        self._save()
        created = self.agent.tools.call(
            "submit_solution", problem_id=self.state.problem_id, code=code
        )
        self.state.submission_count += 1
        self.state.last_submission_id = created.submission_id
        self.state.last_submission_solution_version = self.state.solution_version
        self.state.last_submission_code_sha256 = self.state.solution_sha256
        if entry is not None:
            entry.update(submission_id=created.submission_id, created=created.as_dict())
        self.checkpoint["pending_operation"] = None
        self.checkpoint["test_stage"] = "wait"
        self._save()  # Persist known ID before any polling or optional artifacts.
        payload = {**created.as_dict(), **self._links()}
        key = quote(created.submission_id, safe="")
        self.workspace.write_json(f"artifacts/submissions/{key}-created.json", payload)
        self.workspace.write_json("artifacts/submission-created.json", payload)
        self.workspace.trace.append("SUBMISSION", payload, correlation_id=created.submission_id)

    def _wait(self):
        self._guard()
        submission_id = self.state.last_submission_id
        final = self.agent.tools.call(
            "wait_for_submission", submission_id=submission_id,
            timeout_seconds=self.agent.judge_timeout_seconds,
            poll_interval_seconds=self.agent.poll_seconds,
        )
        if final.submission_id != submission_id or final.known_status is not SubmissionStatus.FINISHED:
            raise OJProtocolError("Final submission identity/state mismatch",
                                  kind=ClientErrorKind.PROTOCOL, method="GET", path="submission")
        if final.outcome_kind is None:
            raise OJProtocolError("Final verdict is unrecognized",
                                  kind=ClientErrorKind.PROTOCOL, method="GET", path="submission")
        self.state.last_verdict = final.verdict
        self.state.last_outcome_kind = final.outcome_kind.value
        entry = self._formal_entry()
        if entry is not None:
            entry["final"] = final.as_dict()
        payload = {**final.as_dict(), "outcome_kind": final.outcome_kind.value, **self._links()}
        key = quote(submission_id, safe="")
        self.workspace.write_json(f"artifacts/submissions/{key}-final.json", payload)
        self.workspace.write_json("artifacts/submission-final.json", payload)
        self.workspace.trace.append("JUDGE_RESULT", payload, correlation_id=submission_id)
        if final.verdict == "AC":
            self.checkpoint["review_reason"] = "judge_accepted"
            self._transition("REVIEW")
        elif final.verdict == "IE":
            raise HarnessStopped("remote_infrastructure_failure", "judge_ie")
        else:
            self.checkpoint["test_stage"] = "feedback"
            self._save()

    def _feedback(self):
        feedback = self.agent.tools.call("get_feedback", submission_id=self.state.last_submission_id)
        if feedback.verdict != self.state.last_verdict:
            raise OJProtocolError("Feedback verdict does not match final submission",
                                  kind=ClientErrorKind.PROTOCOL, method="GET", path="feedback")
        entry = self._formal_entry()
        if entry is not None:
            entry["feedback"] = feedback.as_dict()
        payload = {**feedback.as_dict(), "submission_id": self.state.last_submission_id,
                   **self._links()}
        key = quote(self.state.last_submission_id, safe="")
        self.workspace.write_json(f"artifacts/feedback/{key}.json", payload)
        self.workspace.write_json("artifacts/feedback.json", payload)
        self._failure(feedback.as_dict())

    def _review(self):
        self._code()  # Verify the immutable candidate and the judged hash.
        matched = (self.state.solution_sha256 == self.state.last_submission_code_sha256
                   and self.state.solution_version == self.state.last_submission_solution_version)
        reused = self.checkpoint.get("formal_result_reuses", {}).get(self.state.solution_version)
        if reused is not None:
            identity, _ = self._formal_identity(self._code())
            final, _ = cached_observation(self._formal_entry(), identity, self.state.feedback_policy)
            matched = (final.submission_id == self.state.last_submission_id
                and self.state.solution_sha256 == self.state.last_submission_code_sha256
                and reused["evaluation_identity"] == identity)
        accepted = self.state.last_verdict == "AC" and matched
        self.workspace.write_text(
            "artifacts/review.md",
            f"# Read-only review\n\nVerdict: {self.state.last_verdict}\n"
            f"Submission: {self.state.last_submission_id}\n"
            f"Candidate: {self.state.solution_version}\nHash matches judged code: {matched}\n"
            f"Submissions: {self.state.submission_attempt_count}/{self.policy.max_submissions}\n"
            f"Reserved cost ceiling: {self.state.budget_committed_cny} CNY\n"
            "No source changes were made during review.\n",
        )
        if not accepted:
            raise HarnessStopped("internal_failure", "review_judged_code_mismatch")
        self.workspace.trace.append("REVIEW_RESULT", {"matched": matched,
            "submission_id": self.state.last_submission_id, **self._links(),
            **({"formal_submission_reused": True, "source_solution_version": reused["source_solution_version"]}
               if reused is not None else {})})
        return self._finish("accepted", "judge_accepted")

    def _finish(self, status, reason, error_kind=None):
        self.checkpoint["phase"] = "DONE"
        result = self.agent._finish(AgentTerminalStatus(status), reason, error_kind=error_kind)
        self._save()
        # Include full metrics and associations in the stable result artifact.
        self.workspace.write_json("artifacts/result.json", {
            **asdict(result), "verdict": result.final_verdict,
            "submission_attempts": self.state.submission_attempt_count,
            "budget_committed_cny": self.state.budget_committed_cny,
            "cost_estimate_status": self.state.cost_estimate_status,
            "model_call_id": self.state.solution_model_call_id,
            "replan_count": self.state.replan_count,
            "custom_run_count": self.state.custom_run_count,
        })
        return result

    def _prepare(self, resume):
        if resume:
            # CLI may have loaded the workspace before another runner committed.
            # Rehydrate the authoritative State only after acquiring the run lock.
            fresh = TaskWorkspace.load(self.workspace.root.parent, self.state.task_id)
            self.state = self.workspace.state = fresh.state
            self.base_seconds = self.state.wall_clock_seconds
            self.started = time.monotonic()
            self.agent.started = self.started - self.base_seconds
            self.checkpoint = self.workspace.read_json("checkpoint.json")
            self.formal_dedup_enabled = "formal_submission_dedup" in self.checkpoint["config"]
            self.budget_settlement_enabled = "model_budget_accounting" in self.checkpoint["config"]
            # Old checkpoints retain their original comparison, without an upgrade.
            if "sample_checking" not in self.checkpoint["config"]:
                if self.agent.sample_checking is not None and self.agent.sample_checking_explicit:
                    raise ValueError("Resume configuration differs from the saved sample policy")
                self.agent.sample_checking = None
            elif (not self.agent.sample_checking_explicit and self.agent.sample_checking is not None
                    and self.agent.sample_checking.as_dict() == SampleGatePolicy().as_dict()
                    and self.checkpoint["config"]["sample_checking"]["version"] != SAMPLE_POLICY):
                # Default policy changes do not upgrade an existing checkpoint.
                self.agent.sample_checking = SampleGatePolicy.from_dict(self.checkpoint["config"]["sample_checking"])
            if self.checkpoint["config"] != self._snapshot_config():
                raise ValueError("Resume configuration differs from the saved policy/routes")
            if self.checkpoint["phase"] == "DONE":
                if (self.state.terminal_status == "result_unknown"
                        and self.checkpoint["test_stage"] == "wait"
                        and self.checkpoint.get("pending_operation") is None
                        and self.state.last_submission_id):
                    self.state.terminal_status = None
                    self.state.termination_reason = None
                    self.state.error_kind = None
                    self.checkpoint["phase"] = "TEST"
                    self.checkpoint["test_stage"] = "wait"
                else:
                    return False
            pending = self.checkpoint.get("pending_operation")
            if pending:
                kind = pending["kind"]
                # A request may have succeeded between the remote action and local commit.
                # Never reissue a paid call or formal POST across that uncertainty window.
                if kind == "model":
                    saved = self.workspace.read_json("state.json")
                    if saved.get("llm_call_count", 0) > self.state.llm_call_count:
                        for key in ("llm_call_count", "llm_success_count", "llm_failure_count",
                                    "last_model_call_id", "calls_per_model_profile",
                                    "input_tokens", "output_tokens", "estimated_cost",
                                    "cost_estimate_status", "cost_currency", "llm_usage_missing_count",
                                    "budget_committed_cny", "pending_model_reservation"):
                            setattr(self.state, key, saved.get(key, getattr(self.state, key)))
                if (self.agent.sample_checking and self.agent.sample_checking.version == GENERATED_SAMPLE_POLICY
                        and (pending.get("purpose") == "sample_checker_generation" or kind == "checker_run")):
                    from .checker_session import GeneratedCheckerSession
                    index = self.checkpoint["sample_index"]
                    generated = GeneratedCheckerSession(self).check(self.checkpoint["problem"]["samples"][index],
                        (self.checkpoint.get("pending_sample_run") or {}).get("stdout", ""), index)
                    self._stop_unverified(generated, "checker_action_not_reissued")
                raise HarnessStopped("result_unknown", f"interrupted_{kind}_not_reissued")
            self.state.resume_count += 1
            self.workspace.trace.append("TASK_RESUMED", {"phase": self.checkpoint["phase"]})
            self._save()
        else:
            if (self.workspace.root / "checkpoint.json").exists():
                raise ValueError("Checkpoint already exists; use resume")
            profiles = {self.agent.policy.mapping[role].value
                        for role in (AgentRole.PLAN, AgentRole.CODE, AgentRole.DEBUG)}
            self.state.experiment_variant = (
                f"fixed-{next(iter(profiles))}-no-escalation" if len(profiles) == 1
                else "mixed-fixed-no-escalation"
            )
            self.checkpoint = {
                "schema_version": "phase4-v1", "phase": "FETCH_PROBLEM",
                "config": self._snapshot_config(), "problem": None, "plan": "",
                "feedback": None, "history": [], "candidate_from_debug": False,
                "generated_code": None, "pending_operation": None,
                "completed_model": None,
                "test_stage": "samples", "sample_index": 0, "sample_results": [],
            }
            self.workspace.write_json("task.json", {
                "task_id": self.state.task_id, "problem_id": self.state.problem_id,
                "mode": "harness-loop", "config": self.checkpoint["config"],
            })
            self._save()
        return True

    def run(self, *, resume=False):
        with self.workspace.exclusive_run():
            try:
                if not self._prepare(resume):
                    saved = self.workspace.read_json("artifacts/result.json")
                    fields = AgentResult.__dataclass_fields__
                    return AgentResult(**{key: saved[key] for key in fields if key in saved})
                while True:
                    self._guard()
                    phase = self.checkpoint["phase"]
                    if phase == "FETCH_PROBLEM":
                        problem = self.agent.tools.call("get_problem", problem_id=self.state.problem_id)
                        if problem.problem_id != self.state.problem_id:
                            raise ValueError("Fetched problem identity mismatch")
                        self.checkpoint["problem"] = problem.as_dict()
                        if self.formal_dedup_enabled:
                            self.checkpoint["formal_problem_sha256"] = stable_hash(problem.as_dict())
                        self.state.public_sample_count = len(problem.samples)
                        self._resolve_checker()
                        self.workspace.write_json("problem.json", problem.as_dict())
                        self.workspace.write_text("problem.md", problem_markdown(problem))
                        self._transition("PLAN")
                    elif phase == "PLAN":
                        self.checkpoint["plan"] = self._generate(AgentRole.PLAN)
                        self.workspace.write_text("artifacts/plan.md", self.checkpoint["plan"])
                        self.workspace.write_text(
                            f"artifacts/plans/plan-{self.state.replan_count + 1}.md",
                            self.checkpoint["plan"],
                        )
                        self.checkpoint["candidate_from_debug"] = False
                        self._transition("CODE")
                    elif phase == "CODE":
                        role = AgentRole.DEBUG if self.checkpoint["candidate_from_debug"] else AgentRole.CODE
                        generated = self.checkpoint.get("generated_code")
                        if generated is None:
                            generated = self._generate(role)
                        try:
                            code = extract_cpp(generated)
                        except ValueError:
                            if self._retry_code_extraction(role):
                                continue
                            raise
                        self._record_candidate(code)
                        self.checkpoint["generated_code"] = None
                        self._transition("TEST")
                    elif phase == "DEBUG":
                        self.checkpoint["generated_code"] = self._generate(AgentRole.DEBUG)
                        self.checkpoint["candidate_from_debug"] = True
                        self._transition("CODE", "debug_generated_candidate")
                    elif phase == "TEST":
                        {"samples": self._samples, "sample_failure": self._sample_failure,
                         "sample_unverifiable": self._sample_unverifiable,
                         "submit": self._submit,
                         "wait": self._wait, "feedback": self._feedback}[self.checkpoint["test_stage"]]()
                    elif phase == "REVIEW":
                        return self._review()
                    else:
                        raise ValueError("Unrecognized checkpoint phase")
            except BudgetStopped as exc:
                return self._finish(exc.status, exc.reason, exc.status)
            except ModelCallFailed as exc:
                error = exc.response.error
                return self._finish("model_failure", f"model_{error.kind.value}", error.kind.value)
            except OJClientError as exc:
                status = "result_unknown" if exc.kind is ClientErrorKind.RESULT_UNKNOWN else "client_failure"
                return self._finish(status, f"oj_{exc.kind.value}", exc.kind.value)
            except KeyboardInterrupt:
                self._save()
                self.workspace.trace.append("TASK_INTERRUPTED", {"phase": self.checkpoint["phase"]})
                raise
            except ValueError as exc:
                if "Resume configuration" in str(exc) or "Checkpoint already" in str(exc):
                    raise
                if "complete C++20" in str(exc):
                    return self._finish("invalid_model_output", "code_extraction_failed", "invalid_model_output")
                return self._finish("internal_failure", "checkpoint_or_candidate_invalid", "ValueError")
            except Exception as exc:
                return self._finish("internal_failure", "unexpected_runtime_failure", type(exc).__name__)
