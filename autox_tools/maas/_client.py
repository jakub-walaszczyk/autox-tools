"""OpenShift MaaS client factory and endpoint-derivation helpers.

OpenShift Model-as-a-Service (MaaS) exposes an OpenAI-compatible surface split
across two kinds of endpoints:

* A **general** endpoint at ``{base_url}/maas-api/v1`` that serves the model
  *listing* (``GET /models``) but not inference.
* A **per-model** endpoint at ``{scheme}://{host}/{owned_by}/v1`` that serves
  chat/embedding inference for a single model. The ``owned_by`` path prefix is
  read from each listed model, so a model must be discovered via the general
  endpoint before it can be queried.

This module normalizes connection settings from either an :class:`MaasConfig`
profile or environment variables, and builds the two client flavours on demand.

Environment fallback (used when no ``.autox.yaml`` profile/target applies):

    MAAS_BASE_URL    -- MaaS host root, e.g. "https://maas.apps.<cluster>" (required)
    MAAS_API_KEY     -- API key/token for authentication (optional but usually required)
    MAAS_VERIFY_TLS  -- "false"/"0"/"no" to skip TLS verification (default: verify)
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx
from dotenv import find_dotenv, load_dotenv
from openai import OpenAI

if TYPE_CHECKING:
    from autox_tools.config._models import MaasConfig

# Path suffix appended to the configured host root to reach the model-listing API.
_LIST_API_SUFFIX = "/maas-api/v1"

# OpenAI's client rejects an empty API key; MaaS deployments that don't require
# auth still expect a non-empty placeholder (mirrors the vLLM/OpenAI convention).
_API_KEY_PLACEHOLDER = "EMPTY"


@dataclass(frozen=True)
class MaasSettings:
    """Normalized connection settings shared by the general and per-model clients.

    Attributes
    ----------
    base_url : str
        MaaS host root *without* any API path (e.g. ``https://maas.apps.<cluster>``).
        Both the listing endpoint and per-model endpoints are derived from it.
    api_key : str
        API key/token reused for every derived client. May be empty when the
        deployment does not require authentication.
    verify_tls : bool
        Whether to verify the server's TLS certificate. Set ``False`` for
        clusters exposing self-signed routes.
    """

    base_url: str
    api_key: str = ""
    verify_tls: bool = True


def resolve_settings(cfg: MaasConfig | None = None) -> MaasSettings:
    """Resolve :class:`MaasSettings` from a profile config or environment variables.

    Parameters
    ----------
    cfg : MaasConfig | None
        Profile-resolved config. When ``None``, settings are read from the
        environment (``.env`` is loaded first for convenience).

    Returns
    -------
    MaasSettings
        Normalized, validated connection settings.
    """
    if cfg is not None:
        return MaasSettings(base_url=cfg.base_url, api_key=cfg.api_key, verify_tls=cfg.verify_tls)

    load_dotenv(find_dotenv(usecwd=True))
    base_url = os.getenv("MAAS_BASE_URL", "")
    if not base_url:
        sys.exit("Missing required environment variable: MAAS_BASE_URL")

    verify = os.getenv("MAAS_VERIFY_TLS", "true").lower() not in ("false", "0", "no")
    return MaasSettings(base_url=base_url, api_key=os.getenv("MAAS_API_KEY", ""), verify_tls=verify)


def list_endpoint(base_url: str) -> str:
    """Return the model-listing endpoint for a MaaS host root.

    Appends ``/maas-api/v1`` unless *base_url* already carries it, so a config
    value with or without the suffix resolves to the same endpoint.
    """
    trimmed = base_url.rstrip("/")
    if trimmed.endswith(_LIST_API_SUFFIX):
        return trimmed
    return trimmed + _LIST_API_SUFFIX


def model_endpoint(base_url: str, owned_by: str) -> str:
    """Derive a per-model inference endpoint from the host root and a model's ``owned_by``.

    The scheme and host are taken from *base_url*; any path it carries is
    discarded. The result is ``{scheme}://{host}/{owned_by}/v1``.

    Parameters
    ----------
    base_url : str
        MaaS host root (or any URL sharing the same scheme/host).
    owned_by : str
        ``owned_by`` value of a listed model (e.g. ``ai-eng-cracow/qwen3-8b``).

    Returns
    -------
    str
        The model's OpenAI-compatible base URL, including the ``/v1`` suffix.
    """
    parts = urlsplit(base_url)
    return f"{parts.scheme}://{parts.netloc}/{owned_by.strip('/')}/v1"


def _make_client(base_url: str, settings: MaasSettings) -> OpenAI:
    """Build an :class:`~openai.OpenAI` client for *base_url* honouring TLS settings."""
    http_client = httpx.Client(verify=False) if not settings.verify_tls else None
    return OpenAI(
        base_url=base_url,
        api_key=settings.api_key or _API_KEY_PLACEHOLDER,
        http_client=http_client,
    )


def connect(settings: MaasSettings) -> OpenAI:
    """Build the general MaaS client used to list and inspect models.

    The client points at the ``/maas-api/v1`` listing endpoint. Connectivity is
    not probed here; errors surface on the first request.
    """
    return _make_client(list_endpoint(settings.base_url), settings)


def model_client(settings: MaasSettings, owned_by: str) -> OpenAI:
    """Build a per-model inference client for the model identified by *owned_by*."""
    return _make_client(model_endpoint(settings.base_url, owned_by), settings)
