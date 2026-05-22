"""OGX client connection factory driven by environment variables.

Required env vars (or .env file):
    OGX_CLIENT_BASE_URL -- OGX server base URL (e.g. "https://ogx.apps.cluster.example.com")

Optional:
    OGX_CLIENT_API_KEY  -- API key for authentication
"""

from __future__ import annotations

import os
import sys

from dotenv import find_dotenv, load_dotenv
from ogx_client import OgxClient

_REQUIRED_VARS = ("OGX_CLIENT_BASE_URL",)


def connect() -> OgxClient:
    """Build an ``OgxClient`` from environment configuration."""
    load_dotenv(find_dotenv(usecwd=True))

    missing = [v for v in _REQUIRED_VARS if not os.getenv(v)]
    if missing:
        sys.exit(f"Missing required environment variables: {', '.join(missing)}")

    return OgxClient(
        base_url=os.environ["OGX_CLIENT_BASE_URL"],
        api_key=os.getenv("OGX_CLIENT_API_KEY"),
    )
