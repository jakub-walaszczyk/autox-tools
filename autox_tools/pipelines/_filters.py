"""Filter KFP/Argo scaffolding tasks from pipeline run details.

KFP v2 generates internal orchestration tasks (drivers, loop iterators,
root DAG nodes) that clutter status output.  This module identifies and
hides them so operators see only user-defined pipeline components.
"""

from __future__ import annotations

import re

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SKIP_NAMES = {"root", "executor"}
_SKIP_PREFIXES = ("for-loop-", "iteration-item-", "iteration-iterations-")


def is_user_task(display_name: str, pipeline_name: str | None = None) -> bool:
    """Return True if the task represents a user-defined pipeline component."""
    name = display_name.strip()

    if name.endswith("-driver"):
        return False
    if name.lower() in _SKIP_NAMES:
        return False
    if any(name.startswith(p) for p in _SKIP_PREFIXES):
        return False
    if _UUID_RE.match(name):
        return False

    return not (pipeline_name and name.startswith(f"{pipeline_name}-"))
