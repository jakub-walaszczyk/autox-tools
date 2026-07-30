"""Milvus connection factory.

Accepts an optional ``MilvusConfig`` for profile-based configuration.
Falls back to environment variables when no config is provided.

Required env vars (or .env file) for the fallback path:
    MILVUS_HOST     — server hostname or IP (e.g. "localhost", "milvus.internal")
    MILVUS_PORT     — gRPC port (e.g. "19530")

Optional:
    MILVUS_USER      — authentication username
    MILVUS_PASSWORD  — authentication password
    MILVUS_SECURE    — "true" to enable TLS (default: "false")
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

_REQUIRED_VARS = ("MILVUS_HOST", "MILVUS_PORT")


def _build_uri(host: str, port: int | str, secure: bool) -> tuple[str, bool]:
    """Return a scheme-qualified ``uri`` and the effective ``secure`` flag.

    pymilvus requires the URI to start with a scheme (``http``/``https``).
    When *host* already carries one it wins and dictates TLS; otherwise the
    scheme is derived from *secure* so a bare hostname still connects.
    """
    host = host.strip().rstrip("/")
    if "://" in host:
        secure = host.lower().startswith("https://")
    else:
        host = f"{'https' if secure else 'http'}://{host}"
    return f"{host}:{port}", secure


def connect(cfg: MilvusConfig | None = None) -> MilvusClient:
    """Build a ``MilvusClient`` from *cfg* or environment variables."""
    if cfg is not None:
        uri, secure = _build_uri(cfg.host, cfg.port, cfg.secure)
        return MilvusClient(
            uri=uri,
            user=cfg.user,
            password=cfg.password,
            secure=secure,
            server_pem_path=cfg.server_pem_path,
        )

    load_dotenv(find_dotenv(usecwd=True))

    missing = [v for v in _REQUIRED_VARS if not os.getenv(v)]
    if missing:
        sys.exit(f"Missing required environment variables: {', '.join(missing)}")

    uri, secure = _build_uri(
        os.environ["MILVUS_HOST"],
        os.environ["MILVUS_PORT"],
        os.getenv("MILVUS_SECURE", "false").lower() == "true",
    )

    return MilvusClient(
        uri=uri,
        user=os.getenv("MILVUS_USER", ""),
        password=os.getenv("MILVUS_PASSWORD", ""),
        secure=secure,
        server_pem_path=os.getenv("MILVUS_SERVER_PEM_PATH", ""),
    )
