"""KFP client factory and run introspection helpers.

Accepts an optional ``RhoaiConfig`` for profile-based configuration.
Falls back to environment variables when no config is provided.

Required env vars (or .env file) for the fallback path::

    RHOAI_KFP_URL          KFP API endpoint URL (must end with ``/``)
    RHOAI_TOKEN            Bearer token for authentication
    RHOAI_PROJECT_NAME     OpenShift namespace where pipelines run

Optional::

    KFP_VERIFY_SSL         Set to ``false`` to skip TLS verification (default: ``true``)
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Any

from dotenv import find_dotenv, load_dotenv

from autox_tools.pipelines._filters import is_user_task

if TYPE_CHECKING:
    from autox_tools.config._models import RhoaiConfig

_REQUIRED_VARS = ("RHOAI_KFP_URL", "RHOAI_TOKEN", "RHOAI_PROJECT_NAME")

_TERMINAL_STATES = {"succeeded", "failed", "skipped", "error"}


def connect(cfg: RhoaiConfig | None = None):
    """Build a ``kfp.Client`` from *cfg* or environment variables."""
    import kfp

    if cfg is not None:
        host = cfg.kfp_url
        if not host.endswith("/"):
            host += "/"
        return kfp.Client(
            host=host,
            namespace=cfg.project_name,
            existing_token=cfg.token,
            verify_ssl=cfg.verify_ssl,
        )

    load_dotenv(find_dotenv(usecwd=True))

    missing = [v for v in _REQUIRED_VARS if not os.getenv(v)]
    if missing:
        sys.exit(f"Missing required environment variables: {', '.join(missing)}")

    host = os.environ["RHOAI_KFP_URL"]
    if not host.endswith("/"):
        host += "/"

    verify_ssl = os.getenv("KFP_VERIFY_SSL", "true").lower() != "false"

    return kfp.Client(
        host=host,
        namespace=os.environ["RHOAI_PROJECT_NAME"],
        existing_token=os.environ["RHOAI_TOKEN"],
        verify_ssl=verify_ssl,
    )


# ---------------------------------------------------------------------------
# Run introspection helpers
# ---------------------------------------------------------------------------


def get_run_state(run: Any) -> str:
    """Extract the run state string from a KFP run object."""
    state: Any = getattr(run, "state", None) or getattr(run, "status", None)
    if state is None:
        status = getattr(run, "status", None)
        if status is not None and hasattr(status, "state"):
            state = status.state
    if state is not None and hasattr(state, "value"):
        state = state.value
    return str(state) if state else "Unknown"


def get_pipeline_name(run: Any) -> str | None:
    """Extract the pipeline name from a run object.

    Supports both KFP v1 (``pipeline_spec.pipeline_name``) and KFP v2 IR
    (``pipeline_spec.pipeline_spec.pipelineInfo.name``).
    """
    spec = getattr(run, "pipeline_spec", None)
    if spec is None:
        return None

    if isinstance(spec, dict):
        v1 = spec.get("pipeline_name")
        if v1:
            return str(v1)
        inner = spec.get("pipeline_spec", {})
        if isinstance(inner, dict):
            info = inner.get("pipelineInfo") or inner.get("pipeline_info") or {}
            if isinstance(info, dict) and info.get("name"):
                return str(info["name"])
        return None

    v1 = getattr(spec, "pipeline_name", None)
    if v1:
        return str(v1)
    inner = getattr(spec, "pipeline_spec", None)
    if inner is not None:
        info = getattr(inner, "pipelineInfo", None) or getattr(inner, "pipeline_info", None)
        if info is not None:
            return getattr(info, "name", None)
    return None


def is_terminal(state: str) -> bool:
    """Return ``True`` if *state* represents a finished run."""
    return state.lower() in _TERMINAL_STATES


def extract_tasks(run_details: Any, pipeline_name: str | None = None) -> list[dict[str, Any]]:
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
