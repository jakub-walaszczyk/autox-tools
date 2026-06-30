"""Kubernetes client factory for pod log access.

Accepts an optional ``RhoaiConfig`` for profile-based configuration.
Falls back to environment variables when no config is provided.

Derives the K8S API URL from the KFP route URL by convention:

- Standard OCP: ``https://<route>.apps.<cluster>`` -> ``https://api.<cluster>:6443``
- ROSA:         ``https://<route>.apps.rosa.<cluster>`` -> ``https://api.<cluster>:443``

Override explicitly with ``K8S_API_URL`` (env var) or ``k8s_api_url`` (config)
if the convention does not apply.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import urllib3
from dotenv import find_dotenv, load_dotenv
from kubernetes import client as k8s_client

if TYPE_CHECKING:
    from autox_tools.config._models import RhoaiConfig


def _derive_k8s_api_url(kfp_url: str, *, port_override: str = "") -> str:
    """Derive K8S API URL from KFP route URL.

    Checks ``K8S_API_URL`` env var first as an explicit override.
    """
    if override := os.getenv("K8S_API_URL"):
        return override.strip().rstrip("/")

    hostname = urlparse(kfp_url).hostname or ""
    apps_idx = hostname.find(".apps.")
    if apps_idx < 0:
        sys.exit(
            f"Cannot derive K8S API URL from '{kfp_url}'. "
            "Set K8S_API_URL explicitly."
        )

    base_domain = hostname[apps_idx + len(".apps."):]
    is_rosa = base_domain.startswith("rosa.")
    if is_rosa:
        base_domain = base_domain[len("rosa."):]
    default_port = 443 if is_rosa else 6443
    port = port_override or os.getenv("K8S_API_PORT", str(default_port)).strip()

    return f"https://api.{base_domain}:{port}"


def _build_client(api_url: str, token: str, verify_ssl: bool):
    config = k8s_client.Configuration()
    config.host = api_url
    config.api_key = {"authorization": f"Bearer {token}"}
    config.verify_ssl = verify_ssl

    if not verify_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    return k8s_client.CoreV1Api(k8s_client.ApiClient(config))


def connect(kfp_url: str | None = None, cfg: RhoaiConfig | None = None):
    """Build a Kubernetes ``CoreV1Api`` client from *cfg* or environment variables."""
    if cfg is not None:
        api_url = cfg.k8s_api_url.strip().rstrip("/") if cfg.k8s_api_url else ""
        if not api_url:
            api_url = _derive_k8s_api_url(cfg.kfp_url, port_override=cfg.k8s_api_port)
        return _build_client(api_url, cfg.token, cfg.verify_ssl)

    load_dotenv(find_dotenv(usecwd=True))

    token = os.getenv("RHOAI_TOKEN")
    if not token:
        sys.exit("Missing RHOAI_TOKEN for Kubernetes API access.")

    resolved_url: str = kfp_url or os.getenv("RHOAI_KFP_URL") or ""
    api_url = _derive_k8s_api_url(resolved_url)

    verify_ssl = os.getenv("KFP_VERIFY_SSL", "true").strip().lower()
    verify_ssl_bool = verify_ssl not in ("0", "false", "no")

    return _build_client(api_url, token, verify_ssl_bool)
