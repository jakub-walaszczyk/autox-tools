"""Milvus connection factory.

Accepts an optional ``MilvusConfig`` for profile-based configuration.
Falls back to environment variables when no config is provided.

Required env vars (or .env file) for the fallback path:
    MILVUS_URI       — server URI (e.g. "http://localhost:19530")

Optional:
    MILVUS_TOKEN     — API key or "username:password" (empty for no authentication)
    MILVUS_SECURE    — "true" to enable TLS (HTTPS URIs enable it automatically)
    MILVUS_SERVER_PEM_PATH — path to the server/CA PEM cert for one-way TLS
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

from dotenv import find_dotenv, load_dotenv
from pymilvus import MilvusClient

if TYPE_CHECKING:
    from autox_tools.config._models import MilvusConfig

_REQUIRED_VARS = ("MILVUS_URI",)


def connect(cfg: MilvusConfig | None = None) -> MilvusClient:
    """Build a ``MilvusClient`` from *cfg* or environment variables."""
    if cfg is not None:
        return MilvusClient(
            uri=cfg.uri,
            token=cfg.token,
            secure=cfg.secure or cfg.uri.lower().startswith("https://"),
            server_pem_path=cfg.server_pem_path,
        )

    load_dotenv(find_dotenv(usecwd=True))

    missing = [v for v in _REQUIRED_VARS if not os.getenv(v)]
    if missing:
        sys.exit(f"Missing required environment variables: {', '.join(missing)}")

    uri = os.environ["MILVUS_URI"]

    return MilvusClient(
        uri=uri,
        token=os.getenv("MILVUS_TOKEN", ""),
        secure=uri.lower().startswith("https://") or os.getenv("MILVUS_SECURE", "false").lower() == "true",
        server_pem_path=os.getenv("MILVUS_SERVER_PEM_PATH", ""),
    )
