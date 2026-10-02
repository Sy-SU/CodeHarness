"""Optional, loopback-only server entry point."""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from typing import Optional, Sequence

from .config import DashboardSettings


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Local CodeHarness task launcher and Dashboard")
    parser.add_argument("--workspace-root", type=Path, default=Path("workspace"))
    parser.add_argument("--host", default="127.0.0.1", help="Loopback host only (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--model-config", type=Path, help="Model YAML for execution/inspection; otherwise MODEL_CONFIG")
    parser.add_argument("--harness-config", type=Path, default=Path("config/harness.yaml"))
    parser.add_argument("--sample-config", type=Path, help="Server-owned sample checker/gate policy YAML")
    parser.add_argument("--max-cost-cny", type=float,
                        help="Default task budget displayed in the form (otherwise policy default: 1 CNY)")
    parser.add_argument("--feedback-mode", choices=("full", "verdict_only"), default="verdict_only",
                        help="Required Agent feedback mode; server full can be restricted to verdict_only; "
                             "unknown blocks model calls (default: verdict_only)")
    parser.add_argument("--read-only", action="store_true", help="Disable all task launch/resume endpoints")
    parser.add_argument("--http-timeout", type=float, default=15)
    parser.add_argument("--poll-interval", type=float, default=1)
    parser.add_argument("--deadline", type=float, default=120)
    args = parser.parse_args(argv)
    try:
        settings = DashboardSettings.load(args.workspace_root, args.host, args.port, args.model_config)
        settings = replace(settings, enable_launch=not args.read_only, harness_config=args.harness_config,
            http_timeout=args.http_timeout, poll_interval=args.poll_interval, deadline=args.deadline)
        settings = replace(settings, max_cost_cny=args.max_cost_cny, expected_feedback_mode=args.feedback_mode,
                           sample_config=args.sample_config)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        import uvicorn
        from .app import create_app
    except ImportError:
        parser.error("Dashboard dependencies are optional. Install with: uv sync --extra dashboard")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
