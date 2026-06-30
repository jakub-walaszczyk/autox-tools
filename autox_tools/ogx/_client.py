"""OGX client connection factory.

Accepts an optional ``OgxConfig`` for profile-based configuration.
Falls back to environment variables when no config is provided.

Required env vars (or .env file) for the fallback path:
    OGX_CLIENT_BASE_URL -- OGX server base URL (e.g. "https://ogx.apps.cluster.example.com")

Optional:
    OGX_CLIENT_API_KEY  -- API key for authentication
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

from dotenv import find_dotenv, load_dotenv
from ogx_client import OgxClient

if TYPE_CHECKING:
    from autox_tools.config._models import OgxConfig

_REQUIRED_VARS = ("OGX_CLIENT_BASE_URL",)


def connect(cfg: OgxConfig | None = None) -> OgxClient:
    """Build an ``OgxClient`` from *cfg* or environment variables."""
    if cfg is not None:
        return OgxClient(
            base_url=cfg.base_url,
            api_key=cfg.api_key or None,
        )

    load_dotenv(find_dotenv(usecwd=True))

    missing = [v for v in _REQUIRED_VARS if not os.getenv(v)]
    if missing:
        sys.exit(f"Missing required environment variables: {', '.join(missing)}")

    return OgxClient(
        base_url=os.environ["OGX_CLIENT_BASE_URL"],
        api_key=os.getenv("OGX_CLIENT_API_KEY"),
    )
