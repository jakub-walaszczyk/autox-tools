"""CLI entry point for KFP pipeline management commands.

Usage::

    uv run pipelines status <run-id>
    uv run pipelines list [--limit N] [--experiment EXP] [--state STATE]
    uv run pipelines watch <run-id> [--interval SECS] [--timeout SECS]
    uv run pipelines logs <run-id> [--task NAME] [--tail N] [--all]
    uv run pipelines artifacts <run-id> [--download DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

from autox_tools.pipelines._filters import is_user_task
from autox_tools.pipelines._kfp import connect as kfp_connect

_TERMINAL_STATES = {"succeeded", "failed", "skipped", "error"}
_MAX_ERROR_LEN = 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_json(data: object) -> None:
    print(json.dumps(data, indent=2, default=str))


def _format_duration(seconds: float) -> str:
    """Format seconds into a human-readable duration string."""
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{secs}s"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h{mins}m{secs}s"


def _extract_tasks(run_details: Any, pipeline_name: str | None = None) -> list[dict[str, Any]]:
    """Extract user-visible tasks from KFP run details."""
    tasks: list[dict[str, Any]] = []

    task_details = getattr(run_details, "task_details", None)
    if not task_details:
        return tasks

    for task in task_details:
        display_name = getattr(task, "display_name", "") or getattr(task, "task_name", "") or ""
        if not display_name or not is_user_task(display_name, pipeline_name):
            continue

        state: Any = getattr(task, "state", None) or getattr(task, "status", "Unknown")
        if hasattr(state, "value"):
            state = state.value
        state_str = str(state)

        error_msg = getattr(task, "error", None) or ""
        child_tasks = getattr(task, "child_tasks", None) or []
        pod_name = getattr(task, "pod_name", None) or ""

        tasks.append({
            "name": display_name,
            "state": state_str,
            "error": str(error_msg) if error_msg else "",
            "child_task_ids": [str(ct) for ct in child_tasks] if child_tasks else [],
            "pod_name": pod_name,
        })

    return tasks


def _get_run_state(run: Any) -> str:
    """Extract the run state string from a KFP run object."""
    state: Any = getattr(run, "state", None) or getattr(run, "status", None)
    if state is None:
        status = getattr(run, "status", None)
        if status is not None and hasattr(status, "state"):
            state = status.state
    if state is not None and hasattr(state, "value"):
        state = state.value
    return str(state) if state else "Unknown"


def _get_pipeline_name(run: Any) -> str | None:
    """Extract the pipeline name from a run object.

    Supports both KFP v1 (``pipeline_spec.pipeline_name``) and KFP v2 IR
    (``pipeline_spec.pipeline_spec.pipelineInfo.name``).
    """
    spec = getattr(run, "pipeline_spec", None)
    if spec is None:
        return None

    if isinstance(spec, dict):
        # KFP v1: top-level key
        v1 = spec.get("pipeline_name")
        if v1:
            return str(v1)
        # KFP v2 IR: nested under pipeline_spec -> pipelineInfo -> name
        inner = spec.get("pipeline_spec", {})
        if isinstance(inner, dict):
            info = inner.get("pipelineInfo") or inner.get("pipeline_info") or {}
            if isinstance(info, dict) and info.get("name"):
                return str(info["name"])
        return None

    # Object-style access (protobuf / SDK model)
    v1 = getattr(spec, "pipeline_name", None)
    if v1:
        return str(v1)
    inner = getattr(spec, "pipeline_spec", None)
    if inner is not None:
        info = getattr(inner, "pipelineInfo", None) or getattr(inner, "pipeline_info", None)
        if info is not None:
            return getattr(info, "name", None)
    return None


def _is_terminal(state: str) -> bool:
    return state.lower() in _TERMINAL_STATES


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_status(kfp_client: Any, args: argparse.Namespace, **_: Any) -> None:
    """Get run status with task-level details."""
    run = kfp_client.get_run(args.run_id)
    run_obj = getattr(run, "run", run)

    state = _get_run_state(run_obj)
    pipeline_name = _get_pipeline_name(run_obj)

    start_time = getattr(run_obj, "created_at", None)
    end_time = getattr(run_obj, "finished_at", None)
    error = getattr(run_obj, "error", None) or ""

    run_details = getattr(run, "run_details", None) or run_obj
    tasks = _extract_tasks(run_details, pipeline_name)

    if args.json:
        _print_json({
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

    kwargs: dict[str, Any] = {"page_size": args.limit}
    if experiment_id:
        kwargs["experiment_id"] = experiment_id

    response = kfp_client.list_runs(**kwargs)
    runs = getattr(response, "runs", None) or []

    if args.state:
        target = args.state.lower()
        runs = [r for r in runs if _get_run_state(r).lower() == target]

    if args.json:
        rows = []
        for r in runs:
            run_id = getattr(r, "run_id", None) or getattr(r, "id", "")
            name = getattr(r, "display_name", None) or getattr(r, "name", "")
            state = _get_run_state(r)
            created = getattr(r, "created_at", None)
            finished = getattr(r, "finished_at", None)
            duration = None
            if created and finished:
                duration = _format_duration((finished - created).total_seconds())
            rows.append({
                "run_id": str(run_id),
                "name": name,
                "state": state,
                "created": str(created) if created else None,
                "duration": duration,
            })
        _print_json(rows)
        return

    if not runs:
        print("No runs found.")
        return

    entries: list[tuple[str, str, str, str, str]] = []
    for r in runs:
        run_id = str(getattr(r, "run_id", None) or getattr(r, "id", ""))
        name = getattr(r, "display_name", None) or getattr(r, "name", "")
        state = _get_run_state(r)
        created = getattr(r, "created_at", None)
        finished = getattr(r, "finished_at", None)
        created_str = str(created)[:19] if created else ""
        duration = ""
        if created and finished:
            duration = _format_duration((finished - created).total_seconds())
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


def cmd_watch(kfp_client: Any, args: argparse.Namespace, **_: Any) -> None:
    """Live progress monitoring for a pipeline run."""
    run_id = args.run_id
    interval = args.interval
    timeout = args.timeout
    is_tty = sys.stdout.isatty()

    start = time.monotonic()

    while True:
        elapsed = time.monotonic() - start
        if elapsed > timeout:
            print(f"\n[pipelines] Timeout after {_format_duration(timeout)}.")
            sys.exit(2)

        run = kfp_client.get_run(run_id)
        run_obj = getattr(run, "run", run)
        state = _get_run_state(run_obj)
        pipeline_name = _get_pipeline_name(run_obj)

        run_details = getattr(run, "run_details", None) or run_obj
        tasks = _extract_tasks(run_details, pipeline_name)

        if is_tty:
            line_count = 1 + len(tasks)
            sys.stdout.write(f"\033[{line_count}A\033[J" if elapsed > 0 else "")

        sys.stdout.write(
            f"[pipelines] run={run_id[:12]}... state={state} "
            f"elapsed={_format_duration(elapsed)}\n"
        )

        if tasks:
            for t in tasks:
                dots = "." * max(2, 30 - len(t["name"]))
                sys.stdout.write(f"  {t['name']} {dots} {t['state']}\n")

        sys.stdout.flush()

        if _is_terminal(state):
            error = getattr(run_obj, "error", None)
            if error:
                print(f"\nError: {error}")
            exit_code = 0 if state.lower() == "succeeded" else 1
            sys.exit(exit_code)

        time.sleep(interval)


def _list_run_pods(k8s_api: Any, namespace: str, run_id: str) -> list[Any]:
    """List pods for a pipeline run using the ``pipeline/runid`` label.

    Falls back to a broader namespace list filtered by run ID in the pod name
    if the label selector returns no results.
    """
    try:
        pods = k8s_api.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"pipeline/runid={run_id}",
            _request_timeout=30,
        )
        items = pods.items if hasattr(pods, "items") else []
        if items:
            return items
    except Exception as exc:
        _exit_k8s_error(exc, namespace)

    try:
        pods = k8s_api.list_namespaced_pod(namespace=namespace, _request_timeout=30)
        items = pods.items if hasattr(pods, "items") else []
        return [p for p in items if run_id in (p.metadata.name or "")]
    except Exception as exc:
        _exit_k8s_error(exc, namespace)
        return []  # unreachable, keeps mypy happy


def _exit_k8s_error(exc: Exception, namespace: str) -> None:
    """Exit with an actionable K8S API error message."""
    exc_str = str(exc)
    if "403" in exc_str or "Forbidden" in exc_str:
        sys.exit(
            f"K8S API returned 403 Forbidden for namespace '{namespace}'.\n"
            "The token (RHOAI_TOKEN) lacks permission to list pods or read logs.\n"
            "Verify that the token's service account has 'get'/'list' on pods "
            "and 'get' on pods/log in this namespace."
        )
    sys.exit(f"Failed to query K8S API: {exc}")


_SKIP_CONTAINERS = {"wait"}

_POD_COMPONENT_LABEL_KEYS = [
    "pipelines.kubeflow.org/component_name",
    "component_name",
]

_POD_COMPONENT_ANNOTATION_KEYS = [
    "pipelines.kubeflow.org/component_name",
    "pipelines.kubeflow.org/task_name",
]


def _match_pod_to_task(
    pod_name: str,
    labels: dict[str, str],
    annotations: dict[str, str],
    task_names: set[str],
) -> str | None:
    """Match a pod to a task using labels, annotations, and pod name."""
    for key in _POD_COMPONENT_LABEL_KEYS:
        value = labels.get(key, "")
        if value and value in task_names:
            return value

    for key in _POD_COMPONENT_ANNOTATION_KEYS:
        value = annotations.get(key, "")
        if value and value in task_names:
            return value

    for tname in task_names:
        normalized = tname.lower().replace("_", "-").replace(" ", "-")
        if normalized in pod_name:
            return tname

    return None


def _match_task_by_name(query: str, task_names: list[str]) -> list[str]:
    """Match a user query against task/component names (case-insensitive substring)."""
    q = query.lower()
    exact = [t for t in task_names if t.lower() == q]
    if exact:
        return exact
    return [t for t in task_names if q in t.lower()]


def _fetch_pod_logs(
    k8s_api: Any,
    pod_name: str,
    namespace: str,
    container_names: list[str],
    container_statuses: list[Any],
    tail: int,
) -> list[dict[str, str]]:
    """Fetch logs from all non-skipped containers in a pod."""
    containers_logs: list[dict[str, str]] = []

    for cname in container_names:
        if cname in _SKIP_CONTAINERS:
            continue

        try:
            log_text = k8s_api.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                container=cname,
                tail_lines=tail,
                _request_timeout=60,
            )
        except Exception as exc:
            exc_str = str(exc)
            if "403" in exc_str or "Forbidden" in exc_str:
                log_text = "(403 Forbidden -- token lacks pods/log access)"
            else:
                log_text = f"(log unavailable: {exc_str[:120]})"

        containers_logs.append({"name": cname, "logs": log_text or "(empty)"})

        cs = next((s for s in container_statuses if s.name == cname), None)
        waiting_reason = ""
        if cs and cs.state and cs.state.waiting:
            waiting_reason = cs.state.waiting.reason or ""
        if waiting_reason in {"CrashLoopBackOff", "Error", "OOMKilled"}:
            try:
                prev_log = k8s_api.read_namespaced_pod_log(
                    name=pod_name,
                    namespace=namespace,
                    container=cname,
                    tail_lines=tail,
                    previous=True,
                    _request_timeout=60,
                )
                containers_logs.append({
                    "name": f"{cname} (previous)",
                    "logs": prev_log or "(empty)",
                })
            except Exception:
                pass

    return containers_logs


def cmd_logs(kfp_client: Any, args: argparse.Namespace, **kwargs: Any) -> None:
    """Fetch pod logs for pipeline tasks."""
    from autox_tools.pipelines._k8s import connect as k8s_connect

    k8s_api = kwargs.get("k8s_api")
    if k8s_api is None:
        k8s_api = k8s_connect(os.getenv("RHOAI_KFP_URL"))

    namespace = os.getenv("RHOAI_PROJECT_NAME", "")
    if not namespace:
        sys.exit("RHOAI_PROJECT_NAME is required for the logs command.")

    try:
        run = kfp_client.get_run(args.run_id)
    except Exception as exc:
        exc_str = str(exc)
        if "403" in exc_str or "Forbidden" in exc_str:
            sys.exit(
                f"KFP API returned 403 for run '{args.run_id}'.\n"
                "The token may lack access to this run's namespace or experiment."
            )
        sys.exit(f"Failed to get run details: {exc}")

    run_obj = getattr(run, "run", run)
    pipeline_name = _get_pipeline_name(run_obj)

    run_details = getattr(run, "run_details", None) or run_obj
    tasks = _extract_tasks(run_details, pipeline_name)
    all_task_names = [t["name"] for t in tasks]

    if args.task:
        matched_names = _match_task_by_name(args.task, all_task_names)
        if not matched_names:
            print(f"Task '{args.task}' not found. Available components:")
            for t in tasks:
                print(f"  {t['name']:<40} {t['state']}")
            sys.exit(1)
        tasks = [t for t in tasks if t["name"] in matched_names]
    elif not args.all:
        failed_tasks = [t for t in tasks if t["state"].lower() in {"failed", "error"}]
        if failed_tasks:
            tasks = failed_tasks
        else:
            print(f"No failed tasks in run {args.run_id}. All components:")
            for t in tasks:
                print(f"  {t['name']:<40} {t['state']}")
            print("\nUse --all to fetch logs from all components, "
                  "or --task <name> for a specific one.")
            return

    pod_items = _list_run_pods(k8s_api, namespace, args.run_id)

    if not pod_items:
        print(f"No pods found for run {args.run_id} in namespace '{namespace}'.")
        print("Check that the run ID is correct and pods have not been garbage-collected.")
        return

    pods_by_name: dict[str, Any] = {
        (p.metadata.name or ""): p for p in pod_items
    }
    task_names_set = {t["name"] for t in tasks}
    results: list[dict[str, Any]] = []

    # Strategy 1: use pod_name from KFP task details (direct, authoritative)
    matched_tasks: set[str] = set()
    for t in tasks:
        task_pod = t.get("pod_name", "")
        if not task_pod:
            continue

        pod = pods_by_name.get(task_pod)
        if not pod:
            continue

        matched_tasks.add(t["name"])
        cstatuses = []
        if pod.status and pod.status.container_statuses:
            cstatuses = pod.status.container_statuses
        cnames = [c.name for c in (pod.spec.containers or [])] if pod.spec else ["main"]
        clogs = _fetch_pod_logs(k8s_api, task_pod, namespace, cnames, cstatuses, args.tail)

        results.append({
            "name": t["name"],
            "state": t["state"],
            "pod": task_pod,
            "containers": clogs,
        })

    # Strategy 2: for tasks without pod_name, match pods via labels/annotations/name
    unmatched_tasks = task_names_set - matched_tasks
    if unmatched_tasks:
        for pod in pod_items:
            pname = pod.metadata.name or ""
            if pname in {r["pod"] for r in results}:
                continue

            labels = (pod.metadata.labels or {}) if pod.metadata else {}
            annotations = (pod.metadata.annotations or {}) if pod.metadata else {}
            matched = _match_pod_to_task(pname, labels, annotations, unmatched_tasks)

            if not matched:
                continue

            unmatched_tasks.discard(matched)
            task_state = next((t["state"] for t in tasks if t["name"] == matched), "Unknown")
            cstatuses = []
            if pod.status and pod.status.container_statuses:
                cstatuses = pod.status.container_statuses
            cnames = [c.name for c in (pod.spec.containers or [])] if pod.spec else ["main"]
            clogs = _fetch_pod_logs(k8s_api, pname, namespace, cnames, cstatuses, args.tail)

            results.append({
                "name": matched,
                "state": task_state,
                "pod": pname,
                "containers": clogs,
            })

    if args.json:
        _print_json({"run_id": args.run_id, "tasks": results})
        return

    if not results:
        print(f"No pod logs found for run {args.run_id}.")
        print(f"\n  {len(pod_items)} pod(s) found but none matched the requested components.")
        print(f"  Looking for: {', '.join(sorted(task_names_set))}")
        print("\n  Pods in this run:")
        for pod in pod_items[:15]:
            pn = pod.metadata.name or "(unnamed)"
            pl = (pod.metadata.labels or {}) if pod.metadata else {}
            pa = (pod.metadata.annotations or {}) if pod.metadata else {}
            comp = ""
            for k in _POD_COMPONENT_LABEL_KEYS + _POD_COMPONENT_ANNOTATION_KEYS:
                comp = pl.get(k, "") or pa.get(k, "")
                if comp:
                    break
            suffix = f"  component={comp}" if comp else ""
            print(f"    {pn}{suffix}")
        if len(pod_items) > 15:
            print(f"    ... and {len(pod_items) - 15} more")
        return

    for i, r in enumerate(results):
        if i > 0:
            print("\n" + "─" * 72 + "\n")
        print(f"=== {r['name']} ({r['state']}) ===")
        print(f"    pod: {r['pod']}")
        for c in r["containers"]:
            if len(r["containers"]) > 1:
                print(f"    container: {c['name']}")
            print()
            print(c["logs"])


_CATEGORY_LABELS = {
    "evaluation": "Evaluation Results",
    "indexing_notebooks": "Indexing Notebooks",
    "inference_notebooks": "Inference Notebooks",
    "leaderboard": "Leaderboard",
    "rag_patterns": "RAG Patterns",
    "other": "Other",
}


def _categorize_object(key: str) -> str:
    """Assign an S3 object key to an artifact category.

    ``rag_patterns/`` is checked first — files nested inside pattern folders
    (notebooks, evaluation JSONs, etc.) belong to the patterns category regardless
    of their extension.
    """
    if "rag_patterns" in key.lower() or "pattern.json" in key:
        return "rag_patterns"
    if "evaluation_results.json" in key:
        return "evaluation"
    if key.endswith(".ipynb") and "indexing" in key.lower():
        return "indexing_notebooks"
    if key.endswith(".ipynb") and "inference" in key.lower():
        return "inference_notebooks"
    if key.endswith(".html") or "leaderboard" in key.lower():
        return "leaderboard"
    return "other"


def _find_rag_patterns_prefix(
    s3_client: Any,
    bucket: str,
    key_prefix: str,
) -> str | None:
    """Discover the ``rag_patterns/`` base prefix by sampling object keys.

    The folder sits at an unpredictable depth (e.g.
    ``<prefix>/<component>/<task-id>/rag_patterns/``).  Fetches a small batch
    of objects and scans for the first key containing ``/rag_patterns/``.
    """
    from autox_tools.s3.cli import _paginate_objects

    result = _paginate_objects(s3_client, bucket, key_prefix, max_keys=200)
    for obj in result.get("Contents", []):
        key: str = obj["Key"]
        idx = key.find("/rag_patterns/")
        if idx >= 0:
            return key[: idx + len("/rag_patterns/")]
    return None


def _discover_patterns(
    s3_client: Any,
    bucket: str,
    rag_prefix: str,
) -> list[str]:
    """Enumerate RAG pattern folder names under the given prefix."""
    from autox_tools.s3.cli import _paginate_objects

    result = _paginate_objects(s3_client, bucket, rag_prefix, delimiter="/")
    patterns: list[str] = []
    for cp in result.get("CommonPrefixes", []):
        name = cp["Prefix"][len(rag_prefix) :].rstrip("/")
        if name:
            patterns.append(name)
    return sorted(patterns)


def _match_pattern_name(query: str, patterns: list[str]) -> list[str]:
    """Case-insensitive substring match for pattern names; exact match preferred."""
    q = query.lower()
    exact = [p for p in patterns if p.lower() == q]
    if exact:
        return exact
    return [p for p in patterns if q in p.lower()]


def _discover_components(
    s3_client: Any,
    bucket: str,
    key_prefix: str,
) -> list[str]:
    """Enumerate pipeline component folder names under the run prefix."""
    from autox_tools.s3.cli import _paginate_objects

    result = _paginate_objects(s3_client, bucket, key_prefix, delimiter="/")
    components: list[str] = []
    for cp in result.get("CommonPrefixes", []):
        name = cp["Prefix"][len(key_prefix):].rstrip("/")
        if name:
            components.append(name)
    return sorted(components)


def _resolve_artifact_s3(
    run_obj: Any, pipeline_name: str,
) -> tuple[Any, str, str, str]:
    """Resolve S3 client, bucket, prefix, and artifact root from KFP run config.

    Returns ``(s3_client, bucket, key_prefix, artifact_root)``.
    Exits on missing credentials or bucket.
    """
    runtime_config = getattr(run_obj, "runtime_config", None)
    artifact_root = None
    if runtime_config:
        artifact_root = getattr(runtime_config, "pipeline_root", None)
        if not artifact_root:
            params = getattr(runtime_config, "parameters", {}) or {}
            if isinstance(params, dict):
                artifact_root = params.get("output", None)

    if not artifact_root:
        artifact_root = f"{pipeline_name}/"

    s3_endpoint = os.getenv("ARTIFACTS_AWS_S3_ENDPOINT")
    if not s3_endpoint:
        sys.exit(
            "Artifacts S3 credentials not configured -- set ARTIFACTS_AWS_S3_ENDPOINT, "
            "ARTIFACTS_AWS_ACCESS_KEY_ID, and ARTIFACTS_AWS_SECRET_ACCESS_KEY."
        )

    from autox_tools.pipelines._artifacts_s3 import connect as artifacts_s3_connect
    s3_client = artifacts_s3_connect()

    if artifact_root.startswith("s3://"):
        cleaned = artifact_root[5:]
        parts = cleaned.split("/", 1)
        bucket = parts[0]
        key_prefix = parts[1] if len(parts) == 2 else ""
    else:
        bucket = os.getenv("ARTIFACTS_S3_BUCKET", "")
        if not bucket:
            sys.exit(
                "ARTIFACTS_S3_BUCKET is required when the KFP run config does not "
                "include a full s3:// artifact root.\n"
                f"  artifact_root: {artifact_root}"
            )
        key_prefix = artifact_root

    return s3_client, bucket, key_prefix, artifact_root


def _refine_prefix_for_run(
    s3_client: Any,
    bucket: str,
    key_prefix: str,
    run_id: str,
) -> str:
    """Narrow ``key_prefix`` to include the run ID when it is not already present.

    KFP's ``pipeline_root`` often points at the pipeline level
    (``pipeline-name/``) rather than the run level
    (``pipeline-name/run-id/``).  When that happens, S3 listings return
    run-ID folders instead of component folders.  This helper checks for
    the more specific path and returns it when objects exist there.
    """
    if run_id in key_prefix:
        return key_prefix

    from autox_tools.s3.cli import _paginate_objects

    candidate = f"{key_prefix}{run_id}/"
    probe = _paginate_objects(s3_client, bucket, candidate, max_keys=1)
    if probe.get("Contents"):
        return candidate
    return key_prefix


def _download_objects(
    s3_client: Any,
    bucket: str,
    objects: list[dict[str, Any]],
    base_prefix: str,
    download_dir: str,
) -> None:
    """Download S3 objects to a local directory, preserving relative paths."""
    from autox_tools.s3.cli import _human_size

    os.makedirs(download_dir, exist_ok=True)
    downloaded = 0
    total_bytes = 0

    for obj in objects:
        key = obj["Key"]
        if key.endswith("/"):
            continue
        rel = key[len(base_prefix):].lstrip("/") if base_prefix else key
        local_path = os.path.join(download_dir, rel)
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        s3_client.download_file(bucket, key, local_path)
        downloaded += 1
        total_bytes += obj.get("Size", 0)
        print(f"  Downloaded: {rel} ({_human_size(obj.get('Size', 0))})")

    print(f"\n  {downloaded} file(s), {_human_size(total_bytes)} total to {download_dir}/")


def _cmd_artifacts_pattern(
    s3_client: Any,
    bucket: str,
    key_prefix: str,
    args: argparse.Namespace,
) -> None:
    """Handle ``--pattern`` mode: list or download RAG pattern artifacts."""
    from autox_tools.s3.cli import _human_size, _paginate_objects

    rag_prefix = _find_rag_patterns_prefix(s3_client, bucket, key_prefix)
    if not rag_prefix:
        print("No rag_patterns/ folder found under this run's artifacts.")
        sys.exit(1)

    patterns = _discover_patterns(s3_client, bucket, rag_prefix)
    if not patterns:
        print(f"No patterns found under {rag_prefix}")
        sys.exit(1)

    if args.pattern == "all":
        _cmd_artifacts_pattern_all(
            s3_client, bucket, rag_prefix, patterns, args,
        )
        return

    matched = _match_pattern_name(args.pattern, patterns)
    if not matched:
        print(f"Pattern '{args.pattern}' not found. Available patterns:")
        for p in patterns:
            print(f"  {p}")
        sys.exit(1)

    if len(matched) > 1 and not args.artifact:
        print(f"'{args.pattern}' matches multiple patterns:")
        for p in matched:
            print(f"  {p}")
        print("\nSpecify a more precise name.")
        sys.exit(1)

    pattern_name = matched[0]
    pattern_prefix = f"{rag_prefix}{pattern_name}/"
    result = _paginate_objects(s3_client, bucket, pattern_prefix)
    objects = result.get("Contents", [])

    if args.artifact:
        _cmd_artifacts_single_file(
            s3_client, bucket, pattern_prefix, objects,
            pattern_name, args,
        )
        return

    if args.json:
        _print_json({
            "pattern": pattern_name,
            "prefix": pattern_prefix,
            "total": len(objects),
            "artifacts": [
                {"key": o["Key"], "size_bytes": o.get("Size", 0)} for o in objects
            ],
        })
        return

    print(f"Pattern: {pattern_name}")
    print(f"Prefix : {pattern_prefix}\n")
    for obj in objects:
        rel = obj["Key"][len(pattern_prefix):]
        if not rel:
            continue
        print(f"  {rel:<60} {_human_size(obj.get('Size', 0)):>10}")
    print(f"\n  {len(objects)} artifact(s)")

    if args.download:
        print()
        _download_objects(s3_client, bucket, objects, pattern_prefix, args.download)


def _cmd_artifacts_pattern_all(
    s3_client: Any,
    bucket: str,
    rag_prefix: str,
    patterns: list[str],
    args: argparse.Namespace,
) -> None:
    """List all RAG patterns with file counts and sizes."""
    from autox_tools.s3.cli import _human_size, _paginate_objects

    summaries: list[dict[str, Any]] = []
    all_objects: list[dict[str, Any]] = []

    for pname in patterns:
        prefix = f"{rag_prefix}{pname}/"
        result = _paginate_objects(s3_client, bucket, prefix)
        objs = result.get("Contents", [])
        total_size = sum(o.get("Size", 0) for o in objs)
        summaries.append({
            "name": pname,
            "file_count": len(objs),
            "total_size": total_size,
        })
        all_objects.extend(objs)

    if args.json:
        _print_json({"patterns": summaries, "total_patterns": len(summaries)})
        return

    max_name = max(len(s["name"]) for s in summaries) if summaries else 10
    print(f"RAG Patterns ({len(summaries)}):\n")
    for s in summaries:
        print(
            f"  {s['name']:<{max_name}}  "
            f"{s['file_count']:>5} file(s)  "
            f"{_human_size(s['total_size']):>10}"
        )

    total_files = sum(s["file_count"] for s in summaries)
    total_bytes = sum(s["total_size"] for s in summaries)
    print(f"\n  Total: {len(summaries)} pattern(s), {total_files} file(s), {_human_size(total_bytes)}")

    if args.download:
        print()
        _download_objects(s3_client, bucket, all_objects, rag_prefix, args.download)


def _cmd_artifacts_single_file(
    s3_client: Any,
    bucket: str,
    pattern_prefix: str,
    objects: list[dict[str, Any]],
    pattern_name: str,
    args: argparse.Namespace,
) -> None:
    """Handle ``--artifact`` mode: show, download, or print a single file."""
    from autox_tools.s3.cli import _human_size

    query = args.artifact.lower()
    matches = [
        o for o in objects
        if query in os.path.basename(o["Key"]).lower()
    ]
    if not matches:
        print(f"Artifact '{args.artifact}' not found in pattern '{pattern_name}'.")
        print("Available artifacts:")
        for o in objects:
            rel = o["Key"][len(pattern_prefix):]
            if rel:
                print(f"  {rel}")
        sys.exit(1)

    if len(matches) > 1:
        exact = [o for o in matches if os.path.basename(o["Key"]).lower() == query]
        if len(exact) == 1:
            matches = exact
        else:
            print(f"'{args.artifact}' matches multiple artifacts:")
            for o in matches:
                print(f"  {os.path.basename(o['Key'])}")
            print("\nSpecify a more precise name.")
            sys.exit(1)

    obj = matches[0]
    key = obj["Key"]
    size = obj.get("Size", 0)
    filename = os.path.basename(key)

    if args.print_content:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        sys.stdout.buffer.write(response["Body"].read())
        return

    if args.json:
        _print_json({
            "pattern": pattern_name,
            "artifact": filename,
            "key": key,
            "size_bytes": size,
        })
        return

    print(f"Pattern  : {pattern_name}")
    print(f"Artifact : {filename} ({_human_size(size)})")
    print(f"S3 key   : {key}")

    if args.download:
        local_path = os.path.join(args.download, filename)
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        s3_client.download_file(bucket, key, local_path)
        print(f"\n  Downloaded to {local_path}")


def _cmd_artifacts_component(
    s3_client: Any,
    bucket: str,
    key_prefix: str,
    args: argparse.Namespace,
) -> None:
    """Handle ``--component`` mode: list or download a single component's artifacts."""
    from autox_tools.s3.cli import _human_size, _paginate_objects

    components = _discover_components(s3_client, bucket, key_prefix)
    if not components:
        print(f"No component folders found under {key_prefix}")
        sys.exit(1)

    if args.component == "all":
        summaries: list[dict[str, Any]] = []
        for cname in components:
            prefix = f"{key_prefix}{cname}/"
            result = _paginate_objects(s3_client, bucket, prefix)
            objs = result.get("Contents", [])
            total_size = sum(o.get("Size", 0) for o in objs)
            summaries.append({
                "name": cname,
                "file_count": len(objs),
                "total_size": total_size,
            })

        if args.json:
            _print_json({"components": summaries, "total_components": len(summaries)})
            return

        max_name = max(len(s["name"]) for s in summaries) if summaries else 10
        print(f"Components ({len(summaries)}):\n")
        for s in summaries:
            print(
                f"  {s['name']:<{max_name}}  "
                f"{s['file_count']:>5} file(s)  "
                f"{_human_size(s['total_size']):>10}"
            )
        total_files = sum(s["file_count"] for s in summaries)
        total_bytes = sum(s["total_size"] for s in summaries)
        print(f"\n  Total: {len(summaries)} component(s), {total_files} file(s), {_human_size(total_bytes)}")
        return

    matched = _match_pattern_name(args.component, components)
    if not matched:
        print(f"Component '{args.component}' not found. Available components:")
        for c in components:
            print(f"  {c}")
        sys.exit(1)

    if len(matched) > 1:
        print(f"'{args.component}' matches multiple components:")
        for c in matched:
            print(f"  {c}")
        print("\nSpecify a more precise name.")
        sys.exit(1)

    comp_name = matched[0]
    comp_prefix = f"{key_prefix}{comp_name}/"
    result = _paginate_objects(s3_client, bucket, comp_prefix)
    objects = result.get("Contents", [])

    if args.json:
        _print_json({
            "component": comp_name,
            "prefix": comp_prefix,
            "total": len(objects),
            "artifacts": [
                {"key": o["Key"], "size_bytes": o.get("Size", 0)} for o in objects
            ],
        })
        return

    print(f"Component: {comp_name}")
    print(f"Prefix   : {comp_prefix}\n")
    for obj in objects:
        rel = obj["Key"][len(comp_prefix):]
        if not rel:
            continue
        print(f"  {rel:<60} {_human_size(obj.get('Size', 0)):>10}")
    print(f"\n  {len(objects)} artifact(s)")

    if args.download:
        print()
        _download_objects(s3_client, bucket, objects, comp_prefix, args.download)


