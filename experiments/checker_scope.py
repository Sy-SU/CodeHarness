"""Audit the explicitly authorized checker change against the Pilot source.

An approved change is narrow: unchanged files and algorithm functions are still
compared. The receipt keeps both hashes and the exact changed file list.
"""
from __future__ import annotations

import ast
import io
import subprocess
import tarfile
from pathlib import Path

from agent.execution import fingerprint
from agent.core.checker import SAMPLE_POLICY, GENERATED_SAMPLE_POLICY
from .checker_smoke import CHECKER_FILES
from .freeze import resolve_git_root

# Approved checker-v2 prompt, frozen before the v3 policy change. Main does not
# invoke it, but this authorization does not include tuning any model prompt.
AUTHORIZED_V2_PROMPT_HASH = "ff8aaa94943d51a1d59f64bd074a4d55fc0e2c75018ed8177c4b567fd5a28412"


def _sources(root, commit=None):
    if not commit:
        return {str(p.relative_to(root)): p.read_text() for p in (root / "agent").rglob("*.py")}
    archive = subprocess.check_output(["git", "-C", str(resolve_git_root(root)), "archive", commit, "agent"])
    with tarfile.open(fileobj=io.BytesIO(archive)) as handle:
        return {p.name: handle.extractfile(p).read().decode() for p in handle.getmembers()
                if p.isfile() and p.name.endswith(".py")}


def _units(source):
    values = {}
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            if isinstance(node, ast.ClassDef):
                for member in node.body:
                    if isinstance(member, ast.FunctionDef):
                        values[node.name + "." + member.name] = ast.dump(member, include_attributes=False)
            else:
                values[node.name] = ast.dump(node, include_attributes=False)
        elif isinstance(node, ast.Assign):
            for name in node.targets:
                if isinstance(name, ast.Name):
                    values[name.id] = ast.dump(node, include_attributes=False)
    return values


