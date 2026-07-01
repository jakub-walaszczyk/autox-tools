"""Kubernetes client factory for secret management.

Delegates to the shared :mod:`autox_tools._k8s` module.
Re-exports ``derive_k8s_api_url`` and a ``connect`` wrapper
that omits the *kfp_url* parameter (secrets never need it).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from autox_tools._k8s import connect as _shared_connect
from autox_tools._k8s import derive_k8s_api_url as _derive_k8s_api_url  # noqa: F401

if TYPE_CHECKING:
    from kubernetes import client as k8s_client

    from autox_tools.config._models import RhoaiConfig


def connect(cfg: RhoaiConfig | None = None) -> k8s_client.CoreV1Api:
    """Build a Kubernetes ``CoreV1Api`` client from *cfg* or environment variables."""
    return _shared_connect(cfg=cfg)
