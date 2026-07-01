"""Pod log retrieval for pipeline task debugging.

Discovers pods for a KFP run, matches them to pipeline tasks using
labels, annotations, and naming conventions, then fetches container
logs.  Supports three matching strategies (task pod_name, label/annotation
match, fallback to execution pods) to handle KFP v1 and v2 layouts.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Any

from autox_tools._output import print_json
from autox_tools.pipelines._kfp import extract_tasks, get_pipeline_name

if TYPE_CHECKING:
    import argparse


_SKIP_CONTAINERS = {"wait"}

_POD_COMPONENT_LABEL_KEYS = [
    "pipelines.kubeflow.org/component_name",
    "pipelines.kubeflow.org/v2_component_name",
    "component_name",
]

_POD_COMPONENT_ANNOTATION_KEYS = [
    "pipelines.kubeflow.org/component_name",
    "pipelines.kubeflow.org/v2_component_name",
    "pipelines.kubeflow.org/task_name",
]


# ---------------------------------------------------------------------------
# Pod discovery & matching
# ---------------------------------------------------------------------------


def _list_run_pods(k8s_api: Any, namespace: str, run_id: str) -> list[Any]:
    """List all pods for a pipeline run.

    Merges results from two discovery methods so that both driver and impl
    pods are found even when only one kind carries the KFP label:

    1. Label selector ``pipeline/runid=<run_id>``
    2. Namespace-wide list filtered by run ID in the pod name
    """
    seen: dict[str, Any] = {}

    try:
        pods = k8s_api.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"pipeline/runid={run_id}",
            _request_timeout=30,
        )
        for p in (pods.items if hasattr(pods, "items") else []):
            name = (p.metadata.name or "") if p.metadata else ""
            if name:
                seen[name] = p
    except Exception as exc:
        _exit_k8s_error(exc, namespace)

    try:
        pods = k8s_api.list_namespaced_pod(
            namespace=namespace, _request_timeout=30,
        )
        for p in (pods.items if hasattr(pods, "items") else []):
            name = (p.metadata.name or "") if p.metadata else ""
            if name and name not in seen and run_id in name:
                seen[name] = p
    except Exception as exc:
        if not seen:
            _exit_k8s_error(exc, namespace)

    return list(seen.values())


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


def _normalize_component_name(name: str) -> str:
    """Normalize a KFP component/task name for fuzzy matching.

    Strips the ``comp-`` prefix added by KFP v2 and unifies separators.
    """
    n = name.lower().strip()
    if n.startswith("comp-"):
        n = n[5:]
    return n.replace("_", "-").replace(" ", "-")


def _match_pod_to_task(
    pod_name: str,
    labels: dict[str, str],
    annotations: dict[str, str],
    task_names: set[str],
) -> str | None:
    """Match a pod to a task using labels, annotations, and pod name."""
    normalized_lookup = {_normalize_component_name(t): t for t in task_names}

    for key in _POD_COMPONENT_LABEL_KEYS + _POD_COMPONENT_ANNOTATION_KEYS:
        value = labels.get(key, "") or annotations.get(key, "")
        if not value:
            continue
        if value in task_names:
            return value
        norm = _normalize_component_name(value)
        if norm in normalized_lookup:
            return normalized_lookup[norm]

    for tname in task_names:
        normalized = _normalize_component_name(tname)
        if normalized in pod_name:
            return tname

    return None


def _is_pod_failed(pod: Any) -> bool:
    """Return True if a pod is in a failed state (phase or container exit code)."""
    phase = ((pod.status.phase or "") if pod.status else "").lower()
    if phase == "failed":
        return True
    for cs in (pod.status.container_statuses or []) if pod.status else []:
        terminated = cs.state.terminated if cs.state else None
        if terminated and terminated.exit_code != 0:
            return True
    return False


def _prefer_impl_pod(
    pod_name: str,
    pods_by_name: dict[str, Any],
) -> tuple[str, Any]:
    """Resolve a driver pod to its impl counterpart when available.

    KFP v2 records the driver pod on ``task.pod_name``, but user code runs
    in the impl pod.  When *pod_name* ends with a ``-driver`` segment, this
    looks for a sibling pod whose name shares the same prefix and contains
    ``-impl-``.  Returns ``(resolved_name, pod_object)``.
    """
    pod = pods_by_name.get(pod_name)

    parts = pod_name.rsplit("-driver", 1)
    if len(parts) < 2:
        return pod_name, pod

    prefix = parts[0]
    for candidate_name, candidate_pod in pods_by_name.items():
        if candidate_name.startswith(prefix) and "-impl-" in candidate_name:
            return candidate_name, candidate_pod

    return pod_name, pod


def _find_exec_pods(pod_items: list[Any], prefer_failed: bool) -> list[Any]:
    """Select execution pods for the fallback log dump.

    Prefers ``*-impl-*`` pods (where user code runs in KFP v2) over driver
    pods.  When *prefer_failed* is True, further narrows to failed pods.
    """
    impl_pods = [p for p in pod_items if "-impl-" in (p.metadata.name or "")]
    pool = impl_pods or pod_items

    if prefer_failed:
        failed = [p for p in pool if _is_pod_failed(p)]
        if failed:
            return failed

    return pool


# ---------------------------------------------------------------------------
# Log fetching
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


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
    pipeline_name = get_pipeline_name(run_obj)

    run_details = getattr(run, "run_details", None) or run_obj
    tasks = extract_tasks(run_details, pipeline_name)

    if not args.all:
        failed_tasks = [t for t in tasks if t["state"].lower() in {"failed", "error"}]
        if failed_tasks:
            tasks = failed_tasks
        else:
            print(f"No failed tasks in run {args.run_id}. All components:")
            for t in tasks:
                print(f"  {t['name']:<40} {t['state']}")
            print("\nUse --all to fetch logs from all components.")
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

    # Strategy 1: use pod_name from KFP task details, preferring impl pods
    matched_tasks: set[str] = set()
    for t in tasks:
        task_pod = t.get("pod_name", "")
        if not task_pod or task_pod not in pods_by_name:
            continue

        resolved_name, pod = _prefer_impl_pod(task_pod, pods_by_name)

        matched_tasks.add(t["name"])
        cstatuses = []
        if pod.status and pod.status.container_statuses:
            cstatuses = pod.status.container_statuses
        cnames = [c.name for c in (pod.spec.containers or [])] if pod.spec else ["main"]
        clogs = _fetch_pod_logs(k8s_api, resolved_name, namespace, cnames, cstatuses, args.tail)

        results.append({
            "name": t["name"],
            "state": t["state"],
            "pod": resolved_name,
            "containers": clogs,
        })

    # Strategy 2: for tasks without pod_name, match pods via labels/annotations/name
    unmatched_tasks = task_names_set - matched_tasks
    if unmatched_tasks:
        sorted_pods = sorted(
            pod_items,
            key=lambda p: 0 if "-impl-" in (p.metadata.name or "") else 1,
        )
        for pod in sorted_pods:
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

    # Strategy 3: fallback — show logs from execution pods when mapping fails
    if not results and pod_items:
        failed_states = {"failed", "error"}
        want_failed = any(t["state"].lower() in failed_states for t in tasks)
        exec_pods = _find_exec_pods(pod_items, want_failed)

        for pod in exec_pods:
            pname = pod.metadata.name or ""
            phase = (pod.status.phase or "Unknown") if pod.status else "Unknown"
            cstatuses = []
            if pod.status and pod.status.container_statuses:
                cstatuses = pod.status.container_statuses
            cnames = [c.name for c in (pod.spec.containers or [])] if pod.spec else ["main"]
            clogs = _fetch_pod_logs(k8s_api, pname, namespace, cnames, cstatuses, args.tail)

            results.append({
                "name": f"(unmatched pod, phase={phase})",
                "state": phase,
                "pod": pname,
                "containers": clogs,
            })

    if args.json:
        print_json({"run_id": args.run_id, "tasks": results})
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