def checker_change_scope(root, pilot_commit, *, version=GENERATED_SAMPLE_POLICY):
    root = Path(root)
    before, after = _sources(root, pilot_commit), _sources(root)
    changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
    problems = ["outside_checker_scope:" + name for name in changed if name not in CHECKER_FILES]
    # Protect the algorithm state machine and all contestant prompt/parser/code,
    # submissions/dedup/review/budget/provider/OJ/tool/workspace operations.
    permitted = {
        "agent/core/harness.py": {"HarnessLoop._snapshot_config", "HarnessLoop._resolve_checker", "HarnessLoop._samples",
            "HarnessLoop._llm_check", "HarnessLoop._sample_unverifiable", "HarnessLoop._prepare"},
        "agent/core/agent.py": set(), "agent/execution.py": {"ExecutionService.snapshot"},
        "agent/workspace/task.py": set(),
        "agent/core/checker.py": {"SAMPLE_POLICY", "SampleGatePolicy.__post_init__"},
        "agent/core/generated_checker.py": {"INSTRUCTIONS", "checker_messages"},
        "agent/core/metrics.py": {"recovery_metrics"}}
    protected_before, protected_after = {}, {}
    for file, allowed in permitted.items():
        old, new = _units(before[file]), _units(after[file])
        for name, value in old.items():
            if name not in allowed:
                identity = file + "#" + name
                current = new.get(name)
                if version == SAMPLE_POLICY and identity == "agent/core/harness.py#HarnessLoop._failure":
                    # v3 labels DEBUG's cause; the transition/replan/budget logic
                    # and feedback history remain byte-for-byte equivalent AST.
                    node = next(n for n in ast.walk(ast.parse(after[file]))
                                if isinstance(n, ast.FunctionDef) and n.name == "_failure")
                    class StripFailureReason(ast.NodeTransformer):
                        def visit_Call(self, call):
                            if (isinstance(call.func, ast.Attribute) and call.func.attr == "_failure_reason"
                                    and ast.dump(call) == ast.dump(ast.parse("self._failure_reason(feedback)", mode="eval").body)):
                                return ast.Constant(value="candidate_failed")
                            return self.generic_visit(call)
                    current = ast.dump(StripFailureReason().visit(node), include_attributes=False)
                if version == SAMPLE_POLICY and identity == "agent/core/checker.py#SampleGatePolicy.as_dict":
                    # Legacy serialized policy must keep exactly the v1/v2 keys.
                    from agent.core.checker import SampleGatePolicy
                    expected = {"version": GENERATED_SAMPLE_POLICY, "on_unverifiable": "stop", "overrides": {}, "llm_checker": "submit_on_pass"}
                    if SampleGatePolicy(version=GENERATED_SAMPLE_POLICY).as_dict() == expected:
                        current = value
                protected_before[identity], protected_after[identity] = value, current
                if value != current:
                    problems.append("algorithm_unit_changed:" + identity)
    # recovery_metrics has only added checker-specific branches/counters. Strip
    # those additions and require the original causal recovery function verbatim.
    old_metrics = ast.parse(before["agent/core/metrics.py"])
    new_metrics = ast.parse(after["agent/core/metrics.py"])
    class StripCheckerMetrics(ast.NodeTransformer):
        def visit_Assign(self, node):
            additions = {"checker_generation", "v2"}
            if version == SAMPLE_POLICY:
                additions |= {"v3_samples", "v3", "output_unverifiable"}
            if any(isinstance(t, ast.Name) and t.id in additions
                   for target in node.targets for t in ast.walk(target)):
                return None
            return self.generic_visit(node)
        def visit_Expr(self, node):
            if (isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
                    and isinstance(node.value.func.value, ast.Name) and node.value.func.value.id == "metrics"
                    and node.value.func.attr == "update"):
                return None
            return self.generic_visit(node)
        def visit_If(self, node):
            text = ast.dump(node.test)
            if version == SAMPLE_POLICY and "'sample_policy'" in text and "'sample_execution_failure'" in text:
                return self.visit(node.orelse[0]) if len(node.orelse) == 1 else []
            if any("'" + value + "'" in text for value in ("CHECKER_GENERATION_RESULT", "CHECKER_SANITY_RESULT",
                                                         "CHECKER_DECISION", "CHECKER_EXECUTION_RESULT", "UNVERIFIED_CHECKER_STOP")):
                return self.visit(node.orelse[0]) if len(node.orelse) == 1 else []
            return self.generic_visit(node)
        def visit_BoolOp(self, node):
            self.generic_visit(node)
            if version == SAMPLE_POLICY and isinstance(node.op, ast.And):
                addition = ast.dump(ast.parse("p.get('status') != 'sample_execution_failure'", mode="eval").body)
                if len(node.values) == 2 and ast.dump(node.values[1]) == addition:
                    return node.values[0]
            return node
        def visit_Tuple(self, node):
            self.generic_visit(node)
            if version == SAMPLE_POLICY:
                node.elts = [n for n in node.elts if not isinstance(n, ast.Constant) or n.value != "sample_execution_recovery"]
            return node
    old_fn = next(n for n in old_metrics.body if isinstance(n, ast.FunctionDef) and n.name == "recovery_metrics")
    new_fn = next(n for n in new_metrics.body if isinstance(n, ast.FunctionDef) and n.name == "recovery_metrics")
    new_fn = StripCheckerMetrics().visit(new_fn)
    old_dump, new_dump = ast.dump(old_fn, include_attributes=False), ast.dump(new_fn, include_attributes=False)
    protected_before["algorithm_recovery"], protected_after["algorithm_recovery"] = old_dump, new_dump
    if old_dump != new_dump:
        problems.append("algorithm_recovery_changed")
    prompt_unchanged = None
    if version == SAMPLE_POLICY:
        from agent.core.generated_checker import INSTRUCTIONS
        prompt_node = next(n for n in ast.parse(after["agent/core/generated_checker.py"]).body
                           if isinstance(n, ast.FunctionDef) and n.name == "checker_messages")
        prompt_unchanged = fingerprint({"instructions": INSTRUCTIONS,
            "checker_messages_ast": ast.dump(prompt_node, include_attributes=False)}) == AUTHORIZED_V2_PROMPT_HASH
        if not prompt_unchanged:
            problems.append("checker_generation_prompt_changed")
    # Enum/dataclass additions are machine metadata; existing fields must match.
    for file, classes, allowed_new in (
        ("agent/core/agent.py", {"AgentTerminalStatus"}, {"CHECKER_REJECTED_UNVERIFIED", "CHECKER_GENERATION_FAILED",
            "CHECKER_EXECUTION_FAILED", "CHECKER_SANITY_FAILED", "CHECKER_RESULT_UNKNOWN"}),
        ("agent/workspace/task.py", {"EventType", "TaskState"}, {"CHECKER_GENERATION_RESULT", "CHECKER_SANITY_RESULT",
            "CHECKER_EXECUTION_RESULT", "CHECKER_DECISION", "UNVERIFIED_CHECKER_STOP", "checker_schema_version", "checker_observation", "sample_check_status"})):
        def fields(source, cls):
            node = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == cls)
            return {n.targets[0].id if isinstance(n, ast.Assign) else n.target.id: ast.dump(n, include_attributes=False)
                    for n in node.body if isinstance(n, (ast.Assign, ast.AnnAssign))}
        for cls in classes:
            old, new = fields(before[file], cls), fields(after[file], cls)
            if any(new.get(k) != v for k, v in old.items()) or not set(new).difference(old) <= allowed_new:
                problems.append("existing_state_fields_changed:" + cls)
    return {"policy_version": "authorized_sample_check_v3" if version == SAMPLE_POLICY else "authorized_checker_fix_v2", "pilot_git_sha": pilot_commit,
        "changed_agent_files": changed, "allowed_agent_files": list(CHECKER_FILES),
        "protected_algorithm_hash_before": fingerprint(protected_before),
        "protected_algorithm_hash_after": fingerprint(protected_after), "blockers": sorted(set(problems)),
        "checker_generation_prompt_unchanged": prompt_unchanged,
        "note": "Authorized versioned sample policy and cause/metric classification; algorithm recovery association, prompts, budgets and formal Judge handling stay frozen."}
