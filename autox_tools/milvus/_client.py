"""Milvus connection factory driven by environment variables.

Required env vars (or .env file):
    MILVUS_HOST     — server hostname or IP (e.g. "localhost", "milvus.internal")
    MILVUS_PORT     — gRPC port (e.g. "19530")

Optional:
    MILVUS_USER     — authentication username
    MILVUS_PASSWORD — authentication password
    MILVUS_SECURE   — "true" to enable TLS (default: "false")
"""

from __future__ import annotations

import os
import sys

from dotenv import find_dotenv, load_dotenv
from pymilvus import MilvusClient

_REQUIRED_VARS = ("MILVUS_HOST", "MILVUS_PORT")


def connect() -> MilvusClient:
    """Build a ``MilvusClient`` from environment configuration."""
    load_dotenv(find_dotenv(usecwd=True))

    missing = [v for v in _REQUIRED_VARS if not os.getenv(v)]
    if missing:
        sys.exit(f"Missing required environment variables: {', '.join(missing)}")

    host = os.environ["MILVUS_HOST"]
    port = os.environ["MILVUS_PORT"]
    secure = os.getenv("MILVUS_SECURE", "false").lower() == "true"

    return MilvusClient(
        uri=f"{host}:{port}",
        user=os.getenv("MILVUS_USER", ""),
        password=os.getenv("MILVUS_PASSWORD", ""),
        secure=secure,
    )
