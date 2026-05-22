"""Kubernetes client factory for secret management.

Resolves the K8S API URL from environment variables:

1. ``K8S_API_URL`` -- explicit override (preferred)
2. Derived from ``RHOAI_KFP_URL`` using OCP/ROSA hostname conventions

Authentication uses ``RHOAI_TOKEN`` (OpenShift bearer token).
TLS verification follows ``KFP_VERIFY_SSL`` (default: true).
"""

from __future__ import annotations

import os
import sys
from urllib.parse import urlparse

import urllib3
from dotenv import find_dotenv, load_dotenv
from kubernetes import client as k8s_client


def _derive_k8s_api_url(kfp_url: str) -> str | None:
    """Derive K8S API URL from a KFP route URL.

    Returns ``None`` when the hostname does not follow the expected
    ``*.apps.<cluster>`` convention.
    """
    hostname = urlparse(kfp_url).hostname or ""
    apps_idx = hostname.find(".apps.")
    if apps_idx < 0:
        return None

    base_domain = hostname[apps_idx + len(".apps.") :]
    is_rosa = base_domain.startswith("rosa.")
    if is_rosa:
        base_domain = base_domain[len("rosa.") :]
    default_port = 443 if is_rosa else 6443
    port = os.getenv("K8S_API_PORT", str(default_port)).strip()

    return f"https://api.{base_domain}:{port}"


def connect() -> k8s_client.CoreV1Api:
    """Build a Kubernetes ``CoreV1Api`` client from environment configuration."""
    load_dotenv(find_dotenv(usecwd=True))

    token = os.getenv("RHOAI_TOKEN")
    if not token:
        sys.exit("Missing RHOAI_TOKEN for Kubernetes API access.")

    api_url = os.getenv("K8S_API_URL", "").strip().rstrip("/")
    if not api_url:
        kfp_url = os.getenv("RHOAI_KFP_URL", "")
        if kfp_url:
            api_url = _derive_k8s_api_url(kfp_url) or ""
        if not api_url:
            sys.exit(
                "K8S API URL could not be resolved.\n"
                "Set K8S_API_URL explicitly or provide RHOAI_KFP_URL "
                "so the URL can be derived from the cluster hostname."
            )

    verify_ssl = os.getenv("KFP_VERIFY_SSL", "true").strip().lower()
    verify_ssl_bool = verify_ssl not in ("0", "false", "no")

    config = k8s_client.Configuration()
    config.host = api_url
    config.api_key = {"authorization": f"Bearer {token}"}
    config.verify_ssl = verify_ssl_bool

    if not verify_ssl_bool:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    return k8s_client.CoreV1Api(k8s_client.ApiClient(config))
