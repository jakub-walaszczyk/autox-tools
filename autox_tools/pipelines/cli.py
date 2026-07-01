"""CLI entry point for KFP pipeline management commands.

Usage::

    uv run pipelines status <run-id>
    uv run pipelines list [--limit N] [--experiment EXP] [--state STATE]
    uv run pipelines watch <run-id> [--interval SECS] [--timeout SECS]
    uv run pipelines logs <run-id> [--tail N] [--all]
    uv run pipelines artifacts <run-id> [--download DIR]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

from autox_tools._output import format_duration, print_json
from autox_tools.pipelines._kfp import (
    connect as kfp_connect,
)
from autox_tools.pipelines._kfp import (
    extract_tasks,
    get_pipeline_name,
    get_run_state,
    is_terminal,
)

_MAX_ERROR_LEN = 200


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_status(kfp_client: Any, args: argparse.Namespace, **_: Any) -> None:
    """Get run status with task-level details."""
    run = kfp_client.get_run(args.run_id)
    run_obj = getattr(run, "run", run)

    state = get_run_state(run_obj)
    pipeline_name = get_pipeline_name(run_obj)

    start_time = getattr(run_obj, "created_at", None)
    end_time = getattr(run_obj, "finished_at", None)
    error = getattr(run_obj, "error", None) or ""

    run_details = getattr(run, "run_details", None) or run_obj
    tasks = extract_tasks(run_details, pipeline_name)

    if args.json:
        print_json({
            "run_id": args.run_id,
            "state": state,
            "pipeline_name": pipeline_name,
            "start_time": str(start_time) if start_time else None,
            "end_time": str(end_time) if end_time else None,
            "error": str(error) if error else None,
            "tasks": tasks,
        })
        return

    print(f"Run ID   : {args.run_id}")
    print(f"State    : {state}")
    if pipeline_name:
        print(f"Pipeline : {pipeline_name}")
    if start_time:
        print(f"Started  : {start_time}")
    if end_time:
        print(f"Finished : {end_time}")
    if error:
        display_error = str(error)[:_MAX_ERROR_LEN]
        if len(str(error)) > _MAX_ERROR_LEN:
            display_error += "..."
        print(f"Error    : {display_error}")

    if tasks:
        print(f"\nTasks ({len(tasks)}):")
        max_name = max(len(t["name"]) for t in tasks)
        for t in tasks:
            err_suffix = f"  -- {t['error'][:80]}..." if t["error"] else ""
            print(f"  {t['name']:<{max_name}}  {t['state']}{err_suffix}")


def cmd_list(kfp_client: Any, args: argparse.Namespace, **_: Any) -> None:
    """List recent pipeline runs."""
    experiment_id = None
    if args.experiment:
        try:
            exp = kfp_client.get_experiment(experiment_name=args.experiment)
            experiment_id = exp.experiment_id
        except Exception:
            sys.exit(f"Experiment '{args.experiment}' not found.")

    kwargs: dict[str, Any] = {"page_size": args.limit, "sort_by": "created_at desc"}
    if experiment_id:
        kwargs["experiment_id"] = experiment_id

    response = kfp_client.list_runs(**kwargs)
    runs = getattr(response, "runs", None) or []

    if args.state:
        target = args.state.lower()
        runs = [r for r in runs if get_run_state(r).lower() == target]

    if args.json:
        rows = []
        for r in runs:
            run_id = getattr(r, "run_id", None) or getattr(r, "id", "")
            name = getattr(r, "display_name", None) or getattr(r, "name", "")
            state = get_run_state(r)
            created = getattr(r, "created_at", None)
            finished = getattr(r, "finished_at", None)
            duration = None
            if created and finished:
                duration = format_duration((finished - created).total_seconds())
            rows.append({
                "run_id": str(run_id),
                "name": name,
                "state": state,
                "created": str(created) if created else None,
                "duration": duration,
            })
        print_json(rows)
        return

    if not runs:
        print("No runs found.")
        return

    entries: list[tuple[str, str, str, str, str]] = []
    for r in runs:
        run_id = str(getattr(r, "run_id", None) or getattr(r, "id", ""))
        name = getattr(r, "display_name", None) or getattr(r, "name", "")
        state = get_run_state(r)
        created = getattr(r, "created_at", None)
        finished = getattr(r, "finished_at", None)
        created_str = str(created)[:19] if created else ""
        duration = ""
        if created and finished:
            duration = format_duration((finished - created).total_seconds())
        entries.append((run_id, str(name), state, created_str, duration))

    col_widths = [
        max(len("Run ID"), max(len(e[0]) for e in entries)),
        max(len("Name"), min(40, max(len(e[1]) for e in entries))),
        max(len("State"), max(len(e[2]) for e in entries)),
        max(len("Created"), max(len(e[3]) for e in entries)),
        max(len("Duration"), max(len(e[4]) for e in entries)),
    ]

    w = col_widths
    print(f"  {'Run ID':<{w[0]}}  {'Name':<{w[1]}}  {'State':<{w[2]}}  {'Created':<{w[3]}}  {'Duration':<{w[4]}}")
    print(f"  {'─' * w[0]}  {'─' * w[1]}  {'─' * w[2]}  {'─' * w[3]}  {'─' * w[4]}")

    for run_id, name, state, created_str, duration in entries:
        truncated_name = name[:col_widths[1]]
        print(
            f"  {run_id:<{col_widths[0]}}  {truncated_name:<{col_widths[1]}}  "
            f"{state:<{col_widths[2]}}  {created_str:<{col_widths[3]}}  {duration:<{col_widths[4]}}"
        )

    print(f"\n  {len(entries)} run(s)")


def _print_run_summary(
    run_id: str,
    state: str,
    elapsed: float,
    tasks: list[dict[str, Any]],
    run_obj: Any,
) -> None:
    """Print a structured completion summary for a finished pipeline run."""
    succeeded = state.lower() == "succeeded"
    icon = "[OK]" if succeeded else "[FAIL]"
    border = "=" * 60

    print(f"\n{border}")
    print(f"  {icon}  Pipeline run finished — {state.upper()}")
    print(border)
    print(f"  Run ID   : {run_id}")
    print(f"  Duration : {format_duration(elapsed)}")

    if tasks:
        print(f"  Tasks    : {len(tasks)}")
        print()
        for t in tasks:
            task_icon = "+" if t["state"].lower() == "succeeded" else "-"
            print(f"    [{task_icon}] {t['name']:<30} {t['state']}")
            if t.get("error"):
                err = t["error"]
                if len(err) > _MAX_ERROR_LEN:
                    err = err[:_MAX_ERROR_LEN] + "..."
                print(f"        Error: {err}")

    error = getattr(run_obj, "error", None)
    if error:
        print(f"\n  Run error: {error}")

    print(border)


def cmd_watch(kfp_client: Any, args: argparse.Namespace, **_: Any) -> None:
    """Live progress monitoring for a pipeline run."""
    run_id = args.run_id
    interval = args.interval
    timeout = args.timeout
    is_tty = sys.stdout.isatty()

    start = time.monotonic()
    prev_line_count = 0

    while True:
        elapsed = time.monotonic() - start
        if elapsed > timeout:
            print(f"\n[pipelines] Timeout after {format_duration(timeout)}.")
            sys.exit(2)

        run = kfp_client.get_run(run_id)
        run_obj = getattr(run, "run", run)
        state = get_run_state(run_obj)
        pipeline_name = get_pipeline_name(run_obj)

        run_details = getattr(run, "run_details", None) or run_obj
        tasks = extract_tasks(run_details, pipeline_name)

        if is_tty and prev_line_count > 0:
            sys.stdout.write(f"\033[{prev_line_count}A\033[J")

        sys.stdout.write(
            f"[pipelines] run={run_id[:12]}... state={state} "
            f"elapsed={format_duration(elapsed)}\n"
        )

        if tasks:
            for t in tasks:
                dots = "." * max(2, 30 - len(t["name"]))
                sys.stdout.write(f"  {t['name']} {dots} {t['state']}\n")

        sys.stdout.flush()
        prev_line_count = 1 + len(tasks)

        if is_terminal(state):
            _print_run_summary(run_id, state, elapsed, tasks, run_obj)
            exit_code = 0 if state.lower() == "succeeded" else 1
            sys.exit(exit_code)

        time.sleep(interval)


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipelines",
        description="Monitor and inspect Kubeflow Pipeline runs on OpenShift AI.",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    sub = parser.add_subparsers(dest="command", required=True)

    # status
    p = sub.add_parser("status", help="Get run status with task details")
    p.add_argument("run_id", help="KFP run ID (UUID)")

    # list
    p = sub.add_parser("list", help="List recent pipeline runs")
    p.add_argument("--limit", "-n", type=int, default=20, help="Max runs to return (default: 20)")
    p.add_argument("--experiment", help="Filter by experiment name")
    p.add_argument("--state", help="Filter by state (succeeded, failed, running, ...)")

    # watch
    p = sub.add_parser("watch", help="Live progress monitoring")
    p.add_argument("run_id", help="KFP run ID (UUID)")
    p.add_argument("--interval", type=int, default=10, help="Poll interval in seconds (default: 10)")
    p.add_argument("--timeout", type=int, default=3600, help="Max wait time in seconds (default: 3600)")

    # logs
    p = sub.add_parser("logs", help="Fetch pod logs for pipeline tasks")
    p.add_argument("run_id", help="KFP run ID (UUID)")
    p.add_argument("--tail", type=int, default=100, help="Number of log lines from the end (default: 100)")
    p.add_argument("--all", action="store_true", help="Show logs for all components, not just failed")

    # artifacts
    p = sub.add_parser("artifacts", help="List S3 artifacts from a run")
    p.add_argument("run_id", help="KFP run ID (UUID)")
    p.add_argument(
        "--component",
        help="Pipeline component name or 'all' (e.g. --component search-space-optimization)",
    )
    p.add_argument("--download", metavar="DIR", help="Download artifacts to directory")

    # run
    p = sub.add_parser("run", help="Submit a pipeline run from a JSON config file")
    p.add_argument("config", help="Path to JSON config file with pipeline_package and parameters")
    p.add_argument("--watch", action="store_true", help="Auto-watch the run after submission")
    p.add_argument("--dry-run", action="store_true", help="Validate and print config without submitting")
    p.add_argument("--override", action="append", metavar="KEY=VALUE", help="Override a pipeline parameter")
    p.add_argument("--run-name", help="Override the run display name from config")

    return parser


def main() -> None:
    parser = _build_parser()

    from autox_tools.config._loader import add_profile_args, resolve
    add_profile_args(parser)

    args = parser.parse_args()
    rhoai_cfg = resolve("rhoai", args)
    artifacts_s3_cfg = resolve("artifacts_s3", args) if args.command == "artifacts" else None

    dry_run = args.command == "run" and args.dry_run
    kfp_client = None if dry_run else kfp_connect(rhoai_cfg)

    k8s_api = None
    if args.command == "logs":
        from autox_tools.pipelines._k8s import connect as k8s_connect
        k8s_api = k8s_connect(cfg=rhoai_cfg) if rhoai_cfg is not None else k8s_connect(os.getenv("RHOAI_KFP_URL"))

    from autox_tools.pipelines._artifacts import cmd_artifacts
    from autox_tools.pipelines._logs import cmd_logs
    from autox_tools.pipelines._submit import cmd_run

    commands: dict[str, Any] = {
        "status": cmd_status,
        "list": cmd_list,
        "watch": cmd_watch,
        "logs": cmd_logs,
        "artifacts": cmd_artifacts,
        "run": cmd_run,
    }
    commands[args.command](kfp_client, args, k8s_api=k8s_api, artifacts_s3_cfg=artifacts_s3_cfg)


if __name__ == "__main__":
    main()