def cmd_artifacts(kfp_client: Any, args: argparse.Namespace, **_: Any) -> None:
    """List or download S3 artifacts from a pipeline run."""
    run = kfp_client.get_run(args.run_id)
    run_obj = getattr(run, "run", run)
    pipeline_name = _get_pipeline_name(run_obj) or "unknown"

    has_s3 = bool(os.getenv("ARTIFACTS_AWS_S3_ENDPOINT"))
    if not has_s3 and not args.pattern and not args.component:
        runtime_config = getattr(run_obj, "runtime_config", None)
        artifact_root = None
        if runtime_config:
            artifact_root = getattr(runtime_config, "pipeline_root", None)
        if args.json:
            _print_json({
                "run_id": args.run_id,
                "artifact_root": artifact_root,
                "note": "Artifacts S3 credentials not configured.",
            })
        else:
            print(f"Artifact root: {artifact_root or 'unknown'}")
            print(
                "Artifacts S3 credentials not configured -- set ARTIFACTS_AWS_S3_ENDPOINT, "
                "ARTIFACTS_AWS_ACCESS_KEY_ID, and ARTIFACTS_AWS_SECRET_ACCESS_KEY."
            )
        return

    s3_client, bucket, key_prefix, artifact_root = _resolve_artifact_s3(
        run_obj, pipeline_name,
    )
    key_prefix = _refine_prefix_for_run(s3_client, bucket, key_prefix, args.run_id)

    if args.component:
        if args.pattern or args.artifact or args.print_content:
            sys.exit("--component cannot be combined with --pattern, --artifact, or --print.")
        _cmd_artifacts_component(s3_client, bucket, key_prefix, args)
        return

    if args.pattern:
        _cmd_artifacts_pattern(s3_client, bucket, key_prefix, args)
        return

    if args.artifact:
        sys.exit("--artifact requires --pattern. Usage: --pattern <name> --artifact <file>")

    if args.print_content:
        sys.exit("--print requires --pattern and --artifact.")

    _cmd_artifacts_summary(s3_client, bucket, key_prefix, args)


