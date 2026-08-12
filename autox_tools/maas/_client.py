"""OpenShift MaaS client factory and endpoint-derivation helpers.

OpenShift Model-as-a-Service (MaaS) exposes a single OpenAI-compatible base URL
ending in ``/v1``. That one endpoint serves both the model *listing*
(``GET /models``) and inference (chat/embeddings); the target model is selected
per request via the ``model`` parameter, so no per-model URL is needed.

This module normalizes connection settings from either an :class:`MaasConfig`
profile or environment variables, and builds the client on demand.

Environment fallback (used when no ``.autox.yaml`` profile/target applies):

    MAAS_BASE_URL    -- OpenAI-compatible base URL ending in "/v1",
                        e.g. "https://maas.apps.<cluster>/v1" (required)
    MAAS_API_KEY     -- API key/token for authentication (optional but usually required)
    MAAS_VERIFY_TLS  -- "false"/"0"/"no" to skip TLS verification (default: verify)
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from dotenv import find_dotenv, load_dotenv
from openai import OpenAI

if TYPE_CHECKING:
    from autox_tools.config._models import MaasConfig

# OpenAI-compatible API version suffix. MaaS serves listing and inference from a
# single base URL ending in "/v1"; the suffix is optional in config and added if
# missing so both forms resolve to the same endpoint.
_API_VERSION_SUFFIX = "/v1"

# OpenAI's client rejects an empty API key; MaaS deployments that don't require
# auth still expect a non-empty placeholder (mirrors the vLLM/OpenAI convention).
_API_KEY_PLACEHOLDER = "EMPTY"


@dataclass(frozen=True)
class MaasSettings:
    """Normalized connection settings for the MaaS client.

    Attributes
    ----------
    base_url : str
        OpenAI-compatible base URL ending in ``/v1``
        (e.g. ``https://maas.apps.<cluster>/v1``). The ``/v1`` suffix is optional
        and added automatically when missing.
    api_key : str
        API key/token for authentication. May be empty when the deployment does
        not require it.
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


def api_endpoint(base_url: str) -> str:
    """Return the OpenAI-compatible base URL, ensuring a single trailing ``/v1``.

    Accepts a configured value with or without the ``/v1`` suffix so both forms
    resolve to the same endpoint.
    """
    trimmed = base_url.rstrip("/")
    if trimmed.endswith(_API_VERSION_SUFFIX):
        return trimmed
    return trimmed + _API_VERSION_SUFFIX


def _make_client(base_url: str, settings: MaasSettings) -> OpenAI:
    """Build an :class:`~openai.OpenAI` client for *base_url* honouring TLS settings."""
    http_client = httpx.Client(verify=False) if not settings.verify_tls else None
    return OpenAI(
        base_url=base_url,
        api_key=settings.api_key or _API_KEY_PLACEHOLDER,
        http_client=http_client,
    )


def connect(settings: MaasSettings) -> OpenAI:
    """Build the MaaS client used to list, inspect, and query models.

    All requests share one OpenAI-compatible base URL (ending in ``/v1``); the
    target model is selected per request via the ``model`` parameter.
    Connectivity is not probed here; errors surface on the first request.
    """
    return _make_client(api_endpoint(settings.base_url), settings)
