"""Task-local untrusted checker journal, shared by TEST and bounded smoke.

The host supplies the existing budget/model/tool runtimes. Generated source is
only read as an artifact and passed to MiniOJ; this module never executes it.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from agent.models.runtime import ModelCallFailed
from agent.models.types import AgentRole
from agent.oj_client.client import OJClientError

from .agent import extract_cpp
from .budget import BudgetStopped, validate_usage
from .checker import CheckerSpec, checker_dimensions
from .formal_dedup import stable_hash
from .generated_checker import (CHECKER_CONTRACT_VERSION, CHECKER_PROMPT_VERSION,
                                checker_messages, checker_stdin)


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def execution_observation(result):
    if result.status != "OK" or result.exit_code not in {None, 0}:
        return {"execution_status": "compile_failed" if result.status == "CE" else "execution_failed",
                "failure_status": "checker_execution_failed", "reason": "checker_" + result.status.lower(),
                "decision": None}
    if result.stdout is None or result.stdout_truncated:
        return {"execution_status": "protocol_error", "failure_status": "checker_result_unknown",
                "reason": "checker_stdout_incomplete", "decision": None}
    try:
        body = json.loads(result.stdout)
        if not isinstance(body, dict) or set(body) != {"valid"}:
            raise ValueError("Invalid checker response")
        value = body["valid"]
        if value is not None and not isinstance(value, bool):
            raise ValueError("Invalid checker decision")
    except (ValueError, TypeError):
        return {"execution_status": "malformed_output", "failure_status": "checker_result_unknown",
                "reason": "checker_output_protocol", "decision": None}
    return {"execution_status": "success", "failure_status": None if value is not None else "checker_result_unknown",
            "reason": None if value is not None else "checker_declared_unknown", "decision": value}


class GeneratedCheckerSession:
    def __init__(self, host):
        self.host = host
        self.agent, self.workspace, self.state = host.agent, host.workspace, host.state
        self.checkpoint = host.checkpoint
        spec = CheckerSpec.from_dict(self.checkpoint["sample_checker"])
        self.dimensions = checker_dimensions(spec, generated=True)
        self.messages = checker_messages(self.agent.context_builder, self.checkpoint["problem"])
        self.profile = self.agent.policy.choose(AgentRole.CODE, allow_escalation=False)
        route = self.agent.router.route(self.profile)
        self.identity = {"task_id": self.state.task_id, "problem_id": self.state.problem_id,
            "frozen_problem_sha256": stable_hash(self.checkpoint["problem"]),
            "checker_prompt_version": CHECKER_PROMPT_VERSION,
            "checker_prompt_sha256": stable_hash([asdict(m) for m in self.messages]),
            "profile": self.profile.value, "model_id": route.model, "provider": route.provider,
            "checker_contract_version": CHECKER_CONTRACT_VERSION}
        self.checker_id = "checker_" + stable_hash(self.identity)

    def _links(self, saved=None):
        saved = saved or self.checkpoint.get("generated_checker") or {}
        return {**self.host._links(), **self.dimensions, "checker_id": self.checker_id,
            "problem_id": self.state.problem_id, "generation_model_call_id": saved.get("model_call_id"),
            "profile": self.profile.value, "model_id": self.identity["model_id"],
            "checker_source_sha256": saved.get("code_sha256"),
            "candidate_version": self.state.solution_version,
            "candidate_source_sha256": self.state.solution_sha256}

    def _generation(self):
        saved = self.checkpoint.get("generated_checker")
        if saved is not None:
            if saved.get("identity") != self.identity or saved.get("checker_id") != self.checker_id:
                raise ValueError("Frozen generated checker identity differs")
            self._generation_evidence(saved)
            return saved
        pending = self.checkpoint.get("pending_operation") or {}
        if pending.get("purpose") == "sample_checker_generation":
            # A possibly sent paid request has no complete artifact: never retry.
            return self._commit_generation({"generation_status": "unknown", "reason": "generation_not_reissued",
                                            "model_call_id": self.state.last_model_call_id})
        profile, input_limit, output_limit = self.host._reserve_model(AgentRole.CODE, self.messages)
        self.checkpoint["pending_operation"].update(purpose="sample_checker_generation", checker_id=self.checker_id)
        self.host._save()
        response = None
        try:
            response = self.agent.model_runtime.complete(AgentRole.CODE, profile, self.messages,
                                                        purpose="sample_checker_generation")
            self.workspace.write_json("artifacts/models/" + self.state.last_model_call_id + ".json",
                {"model_call_id": self.state.last_model_call_id, "purpose": "sample_checker_generation", **asdict(response)})
            validate_usage(response, input_limit, output_limit)
            source = extract_cpp(response.content)
        except (ModelCallFailed, BudgetStopped, ValueError) as exc:
            reason = "invalid_checker_source" if isinstance(exc, ValueError) else "checker_model_call_failed"
            if isinstance(exc, BudgetStopped):
                reason = exc.reason
            return self._commit_generation({"generation_status": "failed", "reason": reason,
                "model_call_id": self.state.last_model_call_id})
        path = "artifacts/checkers/checker-v2.cpp"
        self.workspace.write_text(path, source, overwrite=False)
        return self._commit_generation({"generation_status": "success", "path": path,
            "code_sha256": digest(source), "model_call_id": self.state.last_model_call_id,
            "generation_metadata": {"actual_model_id": response.actual_response_model,
                "request_id": response.request_id, "usage": asdict(response.usage),
                "usage_metadata": response.usage_metadata, "finish_reason": response.finish_reason}})

    def _commit_generation(self, value):
        saved = {"schema_version": "generated_checker_v2", "status": "llm_generated_unverified",
                 "checker_id": self.checker_id, "identity": self.identity, "sanity": {}, "executions": {}, **value}
        self.checkpoint["generated_checker"] = saved
        self.checkpoint["pending_operation"] = None
        self.host._save()
        self._generation_evidence(saved)
        return saved

    def _event_once(self, kind, identity, record):
        # Repair an observation committed just before a Trace interruption;
        # this never repeats a model request or Custom Run.
        for line in self.workspace.read_text("events.jsonl").splitlines():
            event = json.loads(line)
            if event["type"] == kind and all(event["payload"].get(k) == v for k, v in identity.items()):
                return
        self.workspace.trace.append(kind, record)

    def _generation_evidence(self, saved):
        record = {**saved, **self._links(saved)}
        for kind in ("GENERATED_CHECKER", "CHECKER_GENERATION_RESULT"):
            self._event_once(kind, {"checker_id": self.checker_id}, record)
        if not (self.workspace.root / "artifacts/checkers/generation.json").exists():
            self.workspace.write_json("artifacts/checkers/generation.json", record)

    def _execute(self, saved, source, sample, stdout, *, stage, index):
        identity = {"checker_id": self.checker_id, "stage": stage, "sample_index": index + 1,
                    "stdin_sha256": digest(checker_stdin(sample["input"], stdout))}
        if stage == "candidate":
            identity.update(candidate_version=self.state.solution_version, candidate_source_sha256=self.state.solution_sha256)
        key = stable_hash(identity)
        existing = saved["executions"].get(key)
        if existing is not None:
            self._event_once("CHECKER_EXECUTION_RESULT", {"execution_id": key}, {**self._links(saved), **existing})
            return existing
        if self.checkpoint.get("pending_operation"):
            return {"execution_status": "unknown", "failure_status": "checker_result_unknown",
                    "reason": "checker_run_not_reissued", "decision": None, "execution_id": key}
        self.host._guard()
        self.state.custom_run_count += 1
        self.checkpoint["pending_operation"] = {"kind": "checker_run", "checker_id": self.checker_id,
                                                "execution_id": key, "stage": stage}
        self.host._save()
        run_event = self.workspace.trace.append("CHECKER_RUN", {**self._links(saved), **identity,
            "execution_id": key, "checker_sha256": saved["code_sha256"], "checker_model_call_id": saved["model_call_id"]})
        try:
            result = self.agent.tools.call("run_code", code=source, stdin=checker_stdin(sample["input"], stdout))
        except OJClientError as exc:
            observation = {"execution_status": "unknown" if exc.submission_state_unknown else "protocol_error",
                "failure_status": "checker_result_unknown" if exc.submission_state_unknown else "checker_execution_failed",
                "reason": "checker_remote_" + exc.kind.value, "decision": None, "result": None}
        except Exception as exc:
            observation = {"execution_status": "protocol_error", "failure_status": "checker_execution_failed",
                           "reason": "checker_adapter_" + type(exc).__name__, "decision": None, "result": None}
        else:
            observation = {**execution_observation(result), "result": result.as_dict()}
        observation.update(execution_id=key, checker_run_event_id=run_event, stage=stage, sample_index=index + 1)
        saved["executions"][key] = observation
        self.checkpoint["pending_operation"] = None
        self.host._save()
        self._event_once("CHECKER_EXECUTION_RESULT", {"execution_id": key}, {**self._links(saved), **observation})
        return observation

    def _observe(self, saved, *, label, sample_index, stage, **details):
        value = {**self._links(saved), "status": "llm_generated_unverified", "model_call_id": saved.get("model_call_id"),
                 "code_sha256": saved.get("code_sha256"), "sample_index": sample_index + 1,
                 "stage": stage, "checker_decision": label, "sample_check_status": label, **details}
        key = stable_hash({"checker_id": self.checker_id, "sample": sample_index, "stage": stage,
                           "candidate": self.state.solution_version, "decision": label})
        emitted = saved.setdefault("observations", {})
        if key not in emitted:
            self.workspace.trace.append("CHECKER_DECISION", value)
            if stage == "candidate":
                self.workspace.trace.append("GENERATED_CHECKER_RESULT",
                    {**self.host._links(), "sample_index": sample_index + 1, "generated_checker": value})
            self.workspace.write_json("artifacts/checkers/observations/" + key + ".json", value)
            emitted[key] = value
        self.state.checker_schema_version = "checker_state_v2"
        self.state.checker_observation = value
        self.state.sample_check_status = value["sample_check_status"]
        self.host._save()
        return value

    def check(self, sample, stdout, index, *, reference_only=False):
        saved = self._generation()
        stage = "reference" if reference_only else "candidate"
        if saved["generation_status"] != "success":
            return self._observe(saved, label="generation_failed", sample_index=index, stage=stage,
                decision=None, failure_status="checker_result_unknown" if saved["generation_status"] == "unknown"
                    else "checker_generation_failed", generation_status=saved["generation_status"],
                sanity_status="not_run", execution_status="not_run", reason=saved["reason"])
        source = self.workspace.read_text(saved["path"])
        if digest(source) != saved["code_sha256"]:
            raise ValueError("Saved checker source hash differs")
        sample_key = stable_hash(sample)
        sanity = saved["sanity"].get(sample_key)
        if sanity is None:
            sanity = self._execute(saved, source, sample, sample["output"], stage="reference", index=index)
            sanity = {**sanity, "sanity_status": "pass" if sanity["decision"] is True else
                      "failed" if sanity["decision"] is False else "unknown"}
            saved["sanity"][sample_key] = sanity
            self.host._save()
        self._event_once("CHECKER_SANITY_RESULT", {"execution_id": sanity["execution_id"]}, {**self._links(saved), **sanity})
        if sanity["decision"] is not True:
            failed = sanity["failure_status"] or "checker_sanity_failed"
            return self._observe(saved, label="sanity_failed" if sanity["decision"] is False else "execution_failed",
                sample_index=index, stage=stage, decision=None, failure_status=failed, sanity_passed=False,
                sanity_status=sanity["sanity_status"], execution_status=sanity["execution_status"],
                reason=sanity["reason"] or "published_reference_rejected", execution_id=sanity["execution_id"],
                checker_run_event_id=sanity.get("checker_run_event_id"),
                result=sanity.get("result"))
        if reference_only:
            return self._observe(saved, label="pass_unverified", sample_index=index, stage=stage,
                decision=True, failure_status=None, sanity_passed=True, sanity_status="pass", execution_status="success",
                execution_id=sanity["execution_id"], checker_run_event_id=sanity.get("checker_run_event_id"), result=sanity["result"])
        run = self._execute(saved, source, sample, stdout, stage="candidate", index=index)
        label = "pass_unverified" if run["decision"] is True else "rejected_unverified" if run["decision"] is False else "execution_failed"
        return self._observe(saved, label=label, sample_index=index, stage=stage, decision=run["decision"],
            failure_status=run["failure_status"] or ("checker_rejected_unverified" if run["decision"] is False else None),
            sanity_passed=True, sanity_status="pass", execution_status=run["execution_status"],
            execution_id=run["execution_id"], checker_run_event_id=run.get("checker_run_event_id"),
            result=run.get("result"), reason=run["reason"])