def _cmd_artifacts_summary(
    s3_client: Any,
    bucket: str,
    key_prefix: str,
    args: argparse.Namespace,
) -> None:
    """Default mode: show a summary of artifacts with category counts."""
    from autox_tools.s3.cli import _human_size, _paginate_objects

    result = _paginate_objects(s3_client, bucket, key_prefix)
    objects = result.get("Contents", [])

    categories: dict[str, int] = {k: 0 for k in _CATEGORY_LABELS}
    cat_sizes: dict[str, int] = {k: 0 for k in _CATEGORY_LABELS}

    for obj in objects:
        cat = _categorize_object(obj["Key"])
        categories[cat] += 1
        cat_sizes[cat] += obj.get("Size", 0)

    rag_prefix = _find_rag_patterns_prefix(s3_client, bucket, key_prefix)
    patterns: list[str] = []
    if rag_prefix:
        patterns = _discover_patterns(s3_client, bucket, rag_prefix)

    total_size = sum(cat_sizes.values())

    if args.json:
        _print_json({
            "run_id": args.run_id,
            "bucket": bucket,
            "prefix": key_prefix,
            "total_artifacts": len(objects),
            "total_size": total_size,
            "categories": {
                k: {"count": categories[k], "size": cat_sizes[k]}
                for k in _CATEGORY_LABELS
            },
            "patterns": patterns,
        })
        return

    print(f"Artifacts for run {args.run_id}")
    print(f"Bucket: {bucket}  Prefix: {key_prefix}\n")

    for cat_key, label in _CATEGORY_LABELS.items():
        count = categories[cat_key]
        if not count:
            continue
        print(f"  {label:<25} {count:>6} file(s)  {_human_size(cat_sizes[cat_key]):>10}")

    print(f"\n  Total: {len(objects)} artifact(s), {_human_size(total_size)}")

    if patterns:
        print(f"\n  RAG Patterns ({len(patterns)}):")
        for p in patterns:
            print(f"    {p}")
        print("\n  Use --pattern <name> to browse, --pattern all to list all.")

    if args.download:
        if len(objects) > 1000:
            print(f"\n  Downloading {len(objects)} objects...")
        print()
        _download_objects(s3_client, bucket, objects, key_prefix, args.download)


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
    p.add_argument("--task", help="Component name or substring (e.g. 'rag-templates-optimization')")
    p.add_argument("--tail", type=int, default=100, help="Number of log lines from the end (default: 100)")
    p.add_argument("--all", action="store_true", help="Show logs for all components, not just failed")

    # artifacts
    p = sub.add_parser("artifacts", help="List S3 artifacts from a run")
    p.add_argument("run_id", help="KFP run ID (UUID)")
    p.add_argument(
        "--component",
        help="Pipeline component name or 'all' (e.g. --component search-space-optimization)",
    )
    p.add_argument(
        "--pattern",
        help="RAG pattern name or 'all' (e.g. --pattern Pattern1, --pattern all)",
    )
    p.add_argument(
        "--artifact",
        help="Artifact filename within a pattern (requires --pattern)",
    )
    p.add_argument(
        "--print", action="store_true", default=False, dest="print_content",
        help="Output artifact content to stdout (requires --pattern and --artifact)",
    )
    p.add_argument("--download", metavar="DIR", help="Download artifacts to directory")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    kfp_client = kfp_connect()

    k8s_api = None
    if args.command == "logs":
        from autox_tools.pipelines._k8s import connect as k8s_connect
        k8s_api = k8s_connect(os.getenv("RHOAI_KFP_URL"))

    commands: dict[str, Any] = {
        "status": cmd_status,
        "list": cmd_list,
        "watch": cmd_watch,
        "logs": cmd_logs,
        "artifacts": cmd_artifacts,
    }
    commands[args.command](kfp_client, args, k8s_api=k8s_api)


if __name__ == "__main__":
    main()
