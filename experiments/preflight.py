"""Read-only experiment preflight. No solve, completion, Custom Run or submission."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx

from agent.core.checker import CheckerSpec, SampleGatePolicy
from agent.core.policy import ModelPolicy
from agent.execution import fingerprint
from agent.models.registry import ModelRegistry
from agent.oj_client.client import OJClient, OJClientError
from agent.oj_client.feedback import VERDICT_ONLY_POLICY, compatible_feedback
from .config import IDENTIFIER
from .freeze import frozen_fingerprint, prompt_snapshot, validate_prompts, resolve_git_root
from .order import execution_order
from .preflight_models import apply_model_metadata, configured_models, model_checks, registry_profiles

PREFLIGHT_VERSION = "experiment_preflight_v1"
METRIC_FIELDS = ("problem_id", "rating", "condition", "mode", "profile", "actual_models",
    "checker_type", "checker_source", "checker_policy", "solved", "final_verdict", "first_try_ac",
    "recovered_to_ac", "recovery_type", "formal_recovery_to_ac", "llm_calls", "input_tokens", "output_tokens",
    "estimated_cost_cny", "custom_runs", "formal_submissions", "candidate_versions", "debug_count", "replan_count",
    "sample_gate_reject_count", "sample_check_unverifiable_count", "invalid_model_output_count", "wall_time")


class PreflightError(ValueError):
    """Safe, fixed diagnostic text that the CLI may display without config secrets."""


class ReadOnlyProbe:
    """Fixed GET allowlist, bounded payloads, no redirects or remote error prose."""
    def __init__(self, settings, *, timeout=15, client=None):
        self.oj = OJClient(settings.oj_base_url, settings.oj_api_token, timeout_seconds=timeout, client=client)
        self.metadata_timeout = min(timeout, 15)
        self.requests = []

    def _oj_get(self, path):
        record = {"method": "GET", "service": "MiniOJ", "path": path,
                  "endpoint_fingerprint": fingerprint(self.oj.base_url), "http_status": None,
                  "requested_at": datetime.now(timezone.utc).isoformat()}
        self.requests.append(record)
        try:
            response = self.oj._request_response("GET", path, expected_statuses=(200,), follow_redirects=False)
            record["http_status"] = response.status_code
            body = response.json() if len(response.content) <= 2_000_000 else None
            if not isinstance(body, dict):
                record["status"] = "invalid_metadata"
                return None
            record["status"] = "received"
            return body
        except (OJClientError, ValueError) as exc:
            record["status"] = type(exc).__name__
            record["http_status"] = getattr(exc, "http_status", None)
            return None

    def feedback(self):
        body = self._oj_get("/api/v1/me") or {}
        mode = body.get("feedback_mode")
        return {"mode": mode if mode in ("full", "verdict_only") else None,
                "source": "GET /api/v1/me#feedback_mode", "status": "confirmed" if mode in ("full", "verdict_only") else "unknown"}

    def problem(self, problem_id):
        identifier = quote(problem_id, safe="")
        path = f"/api/v1/problems/{identifier}"
        body = self._oj_get(path)
        if body is not None and body.get("problem_id") != problem_id:
            body = None
        result = {"problem_id": problem_id, "metadata_available": body is not None,
                  "checker": None, "rating": None, "checker_source": f"GET {path}#checker"}
        if body is not None:
            checker = body.get("checker")
            result["checker"] = checker if isinstance(checker, str) and len(checker) <= 50 else None
            rating = body.get("rating")
            result["rating"] = rating if isinstance(rating, int) and not isinstance(rating, bool) and rating >= 0 else None
        # Reuse the sanitized public problem parser; ordinary metadata never
        # enters the Agent or the preflight artifacts through this operation.
        sample_path = f"/api/v1/agent/problems/{identifier}"
        sample_body = self._oj_get(sample_path)
        result.update(sample_count=None, problem_input_hash=None)
        if sample_body is not None:
            from agent.oj_client.types import AgentProblem, ProtocolValidationError
            try:
                parsed = AgentProblem.from_dict(sample_body)
                if parsed.problem_id == problem_id:
                    result.update(sample_count=len(parsed.samples), problem_input_hash=fingerprint(parsed.as_dict()))
            except ProtocolValidationError:
                pass
        return result

    def provider_models(self, provider):
        record = {"method": "GET", "service": "provider", "path": "/models",
                  "endpoint_fingerprint": fingerprint(provider.base_url), "http_status": None,
                  "requested_at": datetime.now(timezone.utc).isoformat()}
        self.requests.append(record)
        try:
            response = provider.client.get(f"{provider.base_url}/models",
                headers={"Authorization": f"Bearer {provider._api_key}"}, follow_redirects=False,
                timeout=self.metadata_timeout)
            record["http_status"] = response.status_code
            if response.status_code != 200 or len(response.content) > 2_000_000:
                record["status"] = "unavailable"
                return None
            body = response.json()
            record["status"] = "received"
            return body
        except (httpx.HTTPError, ValueError):
            record["status"] = "unavailable"
            return None

    def close(self):
        self.oj.close()


def checker_preflight(config, observations):
    policy = SampleGatePolicy.from_dict(config.sample_checking) if config.sample_checking else None
    has_harness = any(strategy.mode == "harness-loop" for strategy in config.strategies)
    rows, blockers, warnings = [], [], []
    for problem in config.problems:
        observation = observations.get(problem, {})
        local = config.problem_metadata.get(problem, {})
        if policy is None:
            spec = CheckerSpec("token", source="legacy_whitespace_tokens_v1")
            resolution = "client_default"
        elif problem in policy.overrides:
            spec = CheckerSpec.from_dict(policy.overrides[problem])
            resolution = "explicit_experiment_config"
        elif observation.get("metadata_available"):
            spec = CheckerSpec.from_metadata(observation.get("checker"), source=observation["checker_source"])
            resolution = "MiniOJ_API"
        else:
            spec = CheckerSpec()
            resolution = "unknown"
        row_warnings, row_blockers = [], []
        if policy is None and has_harness:
            row_blockers.append("checker_policy_not_current")
            row_warnings.append("legacy_checker_semantics_unverified")
        if local.get("rating") is not None and observation.get("rating") is not None and local["rating"] != observation["rating"]:
            row_blockers.append("problem_rating_drift")
        if policy is not None and problem in policy.overrides and observation.get("checker") is not None:
            advertised = CheckerSpec.from_metadata(observation["checker"], source=observation["checker_source"])
            if advertised.kind != "unknown" and advertised.kind != spec.kind:
                # Never rewrite an explicit policy to make the report pass.
                row_blockers.append("checker_metadata_conflicts_with_frozen_override")
        trusted = policy is not None and (spec.kind in {"exact", "token"} or (spec.kind == "float"
            and spec.absolute_tolerance is not None and spec.relative_tolerance is not None))
        llm = has_harness and not trusted and policy is not None and policy.llm_checker != "disabled"
        count = observation.get("sample_count")
        if count == 0:
            llm = False  # runtime does not invoke a sample checker without samples
        if spec.kind == "float" and not trusted:
            row_warnings.append("float_tolerances_missing")
        if llm:
            row_warnings.append("LLM_checker_required_unverified")
        if not trusted and has_harness and count != 0 and policy is not None and (
            policy.on_unverifiable != "submit" and policy.llm_checker != "submit_on_pass"):
            row_blockers.append("required_checker_path_impossible")
        if not observation.get("metadata_available"):
            row_warnings.append("problem_metadata_unavailable")
        if count is None:
            row_warnings.append("sample_count_unavailable")
        if not trusted and not llm:
            row_warnings.append("sample_check_unverifiable")
        stratum = "fully_client_verifiable" if trusted else "llm_checker_required" if llm else "unverifiable"
        rows.append({"problem_id": problem, "rating": local.get("rating", observation.get("rating")),
            "rating_source": local.get("rating_source"), "observed_rating": observation.get("rating"),
            "checker_type": spec.kind, "effective_checker_type": "llm_generated_unverified" if llm else spec.kind,
            "checker_source": spec.source, "checker_resolution_source": resolution,
            "observed_checker": observation.get("checker"),
            "checker_policy": policy.version if policy else "legacy_whitespace_tokens_v1",
            "checker_spec": as_spec(spec), "float_abs_tolerance": spec.absolute_tolerance,
            "float_rel_tolerance": spec.relative_tolerance, "requires_remote_checker": spec.kind == "special",
            "remote_checker_available": False, "requires_llm_generated_checker": llm,
            "llm_checker_policy": policy.llm_checker if policy else "disabled", "sample_count": count,
            "checker_stratum": stratum, "verified_checker": trusted,
            "problem_input_hash": observation.get("problem_input_hash"),
            "preflight_status": "BLOCKED" if row_blockers else "READY_WITH_WARNINGS" if row_warnings else "READY",
            "blockers": row_blockers, "warnings": row_warnings})
        blockers.extend(f"{value}:{problem}" for value in row_blockers)
        warnings.extend(f"{value}:{problem}" for value in row_warnings)
    kinds = ("exact", "token", "float", "special", "unknown", "llm_generated_unverified")
    counts = Counter(row["checker_type"] for row in rows)
    effective = Counter(row["effective_checker_type"] for row in rows)
    strata = Counter(row["checker_stratum"] for row in rows)
    return {"problems": rows, "checker_distribution": {kind: counts[kind] for kind in kinds},
            "effective_checker_distribution": {kind: effective[kind] for kind in kinds},
            "strata": {key: strata[key] for key in ("fully_client_verifiable", "remote_verifiable", "llm_checker_required", "unverifiable")},
            "llm_checker_risk": [row["problem_id"] for row in rows if row["requires_llm_generated_checker"]]}, blockers, warnings


def as_spec(spec):
    from dataclasses import asdict
    return asdict(spec)


def feedback_preflight(config, observation):
    actual = observation.get("mode")
    policy = VERDICT_ONLY_POLICY if config.expected_feedback_mode == "verdict_only" else None
    ready = actual in {"full", "verdict_only"} and compatible_feedback(config.expected_feedback_mode, actual, policy)
    effective = "verdict_only" if ready and policy else actual if ready else None
    return {"expected_feedback_mode": config.expected_feedback_mode, "actual_feedback_mode": actual,
            "effective_feedback_mode": effective, "feedback_policy": policy,
            "feedback_source": observation.get("source"), "feedback_preflight_status": "READY" if ready else "BLOCKED"}


def build_preflight(config, settings, *, registry=None, policy=None, probe=None, config_path=None,
                    git_root=None, seed=None, offline=False):
    now = datetime.now(timezone.utc).isoformat()
    blockers, warnings = [], []
    try:
        order = execution_order(config, seed)
    except ValueError:
        report = blocked_preflight("invalid_execution_ordering", config_path=config_path)
        report["task_count"] = config.task_count
        return report
    prompts = prompt_snapshot()
    try:
        validate_prompts(prompts)
    except (ValueError, TypeError):
        blockers.append("missing_prompt_snapshot")
    models, routes = [], {}
    owns_registry = registry is None
    try:
        policy = policy or ModelPolicy.from_yaml(config.model_config)
        registry = registry or ModelRegistry.from_yaml(config.model_config, required_profiles=registry_profiles(config, policy))
        models, routes = configured_models(config, registry, policy)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        blockers.append("required_model_or_profile_unresolved:" + type(exc).__name__)
    frozen = None
    try:
        frozen = frozen_fingerprint(config, models, routes, settings.oj_base_url,
            config_path=config_path, git_root=git_root, seed=seed, prompts=prompts)
    except (OSError, ValueError, TypeError, KeyError):
        blockers.append("fingerprint_inconsistent")
    observations, feedback = {}, {"mode": None, "source": None}
    probe_owned = probe is None
    probe = probe or ReadOnlyProbe(settings, timeout=config.http_timeout)
    try:
        if not offline:
            feedback = probe.feedback()
            observations = {problem: probe.problem(problem) for problem in config.problems}
            metadata = {}
            for model in models:
                name = model["provider"]
                if name not in metadata:
                    metadata[name] = probe.provider_models(registry.provider(name))
                apply_model_metadata(model, metadata[name], verified_at=datetime.now(timezone.utc).isoformat())
        checkers, failed, warned = checker_preflight(config, observations)
        blockers.extend(failed); warnings.extend(warned)
        failed, warned = model_checks(models, config)
        blockers.extend(failed); warnings.extend(warned)
        feedback = feedback_preflight(config, feedback)
        if feedback["feedback_preflight_status"] == "BLOCKED":
            blockers.append("feedback_mode_unknown" if feedback["actual_feedback_mode"] is None else "feedback_mode_mismatch")
        if config.expected_feedback_mode != "verdict_only" or not config.require_feedback_mode:
            blockers.append("experiment_requires_effective_verdict_only")
        # A local file edit during GET collection must not create a mixed snapshot.
        if frozen:
            current = frozen_fingerprint(config, models, routes, settings.oj_base_url,
                config_path=config_path, git_root=git_root, seed=seed, prompts=prompts)
            if current != frozen:
                blockers.append("fingerprint_inconsistent")
            if frozen["components"]["git_dirty"]:
                warnings.append("git_dirty")
            if frozen["components"]["git_commit_sha"] is None:
                blockers.append("git_snapshot_unavailable")
        if not models:
            blockers.append("required_model_unresolved")
        minimums = {}
        prices = {model["profile"]: model.get("configured_call_reservation_cny") for model in models}
        for strategy in config.strategies:
            roles = routes.get(strategy.name, {})
            calls = [roles.get("CODE")] if strategy.mode == "code-only" else [roles.get("PLAN"), roles.get("CODE")]
            if strategy.mode == "harness-loop" and checkers["llm_checker_risk"]:
                calls.append(roles.get("CODE"))
            amount = sum(prices[profile] for profile in calls) if all(prices.get(profile) is not None for profile in calls) else None
            minimums[strategy.name] = {"startup_model_calls": len(calls), "startup_reservation_cny": amount}
            if strategy.mode == "harness-loop" and config.harness.max_llm_calls < len(calls):
                blockers.append(f"budget_config_invalid:{strategy.name}:startup_call_cap")
            if amount is not None and amount > config.harness.max_cost_cny:
                blockers.append(f"budget_config_invalid:{strategy.name}:startup_reservation_exceeds_cap")
        if config.preparation.get("five_conditions") and config.total_cost_cny < config.harness.max_cost_cny * config.task_count:
            blockers.append("batch_budget_cannot_allocate_equal_task_caps")
        return {"schema_version": PREFLIGHT_VERSION, "created_at": now,
            "config_path": str(Path(config_path).resolve()) if config_path else None,
            "git_root": str(resolve_git_root(git_root or (Path(config_path).resolve().parent if config_path else Path.cwd()))),
            "status": "BLOCKED" if blockers else "READY_WITH_WARNINGS" if warnings else "READY",
            "blockers": sorted(set(blockers)), "warnings": sorted(set(warnings)),
            "execution_authorized": False, "task_count": config.task_count,
            "checker_summary": checkers, "models": models, "condition_routes": routes,
            "runtime_snapshot_profile_dependencies": sorted(profile.value for profile in registry_profiles(config, policy)
                if profile.value not in {profile for values in routes.values() for profile in values.values()}) if registry and policy else [],
            "feedback": feedback, "fingerprint": frozen, "execution": order,
            "prompt_snapshot": prompts, "metric_fields": list(METRIC_FIELDS),
            "cost_boundary": {"per_task_budget_cny": config.harness.max_cost_cny,
                "batch_budget_cny": config.total_cost_cny,
                "minimum_startup_reservations": minimums,
                "budget_note": "budget cap != estimated actual spend",
                "price_note": "configured price != verified billing"},
            "side_effects": {"llm_calls": 0, "custom_runs": 0, "formal_submissions": 0,
                "paid_requests": 0, "paid_cost_cny": 0, "read_only_http_gets": list(probe.requests)}}
    finally:
        if probe_owned:
            probe.close()
        if owns_registry and registry:
            for provider in registry.providers.values():
                provider.client.close()


def preflight_directory(workspace_root, preflight_id):
    if not isinstance(preflight_id, str) or not IDENTIFIER.fullmatch(preflight_id):
        raise ValueError("Invalid preflight ID")
    base = Path(workspace_root).resolve() / ".experiments"
    root = base / preflight_id
    if base.is_symlink() or root.is_symlink() or (root / "preflight").is_symlink():
        raise ValueError("Unsafe preflight storage")
    return root / "preflight"


def report_markdown(report):
    lines = [f"# Experiment preflight: {report['status']}", "", f"ID: {report['preflight_id']}",
             f"Tasks: {report['task_count']}; execution_authorized=false", "", "## Blockers", ""]
    lines += [f"- {value}" for value in report["blockers"]] or ["None."]
    lines += ["", "## Warnings / LLM checker risk", ""]
    lines += [f"- {value}" for value in report["warnings"]] or ["None."]
    lines += ["", "## Problems", "", "| Problem | Rating | Checker | Source | Sample count | Stratum | Status |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for row in report["checker_summary"]["problems"]:
        lines.append("| " + " | ".join(str(row[key]).replace("|", "\\|").replace("\n", " ") for key in (
            "problem_id", "rating", "checker_type", "checker_source", "sample_count", "checker_stratum", "preflight_status")) + " |")
    for title, value in [("Checker distribution / strata", report["checker_summary"]),
                         ("Models: configured and verified", report["models"]),
                         ("Feedback", report["feedback"]), ("Frozen experiment", report["fingerprint"]),
                         ("Execution order", report["execution"]), ("Cost boundary", report["cost_boundary"]),
                         ("Side effects / GET audit", report["side_effects"])]:
        lines += ["", f"## {title}", "", "```json", json.dumps(value, ensure_ascii=False, indent=2), "```"]
    return "\n".join(lines) + "\n"


def save_preflight(report, workspace_root, preflight_id):
    directory = preflight_directory(workspace_root, preflight_id)
    # Neither a historical experiment nor a previous preflight may be replaced.
    directory.parent.mkdir(parents=True, exist_ok=False)
    directory.mkdir()
    report = {**report, "preflight_id": preflight_id}
    artifacts = {"execution-order.json": report["execution"], "model-snapshot.json": {
        "models": report["models"], "condition_routes": report["condition_routes"]},
        "prompt-snapshot.json": report["prompt_snapshot"], "checker-summary.json": report["checker_summary"]}
    report["artifact_hashes"] = {name: fingerprint(value) for name, value in artifacts.items()}
    report["preflight_report_hash"] = fingerprint(report)
    for name, value in {**artifacts, "preflight.json": report}.items():
        with (directory / name).open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    with (directory / "preflight.md").open("x", encoding="utf-8") as handle:
        handle.write(report_markdown(report))
    return report


def load_preflight(workspace_root, preflight_id):
    directory = preflight_directory(workspace_root, preflight_id)
    path = directory / "preflight.json"
    if path.is_symlink():
        raise ValueError("Unsafe preflight report")
    report = json.loads(path.read_text(encoding="utf-8"))
    signature = report.pop("preflight_report_hash", None)
    if (signature != fingerprint(report) or report.get("schema_version") != PREFLIGHT_VERSION
            or report.get("preflight_id") != preflight_id):
        raise ValueError("Preflight report integrity mismatch")
    report["preflight_report_hash"] = signature
    if set(report["artifact_hashes"]) != {"execution-order.json", "model-snapshot.json", "prompt-snapshot.json", "checker-summary.json"}:
        raise ValueError("Missing preflight snapshot")
    for name, expected in report["artifact_hashes"].items():
        if name not in {"execution-order.json", "model-snapshot.json", "prompt-snapshot.json", "checker-summary.json"}:
            raise ValueError("Invalid preflight artifact")
        path = directory / name
        if path.is_symlink() or fingerprint(json.loads(path.read_text(encoding="utf-8"))) != expected:
            raise ValueError("Preflight artifact integrity mismatch")
    return report


def blocked_preflight(reason, *, config_path=None):
    """Still emit an auditable BLOCKED report for invalid local configuration."""
    return {"schema_version": PREFLIGHT_VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
        "config_path": str(Path(config_path).resolve()) if config_path else None,
        "status": "BLOCKED", "blockers": [reason], "warnings": [], "execution_authorized": False,
        "task_count": 0, "checker_summary": {"problems": [], "checker_distribution": {}, "strata": {},
            "llm_checker_risk": []}, "models": [], "condition_routes": {}, "feedback": {
            "expected_feedback_mode": "verdict_only", "actual_feedback_mode": None,
            "effective_feedback_mode": None, "feedback_policy": VERDICT_ONLY_POLICY,
            "feedback_preflight_status": "BLOCKED"}, "fingerprint": None,
        "execution": {"execution_seed": None, "execution_order": [], "execution_order_hash": None},
        "prompt_snapshot": None, "metric_fields": list(METRIC_FIELDS), "cost_boundary": {},
        "side_effects": {"llm_calls": 0, "custom_runs": 0, "formal_submissions": 0,
            "paid_requests": 0, "paid_cost_cny": 0, "read_only_http_gets": []}}


def check_frozen_preflight(config, service, preflight_id, *, expected_report_hash=None):
    try:
        report = load_preflight(service.workspace_root, preflight_id)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise PreflightError("Frozen preflight is missing or invalid; use a new preflight/experiment ID") from exc
    if report["status"] not in {"READY", "READY_WITH_WARNINGS"}:
        raise PreflightError("Preflight is BLOCKED; a new preflight is required")
    validate_prompts(report["prompt_snapshot"])
    if expected_report_hash is not None and report["preflight_report_hash"] != expected_report_hash:
        raise PreflightError("Saved preflight differs from the experiment; start a new ID")
    models, routes = configured_models(config, service.registry, service.base_policy)
    model_checks(models, config)
    components = report["fingerprint"]["components"]
    source = report.get("config_path")
    current = frozen_fingerprint(config, models, routes, service.settings.oj_base_url,
        config_path=source, git_root=report.get("git_root"), seed=report["execution"]["execution_seed"])
    if current != report["fingerprint"]:
        changes = [key for key in current["components"] if current["components"][key] != components.get(key)]
        raise PreflightError("Frozen preflight drift (" + ", ".join(changes) + "); use a new preflight/experiment ID")
    if report["execution"] != execution_order(config, report["execution"]["execution_seed"]):
        raise PreflightError("Invalid execution ordering in preflight")
    return report
