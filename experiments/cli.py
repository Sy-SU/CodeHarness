"""Explicitly confirmed live experiment execution and recovery."""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import yaml

from dotenv import load_dotenv

from agent.config import ClientSettings
from agent.execution import ExecutionService
from .config import ExperimentConfig
from .runner import ExperimentRunner
from .preflight import PreflightError
from .contest import ContestRequest, ContestRunner
from agent.core.harness import HarnessPolicy
from agent.execution import RunRequest
from agent.oj_client.client import OJClientError


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bounded sequential CodeHarness experiments")
    parser.add_argument("command", choices=("run", "resume", "contest", "contest-resume", "prepare", "preflight", "contest-performance"))
    parser.add_argument("config", type=Path, metavar="CONFIG_OR_CONTEST_ID")
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--preflight-id", help="Frozen preflight ID required for prepared formal/pilot runs")
    parser.add_argument("--execution-seed", type=int, help="Preflight order seed; default: 20261002")
    parser.add_argument("--offline", action="store_true", help="Preflight without GETs; feedback remains unknown/BLOCKED")
    parser.add_argument("--workspace-root", type=Path, default=Path("workspace"))
    parser.add_argument("--max-cost-cny", type=float,
                        help="Override each task's CNY limit; overall YAML batch cap remains unchanged")
    parser.add_argument("--confirm-model-call", action="store_true")
    parser.add_argument("--confirm-submit", action="store_true")
    parser.add_argument("--model-config", type=Path, help="Model config for contest commands")
    parser.add_argument("--harness-config", type=Path, default=Path("config/harness.yaml"))
    parser.add_argument("--mode", choices=("harness-loop", "code-only"), default="harness-loop")
    parser.add_argument("--profile", choices=("fast", "standard", "strong", "max"),
                        help="Fixed contest model; omitted harness-loop uses configured Role mapping")
    parser.add_argument("--max-total-cost-cny", type=float, default=1.0,
                        help="Whole contest budget (default: 1 CNY); independent per-problem cap still applies")
    parser.add_argument("--max-llm-calls", type=int, help="Per-problem model-call cap for contest commands")
    parser.add_argument("--max-submissions", type=int, help="Per-problem formal POST cap for contest commands (1-10)")
    parser.add_argument("--output", type=Path, help="New file for an offline prepared plan (never overwrite)")
    parser.add_argument("--freeze-model-config", action="store_true", help="Also snapshot configured models during offline preparation")
    parser.add_argument("--sample-unverifiable", choices=("stop", "submit"), help="Freeze new contest sample gate behavior")
    parser.add_argument("--llm-checker", choices=("disabled", "advisory", "submit_on_pass"), help="New contest generated-checker policy")
    args = parser.parse_args(argv)
    executing = args.command in {"run", "resume", "contest", "contest-resume"}
    if executing and (not args.confirm_model_call or not args.confirm_submit):
        parser.error("Both --confirm-model-call and --confirm-submit are required")
    if args.command != "preflight" and (args.execution_seed is not None or args.offline):
        parser.error("--execution-seed and --offline apply only to preflight")
    if args.command not in {"run", "resume"} and args.preflight_id is not None:
        parser.error("--preflight-id applies only to run/resume")
    if args.command != "prepare" and (args.output is not None or args.freeze_model_config):
        parser.error("--output and --freeze-model-config apply only to prepare")
    if args.command not in {"contest", "contest-resume"} and (
            args.sample_unverifiable is not None or args.llm_checker is not None):
        parser.error("Sample gate options apply only to contest/contest-resume; use YAML for experiments")
    if args.command not in {"contest", "contest-resume"} and (
            args.max_llm_calls is not None or args.max_submissions is not None
            or args.model_config is not None or args.profile is not None
            or args.max_total_cost_cny != 1.0 or args.mode != "harness-loop"
            or args.harness_config != Path("config/harness.yaml")):
        parser.error("Contest options apply only to contest/contest-resume; use YAML for experiments")
    if args.command in {"prepare", "preflight", "contest-performance"} and args.max_cost_cny is not None:
        parser.error("Use YAML budgets for preparation; Performance refresh has no Agent budget")
    if args.command == "preflight":
        from .preflight import build_preflight, blocked_preflight, preflight_directory, save_preflight
        # Reject an existing ID before performing even read-only remote requests.
        try:
            if preflight_directory(args.workspace_root, args.experiment_id).parent.exists():
                raise FileExistsError("Preflight/experiment ID already exists")
        except (OSError, ValueError) as exc:
            parser.error(f"Preflight storage unavailable ({type(exc).__name__})")
        load_dotenv()
        try:
            config = ExperimentConfig.from_yaml(args.config)
        except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError):
            report = blocked_preflight("experiment_config_invalid", config_path=args.config)
        else:
            try:
                settings = ClientSettings.from_environment(model_config_override=str(config.model_config))
                report = build_preflight(config, settings, config_path=args.config,
                    seed=args.execution_seed, offline=args.offline)
            except (OSError, ValueError, TypeError, KeyError):
                report = blocked_preflight("budget_connection_or_snapshot_config_invalid", config_path=args.config)
        try:
            report = save_preflight(report, args.workspace_root, args.experiment_id)
        except (OSError, ValueError) as exc:
            parser.error(f"Preflight storage unavailable ({type(exc).__name__})")
        print(json.dumps({"preflight_id": args.experiment_id, "status": report["status"],
            "task_count": report["task_count"], "blockers": report["blockers"], "warnings": report["warnings"],
            "reports": str(preflight_directory(args.workspace_root, args.experiment_id))}, ensure_ascii=False, indent=2))
        return 2 if report["status"] == "BLOCKED" else 0
    if args.command == "prepare":
        from .prepare import prepare_experiment
        try:
            config = ExperimentConfig.from_yaml(args.config)
            prepared_service = None
            if args.freeze_model_config:
                load_dotenv()
                prepared_service = ExecutionService(ClientSettings.from_environment(
                    model_config_override=str(config.model_config)), args.workspace_root)
            try:
                plan = prepare_experiment(config, service=prepared_service)
            finally:
                if prepared_service:
                    prepared_service.close()
            if args.output:
                with args.output.open("x", encoding="utf-8") as handle:
                    handle.write(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, TypeError, KeyError) as exc:
            parser.error(f"Preparation unavailable ({type(exc).__name__})")
    load_dotenv()
    if args.command == "contest-performance":
        from .contest import refresh_performance, read_report
        from agent.oj_client.contests import contest_identifier
        try:
            report = read_report(args.workspace_root, args.experiment_id)
            if contest_identifier(str(args.config)) != str(report["contest_id"]):
                raise ValueError("Performance refresh contest does not match the saved run")
            report = refresh_performance(args.workspace_root, args.experiment_id,
                                          ClientSettings.from_environment())
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, TypeError, KeyError) as exc:
            parser.error(f"Performance refresh unavailable ({type(exc).__name__})")
    service = None
    contest_command = args.command.startswith("contest")
    try:
        if contest_command:
            policy = HarnessPolicy.from_yaml(args.harness_config)
            if args.max_cost_cny is not None:
                policy = replace(policy, max_cost_cny=args.max_cost_cny)
            limits = {name: getattr(args, name) for name in ("max_llm_calls", "max_submissions")
                      if getattr(args, name) is not None}
            policy = replace(policy, **limits)
            if args.mode == "code-only":
                if any(value != 1 for value in limits.values()):
                    raise ValueError("code-only contest tasks allow one model call and at most one submission")
                policy = replace(policy, max_llm_calls=1, max_submissions=1)
            request = ContestRequest(str(args.config), RunRequest("contest", args.mode,
                args.profile or ("standard" if args.mode == "code-only" else None), policy=policy),
                args.max_total_cost_cny)
            if args.sample_unverifiable is not None or args.llm_checker is not None:
                from agent.core.checker import SampleGatePolicy
                policy = SampleGatePolicy.from_dict(request.run.sample_checking)
                changes = {}
                if args.sample_unverifiable is not None:
                    changes["on_unverifiable"] = args.sample_unverifiable
                if args.llm_checker is not None:
                    changes["llm_checker"] = args.llm_checker
                request = replace(request, run=replace(request.run,
                    sample_checking=replace(policy, **changes).as_dict()))
            model_path = args.model_config
        else:
            config = ExperimentConfig.from_yaml(args.config)
            if args.max_cost_cny is not None:
                config = replace(config, harness=replace(config.harness, max_cost_cny=args.max_cost_cny))
            model_path = config.model_config
        settings = ClientSettings.from_environment(model_config_override=str(model_path) if model_path else None)
        if not contest_command and (args.preflight_id or config.preparation.get("five_conditions")):
            # Match preflight's used-profile resolution. Legacy/contest creation
            # retains the existing loader/checkpoint behavior.
            from agent.core.policy import ModelPolicy
            from agent.models.registry import ModelRegistry
            from .preflight_models import registry_profiles
            base_policy = ModelPolicy.from_yaml(config.model_config)
            registry = ModelRegistry.from_yaml(config.model_config, required_profiles=registry_profiles(config, base_policy))
            service = ExecutionService(settings, args.workspace_root, registry=registry, base_policy=base_policy)
        else:
            service = ExecutionService(settings, args.workspace_root)
        if contest_command:
            report = ContestRunner(service).run(request, args.experiment_id,
                resume=args.command == "contest-resume")
        else:
            manifest = ExperimentRunner(service).run(config, args.experiment_id, resume=args.command == "resume",
                preflight_id=args.preflight_id)
    except KeyboardInterrupt:
        print(json.dumps({"status": "interrupted", "experiment_id": args.experiment_id}))
        return 130
    except PreflightError as exc:
        parser.error(str(exc))
    except (OSError, ValueError, TypeError, KeyError, OJClientError) as exc:
        # Class only: config parsing errors can otherwise contain arbitrary secret-bearing YAML.
        parser.error(f"Experiment unavailable or invalid ({type(exc).__name__})")
    finally:
        if service is not None:
            service.close()
    if contest_command:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "completed" else 2
    print(json.dumps({"experiment_id": args.experiment_id, "status": manifest["status"],
        "tasks": len(manifest["tasks"]), "solved": sum(bool(t.get("solved")) for t in manifest["tasks"]),
        "budget_committed_cny": manifest["budget_committed_cny"],
        "reports": str(args.workspace_root / ".experiments" / args.experiment_id)}, ensure_ascii=False, indent=2))
    return 0 if manifest["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
