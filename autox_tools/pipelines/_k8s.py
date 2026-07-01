"""Kubernetes client factory for pod log access.

Delegates to the shared :mod:`autox_tools._k8s` module.  Retains a
local ``_derive_k8s_api_url`` wrapper that checks the ``K8S_API_URL``
env-var override and calls ``sys.exit`` on failure — behaviour expected
by the pipeline CLI and its tests.
"""

from __future__ import annotations

import os
import sys

from autox_tools._k8s import (
    connect,  # noqa: F401 — re-exported
    derive_k8s_api_url,
)


def _derive_k8s_api_url(kfp_url: str, *, port_override: str = "") -> str:
    """Derive K8S API URL, checking ``K8S_API_URL`` env var first.

    Unlike the shared :func:`~autox_tools._k8s.derive_k8s_api_url`
    this wrapper treats failure as fatal (``sys.exit``).
    """
    if override := os.getenv("K8S_API_URL"):
        return override.strip().rstrip("/")

    result = derive_k8s_api_url(kfp_url, port_override=port_override)
    if result is None:
        sys.exit(
            f"Cannot derive K8S API URL from '{kfp_url}'. "
            "Set K8S_API_URL explicitly."
        )
    return result
