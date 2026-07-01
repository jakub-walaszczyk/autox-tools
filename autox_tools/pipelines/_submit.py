"""Pipeline run submission from a JSON config file.

Loads a config with ``pipeline_package``, ``parameters``, ``experiment``,
etc., validates it, applies CLI overrides, and submits via the KFP client.
Optionally auto-watches the run after submission.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from autox_tools._output import print_json

_RUN_CONFIG_REQUIRED_KEYS = {"pipeline_package"}


def _load_run_config(config_path: str) -> dict[str, Any]:
    """Load and validate a pipeline run config from a JSON file.

    Resolves ``pipeline_package`` relative to the config file's parent
    directory when the path is not absolute.  Exits on validation errors.
    """
    if not os.path.isfile(config_path):
        sys.exit(f"Config file not found: {config_path}")

    try:
        with open(config_path) as f:
            config = json.load(f)
    except json.JSONDecodeError as exc:
        sys.exit(f"Invalid JSON in {config_path}: {exc}")

    if not isinstance(config, dict):
        sys.exit(f"Config must be a JSON object, got {type(config).__name__}.")

    missing = _RUN_CONFIG_REQUIRED_KEYS - config.keys()
    if missing:
        sys.exit(f"Missing required config keys: {', '.join(sorted(missing))}")

    pipeline_path = config["pipeline_package"]
    if not os.path.isabs(pipeline_path):
        config_dir = os.path.dirname(os.path.abspath(config_path))
        pipeline_path = os.path.join(config_dir, pipeline_path)
    config["pipeline_package"] = os.path.normpath(pipeline_path)

    if not os.path.isfile(config["pipeline_package"]):
        sys.exit(f"Pipeline package not found: {config['pipeline_package']}")

    params = config.get("parameters")
    if params is not None and not isinstance(params, dict):
        sys.exit(f"\"parameters\" must be an object, got {type(params).__name__}.")

    return config


def _apply_overrides(config: dict[str, Any], overrides: list[str] | None, run_name: str | None) -> None:
    """Merge CLI ``--override`` and ``--run-name`` into a loaded config in place."""
    if run_name:
        config["run_name"] = run_name

    if not overrides:
        return

    params = config.setdefault("parameters", {})
    for entry in overrides:
        if "=" not in entry:
            sys.exit(f"Invalid --override format (expected KEY=VALUE): {entry}")
        key, value = entry.split("=", 1)
        params[key] = value


def cmd_run(kfp_client: Any, args: argparse.Namespace, **_: Any) -> None:
    """Submit a pipeline run from a JSON config file."""
    from autox_tools.pipelines.cli import cmd_watch

    config = _load_run_config(args.config)
    _apply_overrides(config, args.override, args.run_name)

    pipeline_file = config["pipeline_package"]
    experiment_name = config.get("experiment", "Default")
    run_name = config.get("run_name")
    parameters = config.get("parameters", {})
    service_account = config.get("service_account")

    if args.dry_run:
        summary = {
            "pipeline_package": pipeline_file,
            "experiment": experiment_name,
            "run_name": run_name,
            "parameters": parameters,
            "service_account": service_account,
            "namespace": os.getenv("RHOAI_PROJECT_NAME", ""),
        }
        if args.json:
            print_json(summary)
        else:
            print("Dry run — the following would be submitted:\n")
            print(f"  Pipeline : {pipeline_file}")
            print(f"  Experiment: {experiment_name}")
            if run_name:
                print(f"  Run name : {run_name}")
            if service_account:
                print(f"  SA       : {service_account}")
            if parameters:
                print("  Parameters:")
                max_key = max(len(k) for k in parameters)
                for k, v in sorted(parameters.items()):
                    print(f"    {k:<{max_key}}  {v}")
        return

    if kfp_client is None:
        sys.exit("KFP client is required for submission (remove --dry-run or set credentials).")

    namespace = os.getenv("RHOAI_PROJECT_NAME", "")

    submit_kwargs: dict[str, Any] = {
        "pipeline_file": pipeline_file,
        "arguments": parameters or None,
        "experiment_name": experiment_name,
        "namespace": namespace or None,
    }
    if run_name:
        submit_kwargs["run_name"] = run_name
    if service_account:
        submit_kwargs["service_account"] = service_account

    try:
        run = kfp_client.create_run_from_pipeline_package(**submit_kwargs)
    except FileNotFoundError:
        sys.exit(f"Pipeline package not found: {pipeline_file}")
    except Exception as exc:
        exc_str = str(exc)
        if "403" in exc_str or "Forbidden" in exc_str:
            sys.exit(
                "KFP API returned 403 Forbidden.\n"
                "The token (RHOAI_TOKEN) may lack permission to create runs in "
                f"namespace '{namespace}'."
            )
        sys.exit(f"Failed to submit pipeline run: {exc}")

    run_id = getattr(run, "run_id", None) or getattr(run, "id", "")

    if args.json:
        print_json({
            "run_id": str(run_id),
            "experiment": experiment_name,
            "pipeline_package": pipeline_file,
            "parameters": parameters,
        })
    else:
        print(f"Run submitted: {run_id}")
        print(f"  Experiment: {experiment_name}")
        print(f"  Pipeline  : {os.path.basename(pipeline_file)}")

    if args.watch and run_id:
        print()
        watch_args = argparse.Namespace(
            run_id=str(run_id), interval=10, timeout=3600, json=args.json,
        )
        cmd_watch(kfp_client, watch_args)
