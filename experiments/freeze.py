"""Content snapshots and local drift checks. No model/OJ execution imports."""
from __future__ import annotations

import hashlib
import inspect
import subprocess
from pathlib import Path

from agent.core.checker import SAMPLE_POLICY
from agent.core.formal_dedup import FORMAL_DEDUP_POLICY
from agent.core.context import ContextBuilder
from agent.core.generated_checker import INSTRUCTIONS, checker_messages
from agent.execution import fingerprint
from agent.models.types import AgentRole
from agent.oj_client.feedback import VERDICT_ONLY_POLICY
from .order import execution_order


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def resolve_git_root(start):
    try:
        return Path(subprocess.check_output(["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL).decode().strip()).resolve()
    except (OSError, subprocess.CalledProcessError):
        return Path(start).resolve()


def runtime_source_hash():
    # Also identify loaded package sources when a console entry point runs from
    # an installed wheel rather than the repository's editable installation.
    base = Path(__file__).resolve().parents[1]
    return fingerprint({str(path.relative_to(base)): file_hash(path)
                        for package in ("agent", "experiments")
                        for path in sorted((base / package).rglob("*.py"))})


def prompt_snapshot():
    problem = {"problem_id": "{problem_id}", "title": "{title}", "statement": "{statement}",
               "input_specification": "{input_specification}", "output_specification": "{output_specification}",
               "notes": "{notes}", "limits": {"time_ms": "{time_ms}", "memory_mb": "{memory_mb}"},
               "samples": [{"input": "{sample_input}", "output": "{sample_output}"}]}
    builder = ContextBuilder()
    assembly = {"context_builder_source": inspect.getsource(ContextBuilder),
                "checker_messages_source": inspect.getsource(checker_messages),
                "checker_instructions": INSTRUCTIONS}
    templates = {"system": {"version": "context_v1", "text": builder.SYSTEM}}
    for role in (AgentRole.PLAN, AgentRole.CODE, AgentRole.DEBUG):
        messages = builder.build(problem=problem, role=role, plan="{plan}",
            current_solution="{current_solution}", feedback={"feedback": "{feedback}"},
            recent_history=["{recent_history}"])
        templates[role.value] = {"version": "context_v1", "messages": [
            {"role": message.role, "content": message.content} for message in messages]}
    templates["sample_checker_generation"] = {"version": "llm_checker_prompt_v1", "messages": [
        {"role": message.role, "content": message.content} for message in checker_messages(builder, problem)]}
    prompts = [{"prompt_name": name, "prompt_version": value["version"],
                "sha256": fingerprint({"template": value, "assembly": assembly}), **value}
               for name, value in templates.items()]
    bundle = {"schema_version": "prompt_bundle_v1", "prompts": prompts, "assembly": assembly,
              "assembly_policy": {"feedback_max_characters": 8000, "recent_history_limit": 5,
                  "checker_generation_role": "CODE", "checker_status": "llm_generated_unverified"}}
    return {**bundle, "prompt_bundle_hash": fingerprint(bundle)}


def validate_prompts(snapshot):
    required = {"system", "PLAN", "CODE", "DEBUG", "sample_checker_generation"}
    if (not isinstance(snapshot, dict) or not isinstance(snapshot.get("prompts"), list)
            or {entry.get("prompt_name") for entry in snapshot["prompts"]} != required
            or not snapshot.get("assembly") or not snapshot.get("prompt_bundle_hash")):
        raise ValueError("Missing prompt snapshot")
    if any(not entry.get("sha256") or not entry.get("prompt_version")
           or not (entry.get("text") or entry.get("messages")) for entry in snapshot["prompts"]):
        raise ValueError("Missing prompt content/hash/version")


def git_snapshot(root):
    root = resolve_git_root(root)
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL)
    try:
        sha = git("rev-parse", "HEAD").decode().strip()
        status = git("status", "--porcelain=v1", "-z")
        diffs = [git("diff", "--binary"), git("diff", "--cached", "--binary")]
        untracked = git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
        entries = []
        for name in sorted(value for value in untracked if value):
            relative = name.decode("utf-8", "surrogateescape")
            path = root / relative
            if path.is_symlink():
                raise ValueError("Cannot freeze an untracked symlink")
            entries.append([relative, file_hash(path)])
        diff_hash = fingerprint({"diffs": [hashlib.sha256(value).hexdigest() for value in diffs],
                                 "status_hash": hashlib.sha256(status).hexdigest(), "untracked": entries})
        return {"git_commit_sha": sha, "git_dirty": bool(status), "git_diff_hash": diff_hash}
    except (OSError, subprocess.CalledProcessError):
        return {"git_commit_sha": None, "git_dirty": None, "git_diff_hash": None}


def frozen_fingerprint(config, models, condition_routes, oj_endpoint, *, config_path=None,
                       git_root=None, seed=None, prompts=None):
    prompts = prompt_snapshot() if prompts is None else prompts
    order = execution_order(config, seed)
    configured = [{key: model[key] for key in ("profile", "provider", "model_id", "endpoint_fingerprint", "configured")}
                  for model in models]
    git = git_snapshot(git_root or (Path(config_path).resolve().parent if config_path else Path.cwd()))
    components = {**git,
        "experiment_config_hash": fingerprint(config.as_dict()),
        "experiment_config_file_hash": file_hash(config_path) if config_path else None,
        "problem_list_hash": fingerprint(config.problems),
        "model_snapshot_hash": fingerprint({"models": configured, "condition_routes": condition_routes}),
        "model_config_file_hash": file_hash(config.model_config),
        "harness_config_hash": fingerprint(config.as_dict()["harness"]),
        "prompt_bundle_hash": prompts["prompt_bundle_hash"],
        "runtime_source_hash": runtime_source_hash(),
        "formal_submission_dedup_policy": dict(FORMAL_DEDUP_POLICY),
        "checker_policy_version": SAMPLE_POLICY,
        "checker_policy_hash": fingerprint(config.sample_checking),
        "feedback_policy_version": VERDICT_ONLY_POLICY if config.expected_feedback_mode == "verdict_only" else "full_native_v1",
        "feedback_policy_hash": fingerprint([config.expected_feedback_mode, config.require_feedback_mode, VERDICT_ONLY_POLICY]),
        "execution_order_version": order["version"], "execution_seed": order["execution_seed"],
        "execution_order_hash": order["execution_order_hash"],
        "oj_endpoint_fingerprint": fingerprint(oj_endpoint),
        "provider_endpoint_fingerprint": {model["profile"]: model["endpoint_fingerprint"] for model in models}}
    return {"schema_version": "experiment_fingerprint_v1", "components": components,
            "sha256": fingerprint(components)}
