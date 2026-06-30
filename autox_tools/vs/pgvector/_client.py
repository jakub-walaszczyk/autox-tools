"""PostgreSQL/pgvector connection factory.

Accepts an optional ``PgvectorConfig`` for profile-based configuration.
Falls back to environment variables when no config is provided.

Required env vars (or .env file) for the fallback path:
    PGVECTOR_HOST     -- server hostname or IP (e.g. "localhost")
    PGVECTOR_PORT     -- PostgreSQL port (e.g. "5432")
    PGVECTOR_DATABASE -- database name

Optional:
    PGVECTOR_USER     -- authentication username
    PGVECTOR_PASSWORD -- authentication password
    PGVECTOR_SSLMODE  -- SSL mode (default: "prefer")
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

from dotenv import find_dotenv, load_dotenv
from psycopg import Connection

if TYPE_CHECKING:
    from autox_tools.config._models import PgvectorConfig

_REQUIRED_VARS = ("PGVECTOR_HOST", "PGVECTOR_PORT", "PGVECTOR_DATABASE")


def connect(cfg: PgvectorConfig | None = None) -> Connection:
    """Build a ``psycopg.Connection`` from *cfg* or environment variables."""
    if cfg is not None:
        return Connection.connect(
            host=cfg.host,
            port=cfg.port,
            dbname=cfg.database,
            user=cfg.user,
            password=cfg.password,
            sslmode=cfg.sslmode,
            autocommit=True,
        )

    load_dotenv(find_dotenv(usecwd=True))

    missing = [v for v in _REQUIRED_VARS if not os.getenv(v)]
    if missing:
        sys.exit(f"Missing required environment variables: {', '.join(missing)}")

    return Connection.connect(
        host=os.environ["PGVECTOR_HOST"],
        port=int(os.environ["PGVECTOR_PORT"]),
        dbname=os.environ["PGVECTOR_DATABASE"],
        user=os.getenv("PGVECTOR_USER", ""),
        password=os.getenv("PGVECTOR_PASSWORD", ""),
        sslmode=os.getenv("PGVECTOR_SSLMODE", "prefer"),
        autocommit=True,
    )
