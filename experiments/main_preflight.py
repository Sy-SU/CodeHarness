"""Additive final-main receipt in the existing preflight store; never runs a task."""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
from collections import Counter
from pathlib import Path

from agent.execution import fingerprint
from .config import IDENTIFIER, MAIN_RECEIPT_POLICY, requires_main_receipt
from .freeze import file_hash, resolve_git_root
from .order import validate_order
from .preflight import PreflightError, load_preflight, preflight_directory

MAIN_GATES = {"MAIN_V1_READY", "MAIN_V1_READY_WITH_WARNINGS", "MAIN_V1_BLOCKED"}
VERIFICATION_CLASSES = ("client_verified", "remote_verified", "llm_generated_unverified", "unverifiable")


def safe_id(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("Invalid experiment/preflight ID")
    return value


def agent_source_hash(root, commit=None):
    """Compare Agent behavior with the Pilot commit independently of report changes."""
    root = Path(root)
    if commit:
        try:
            archive = subprocess.check_output(["git", "-C", str(root), "archive", commit, "agent"],
                                              stderr=subprocess.DEVNULL)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ValueError("Agent source snapshot unavailable") from exc
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            files = {entry.name: hashlib.sha256(source.extractfile(entry).read()).hexdigest()
                     for entry in source.getmembers() if entry.isfile() and entry.name.endswith(".py")}
    else:
        files = {str(path.relative_to(root)): file_hash(path) for path in sorted((root / "agent").rglob("*.py"))}
    if not files:
        raise ValueError("Agent source snapshot unavailable")
    return fingerprint(files)


def load_pilot_evidence(workspace_root, run_id):
    """Only validated history is evidence. No checkpoint, candidate or response is imported."""
    safe_id(run_id)
    root = Path(workspace_root).resolve()
    audit = root / ".pilot-v2-validation" / run_id
    paths = {"receipt": audit / "final-receipt.json", "validation": audit / "pilot-v2-validation.json",
             "manifest": root / ".experiments" / run_id / "manifest.json"}
    for path in paths.values():
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError("Unsafe Pilot evidence")
    receipt, validation, manifest = (json.loads(paths[key].read_text(encoding="utf-8"))
                                     for key in ("receipt", "validation", "manifest"))
    if (receipt.get("gate") != "PILOT_V2_VALIDATED_WITH_WARNINGS"
            or validation.get("gate") != receipt["gate"] or validation.get("blockers")
            or receipt.get("experiment_id") != run_id or manifest.get("experiment_id") != run_id
            or manifest.get("status") != "completed"
            or receipt.get("files_sha256", {}).get("pilot-v2-validation.json") != file_hash(paths["validation"])
            or receipt.get("fingerprint") != manifest.get("experiment_fingerprint")
            or validation.get("identity", {}).get("fingerprint") != receipt["fingerprint"]["sha256"]
            or fingerprint(manifest["config"]) != receipt["fingerprint"]["components"]["experiment_config_hash"]):
        raise ValueError("Pilot validation/receipt integrity mismatch")
    preflight = load_preflight(root, receipt["preflight_id"])
    if (preflight["fingerprint"] != receipt["fingerprint"]
            or preflight["preflight_report_hash"] != manifest["preflight_report_hash"]):
        raise ValueError("Pilot frozen evidence mismatch")
    paths["preflight"] = preflight_directory(root, receipt["preflight_id"]) / "preflight.json"
    task_ids = [row["task_id"] for row in validation["tasks"]]
    if (len(task_ids) != len(set(task_ids)) or set(task_ids) != {row["task_id"] for row in manifest["tasks"]}
            or validation["accounting"].get("terminal") != len(task_ids)
            or validation["accounting"].get("infrastructure_failures") != 0):
        raise ValueError("Pilot task evidence mismatch")
    git_root = resolve_git_root(preflight.get("git_root") or Path.cwd())
    return {"run_id": run_id, "receipt": receipt, "validation": validation,
            "config": manifest["config"], "preflight": preflight,
            "problem_ids": sorted({row["problem_id"] for row in validation["tasks"]}),
            "task_ids": task_ids, "agent_source_hash": agent_source_hash(git_root, receipt["git_sha"]),
            "file_hashes": {str(path.relative_to(root)): file_hash(path) for path in paths.values()}}


def checker_matrix(rows, pilot=None, smoke=None):
    seen = set(pilot["problem_ids"]) if pilot else set()
    # Coverage comes from validated live token checks, not merely seeing a problem.
    live = set((pilot or {}).get("validation", {}).get("checker", {}).get("problem_ids", []))
    live_kind = (pilot or {}).get("validation", {}).get("checker", {}).get("kind")
    prior_rows = {row["problem_id"]: row for row in (pilot or {}).get("preflight", {}).get("checker_summary", {}).get("problems", [])}
    result = []
    smoke_rows = {row["problem_id"]: row for row in (smoke or {}).get("summary", {}).get("problems", [])}
    for row in rows:
        trusted = row["verified_checker"]
        remote = row.get("remote_checker_available") is True
        llm = row["requires_llm_generated_checker"]
        kind = row["checker_type"]
        verification = ("client_verified" if trusted else "remote_verified" if remote else
                        "llm_generated_unverified" if llm else "unverifiable")
        previous = prior_rows.get(row["problem_id"], {})
        coverage = (row["problem_id"] in live and kind == live_kind and verification == "client_verified"
                    and row.get("problem_input_hash") is not None
                    and row["problem_input_hash"] == previous.get("problem_input_hash")
                    and row["checker_spec"] == previous.get("checker_spec"))
        smoke_row = smoke_rows.get(row["problem_id"], {})
        smoke_match = (bool(smoke_row) and smoke_row.get("problem_input_hash") == row.get("problem_input_hash")
                       and smoke_row.get("checker_spec") == row.get("checker_spec"))
        smoke_live = smoke_row.get("live_path_status") if smoke_match else "UNKNOWN" if smoke_row else None
        coverage = coverage or smoke_live == "PASS"
        result.append({**row, "requires_float_tolerance": kind == "float",
            "abs_tolerance": row["float_abs_tolerance"], "rel_tolerance": row["float_rel_tolerance"],
            "requires_llm_checker": llm, "verification_class": verification,
            "checker_verification_class": verification, "pilot_seen": row["problem_id"] in seen,
            "pilot_run_id": pilot["run_id"] if pilot and row["problem_id"] in seen else None,
            "checker_live_coverage": coverage,
            "live_validation_required": not coverage and row.get("sample_policy") != "execution_only",
            "checker_smoke_id": smoke["summary"]["smoke_id"] if smoke_row else None,
            "checker_smoke_live_path_status": smoke_live,
            "checker_smoke_generation_status": smoke_row.get("generation_status"),
            "checker_smoke_execution_status": smoke_row.get("execution_status"),
            "checker_smoke_reference_sanity_status": smoke_row.get("reference_sanity_status"),
            "checker_semantic_certification": False,
            "legal_runtime_path": not bool(row["blockers"])})
    types = Counter(row["checker_type"] for row in result)
    return {"problems": result,
            "checker_counts": {kind: types[kind] for kind in ("exact", "token", "float", "special", "unknown")},
            "requires_llm_checker": sum(row["requires_llm_checker"] for row in result),
            "strata": {kind: [row["problem_id"] for row in result if row["verification_class"] == kind]
                       for kind in VERIFICATION_CLASSES}}


def freeze_diff(report, config, pilot):
    prior = pilot["preflight"]["fingerprint"]
    current = report["fingerprint"]
    items = []
    def compare(name, before, after):
        status = "same" if before == after else "not_applicable" if before is None or after is None else "changed"
        items.append({"field": name, "status": status, "pilot": before, "main": after})
    for key in sorted(set(prior["components"]) | set(current["components"])):
        compare(key, prior["components"].get(key), current["components"].get(key))
    compare("condition_routes", pilot["preflight"]["condition_routes"], report["condition_routes"])
    compare("conditions", pilot["config"]["strategies"], config.as_dict()["strategies"])
    for model in report["models"]:
        previous = next((old for old in pilot["preflight"]["models"] if old["profile"] == model["profile"]), {})
        for key in ("model_id", "provider", "endpoint_fingerprint"):
            compare("model:" + model["profile"] + ":" + key, previous.get(key), model[key])
        for key, value in model["configured"].items():
            compare("model:" + model["profile"] + ":" + key, previous.get("configured", {}).get(key), value)
    for key in ("http_timeout", "poll_interval", "deadline", "send_custom_run_code_alias", "repetitions"):
        compare(key, pilot["config"].get(key), config.as_dict().get(key))
    def global_sample(value):
        return {key: entry for key, entry in (value or {}).items() if key != "overrides"}
    compare("sample_gate_global_policy", global_sample(pilot["config"].get("sample_checking")),
            global_sample(config.sample_checking))
    old_specs = {row["problem_id"]: row["checker_spec"] for row in pilot["preflight"]["checker_summary"]["problems"]}
    for row in report["checker_summary"]["problems"]:
        if row["problem_id"] in old_specs:
            compare("shared_checker_spec:" + row["problem_id"], old_specs[row["problem_id"]], row["checker_spec"])
    compare("agent_source_hash", pilot["agent_source_hash"], agent_source_hash(Path(__file__).resolve().parents[1]))
    return {"pilot_fingerprint": prior["sha256"], "main_fingerprint": current["sha256"], "items": items,
            "note": "New main identity/list/order/report runtime are separate; shared Agent behavior must stay frozen."}


def extend_main_preflight(report, config, *, main_experiment_id, workspace_root, pilot=None, pilot_error=None,
                          smoke=None, smoke_error=None):
    """Final main checks are stricter than a generic preparation; still zero execution."""
    safe_id(main_experiment_id)
    report = dict(report)
    blockers, warnings = list(report["blockers"]), list(report["warnings"])
    if not requires_main_receipt(config) or not config.preparation.get("five_conditions"):
        blockers.append("main_requires_original_12_x_5_x_1_layout")
    if (Path(workspace_root) / ".experiments" / main_experiment_id).exists():
        blockers.append("main_experiment_id_already_exists")
    try:
        validate_order(config, report["execution"]["execution_order"])
    except (ValueError, TypeError, KeyError):
        blockers.append("execution_order_invalid")
    from agent.core.checker import SAMPLE_POLICY, GENERATED_SAMPLE_POLICY
    checker_v2 = (config.sample_checking or {}).get("version") == GENERATED_SAMPLE_POLICY
    checker_v3 = (config.sample_checking or {}).get("version") == SAMPLE_POLICY
    scope = None
    approved = set()
    if checker_v2 or checker_v3:
        if checker_v2 and not smoke:
            blockers.append("checker_v2_live_smoke_unavailable" + (":" + smoke_error if smoke_error else ""))
        if pilot and (checker_v3 or smoke):
            from .checker_scope import checker_change_scope
            scope = checker_change_scope(Path(__file__).resolve().parents[1], pilot["receipt"]["git_sha"],
                version=SAMPLE_POLICY if checker_v3 else GENERATED_SAMPLE_POLICY)
            blockers.extend(scope["blockers"])
            old_global = {k: v for k, v in (pilot["config"].get("sample_checking") or {}).items() if k not in {"version", "overrides"}}
            new_global = {k: v for k, v in config.sample_checking.items() if k not in {"version", "overrides"}}
            def algorithm_prompts(snapshot):
                return {p["prompt_name"]: {k: v for k, v in p.items() if k != "sha256"}
                        for p in snapshot["prompts"] if p["prompt_name"] != "sample_checker_generation"}
            prompts_unchanged = algorithm_prompts(pilot["preflight"]["prompt_snapshot"]) == algorithm_prompts(report["prompt_snapshot"])
            sample_policy_approved = (new_global == {"on_unverifiable": "stop", "llm_checker": "disabled",
                "unverifiable_output": "execution_only"}) if checker_v3 else old_global == new_global
            scope.update(algorithm_prompts_unchanged=prompts_unchanged,
                nonversion_sample_policy_unchanged=old_global == new_global, sample_policy_approved=sample_policy_approved)
            if not prompts_unchanged:
                blockers.append("algorithm_prompt_changed")
            if not sample_policy_approved:
                blockers.append("sample_policy_not_authorized")
            if not scope["blockers"] and prompts_unchanged and sample_policy_approved:
                approved = {"agent_source_hash", "prompt_bundle_hash", "checker_policy_version", "sample_gate_global_policy"}
        if checker_v2 and smoke and smoke["summary"]["gate"] == "CHECKER_LIVE_SMOKE_FAILED":
            blockers.append("checker_live_smoke_failed")
    # v3 neither imports smoke sources nor treats unverified smoke decisions as
    # critical-path coverage. Its independent legal path is public execution.
    matrix = checker_matrix(report["checker_summary"]["problems"], pilot, None if checker_v3 else smoke)
    report["checker_summary"] = {**report["checker_summary"], "problems": matrix["problems"]}
    for row in matrix["problems"]:
        if not row.get("metadata_available") or not row.get("problem_input_hash") or row["sample_count"] is None:
            blockers.append("problem_inaccessible:" + row["problem_id"])
        if row.get("sample_policy") == "execution_only":
            warnings.append("execution_only_no_semantic_sample_verification:" + row["problem_id"])
            if row.get("formal_fallback") != "enabled" or row["requires_llm_checker"]:
                blockers.append("execution_only_path_invalid:" + row["problem_id"])
        elif not row["checker_live_coverage"]:
            warnings.append("checker_live_validation_required:" + row["problem_id"])
        if checker_v2 and row["requires_llm_checker"] and row.get("checker_smoke_live_path_status") != "PASS":
            blockers.append("required_checker_live_path_not_passed:" + row["problem_id"])
        if row["pilot_seen"]:
            warnings.append("pilot_seen_problem:" + row["problem_id"])
    if not pilot:
        blockers.append("pilot_validated_evidence_unavailable" + (":" + pilot_error if pilot_error else ""))
    for model in report["models"]:
        if model["verified"]["model_available"] is not True:
            blockers.append("model_availability_unresolved:" + model["profile"])
    diff = freeze_diff(report, config, pilot) if pilot and report.get("fingerprint") else {
        "pilot_fingerprint": None, "main_fingerprint": (report.get("fingerprint") or {}).get("sha256"), "items": []}
    shared = {"harness_config_hash", "prompt_bundle_hash", "formal_submission_dedup_policy", "checker_policy_version",
              "feedback_policy_version", "feedback_policy_hash", "oj_endpoint_fingerprint", "condition_routes", "conditions", "agent_source_hash",
              "sample_gate_global_policy", "http_timeout", "poll_interval", "deadline", "send_custom_run_code_alias"}
    for item in diff["items"]:
        item["authorized_checker_change"] = item["field"] in approved and item["status"] != "same"
        if (item["field"] in shared or item["field"].startswith(("model:", "shared_checker_spec:"))) and item["status"] != "same" and item["field"] not in approved:
            blockers.append("shared_freeze_differs_from_pilot:" + item["field"])
    if pilot and not pilot["validation"].get("dedup", {}).get("live_dedup_hit_observed"):
        warnings.append("no_natural_live_dedup_hit")
    plan = []
    for entry in report["execution"]["execution_order"]:
        p = config.problems.index(entry["problem_id"])
        s = next(i for i, strategy in enumerate(config.strategies) if strategy.name == entry["condition"])
        plan.append({**entry, "task_id": f"{main_experiment_id}-s{s+1}-p{p+1}-r{entry['repetition']}"})
    if (len({entry["task_id"] for entry in plan}) != 60
            or pilot and set(pilot["task_ids"]) & {entry["task_id"] for entry in plan}):
        blockers.append("main_task_identity_not_independent")
    if any((Path(workspace_root) / entry["task_id"]).exists() for entry in plan):
        blockers.append("main_task_workspace_already_exists")
    blockers, warnings = sorted(set(blockers)), sorted(set(warnings))
    gate = "MAIN_V1_BLOCKED" if blockers else "MAIN_V1_READY_WITH_WARNINGS" if warnings else "MAIN_V1_READY"
    report.update(status="BLOCKED" if blockers else "READY_WITH_WARNINGS" if warnings else "READY",
                  blockers=blockers, warnings=warnings)
    report["main_final_preflight"] = {"policy_version": MAIN_RECEIPT_POLICY, "gate": gate,
        "result_schema_version": "main_task_results_v1",
        "main_experiment_id": main_experiment_id, "pilot_run_id": pilot["run_id"] if pilot else None,
        "pilot_evidence_hashes": pilot["file_hashes"] if pilot else {}, "freeze_diff": diff,
        "checker_change_scope": scope, "checker_smoke_evidence_hashes": smoke["file_hashes"] if smoke else {},
        "sample_policy_version": (config.sample_checking or {}).get("version"),
        "llm_checker_critical_path": not checker_v3 and matrix["requires_llm_checker"] > 0,
        "checker_smoke_required": checker_v2,
        "checker_smoke_id": smoke["summary"]["smoke_id"] if smoke else None,
        "checker_smoke_gate": smoke["summary"]["gate"] if smoke else None,
        "checker_matrix": matrix, "planned_tasks": plan,
        "pilot_seen_problems": [row["problem_id"] for row in matrix["problems"] if row["pilot_seen"]],
        "pilot_unseen_problems": [row["problem_id"] for row in matrix["problems"] if not row["pilot_seen"]],
        "pilot_observed": {"actual_models": pilot["receipt"].get("actual_models"),
            "accepted_requested_output": "observed accepted in pilot; not a provider maximum",
            "cost": pilot["receipt"].get("cost"), "transport": pilot["validation"].get("provider_transport"),
            "sampling": pilot["validation"].get("model")} if pilot else None,
        "budget_summary": {**report["cost_boundary"], "task_allocations": len(plan),
            "maximum_allocated_budget_cny": len(plan) * config.harness.max_cost_cny,
            "configured_prices": {model["profile"]: {key: model["configured"][key]
                for key in ("input_price", "output_price", "currency", "price_unit")} for model in report["models"]},
            "verified_prices": {model["profile"]: {key: model["verified"][key]
                for key in ("input_price", "output_price", "currency")} for model in report["models"]},
            "reservation_rule": "Configured input reserve + requested output, per call; conservative reservations are not refunded.",
            "expected_spend_cny": None, "provider_reported_cost": None, "billed_cost": None,
            "historical_pilot_estimated_cost_cny": pilot["receipt"]["cost"]["estimated_cost_cny"] if pilot else None},
        "execution_authorized": False}
    return report


def preflight_content_hash(report):
    return fingerprint({key: value for key, value in report.items()
                        if key not in {"artifact_hashes", "preflight_report_hash"}})


def blocked_main_preflight(report, *, main_experiment_id, pilot_run_id):
    """Invalid local config still produces a final-main receipt, never a partial READY."""
    report["main_final_preflight"] = {"policy_version": MAIN_RECEIPT_POLICY, "gate": "MAIN_V1_BLOCKED",
        "main_experiment_id": main_experiment_id, "pilot_run_id": pilot_run_id, "pilot_evidence_hashes": {},
        "freeze_diff": {"pilot_fingerprint": None, "main_fingerprint": None, "items": []},
        "checker_matrix": checker_matrix([]), "planned_tasks": [], "pilot_seen_problems": [],
        "pilot_unseen_problems": [], "pilot_observed": None, "budget_summary": {}, "execution_authorized": False}
    return report


def final_receipt(report):
    main = report["main_final_preflight"]
    components = (report.get("fingerprint") or {}).get("components", {})
    feedback = report["feedback"]
    return {"schema_version": MAIN_RECEIPT_POLICY, "gate": main["gate"],
        "preflight_id": report["preflight_id"], "main_experiment_id": main["main_experiment_id"],
        "git_sha": components.get("git_commit_sha"), "git_dirty": components.get("git_dirty"),
        "git_diff_hash": components.get("git_diff_hash"), "fingerprint": (report.get("fingerprint") or {}).get("sha256"),
        "preflight_content_hash": preflight_content_hash(report),
        "problem_count": len(main["checker_matrix"]["problems"]), "condition_count": len(report["condition_routes"]),
        "task_count": report["task_count"], **{key: components.get(key) for key in ("problem_list_hash", "execution_seed",
            "execution_order_hash", "prompt_bundle_hash", "model_snapshot_hash", "checker_policy_version", "feedback_policy_version")},
        "dedup_policy_version": components.get("formal_submission_dedup_policy", {}).get("version"),
        "checker_implementation_hash": components.get("checker_implementation_hash"),
        "checker_contract_version": components.get("checker_contract_version"),
        "checker_prompt_version": components.get("checker_prompt_version"),
        "checker_smoke_id": main.get("checker_smoke_id"), "checker_smoke_gate": main.get("checker_smoke_gate"),
        "feedback_actual": feedback["actual_feedback_mode"], "feedback_effective": feedback["effective_feedback_mode"],
        "blockers": report["blockers"], "warnings": report["warnings"], "created_at": report["created_at"],
        "execution_authorized": False}


def main_artifacts(report):
    main = report["main_final_preflight"]
    return {"final-receipt.json": final_receipt(report), "freeze-diff.json": main["freeze_diff"],
            "checker-matrix.json": main["checker_matrix"], "budget-summary.json": main["budget_summary"]}


def check_main_receipt(report, workspace_root, experiment_id):
    """Called after local fingerprint check, before creating a batch or any HTTP."""
    try:
        path = preflight_directory(workspace_root, report["preflight_id"]) / "final-receipt.json"
        if path.is_symlink():
            raise ValueError("Unsafe receipt")
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if not report.get("main_final_preflight") or receipt != final_receipt(report):
            raise ValueError("Receipt association mismatch")
        if receipt["main_experiment_id"] != experiment_id:
            raise ValueError("Receipt experiment ID mismatch")
        if receipt["gate"] not in MAIN_GATES or receipt["gate"] == "MAIN_V1_BLOCKED" or receipt["blockers"]:
            raise ValueError("Main receipt is BLOCKED")
        evidence = {**report["main_final_preflight"]["pilot_evidence_hashes"],
                    **report["main_final_preflight"].get("checker_smoke_evidence_hashes", {})}
        for relative, expected in evidence.items():
            source = Path(workspace_root) / relative
            if (Path(relative).is_absolute() or ".." in Path(relative).parts
                    or any(part.is_symlink() for part in (source, *source.parents)) or file_hash(source) != expected):
                raise ValueError("Pilot history evidence drift")
        return receipt
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise PreflightError("Main final receipt is missing, BLOCKED or mismatched; use a new preflight/experiment ID") from exc
