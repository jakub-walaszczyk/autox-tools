"""KFP client factory.

Accepts an optional ``RhoaiConfig`` for profile-based configuration.
Falls back to environment variables when no config is provided.

Required env vars (or .env file) for the fallback path::

    RHOAI_KFP_URL          KFP API endpoint URL (must end with ``/``)
    RHOAI_TOKEN            Bearer token for authentication
    RHOAI_PROJECT_NAME     OpenShift namespace where pipelines run

Optional::

    KFP_VERIFY_SSL         Set to ``false`` to skip TLS verification (default: ``true``)
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

from dotenv import find_dotenv, load_dotenv

if TYPE_CHECKING:
    from autox_tools.config._models import RhoaiConfig

_REQUIRED_VARS = ("RHOAI_KFP_URL", "RHOAI_TOKEN", "RHOAI_PROJECT_NAME")


def connect(cfg: RhoaiConfig | None = None):
    """Build a ``kfp.Client`` from *cfg* or environment variables."""
    import kfp

    if cfg is not None:
        host = cfg.kfp_url
        if not host.endswith("/"):
            host += "/"
        return kfp.Client(
            host=host,
            namespace=cfg.project_name,
            existing_token=cfg.token,
            verify_ssl=cfg.verify_ssl,
        )

    load_dotenv(find_dotenv(usecwd=True))

    missing = [v for v in _REQUIRED_VARS if not os.getenv(v)]
    if missing:
        sys.exit(f"Missing required environment variables: {', '.join(missing)}")

    host = os.environ["RHOAI_KFP_URL"]
    if not host.endswith("/"):
        host += "/"

    verify_ssl = os.getenv("KFP_VERIFY_SSL", "true").lower() != "false"

    return kfp.Client(
        host=host,
        namespace=os.environ["RHOAI_PROJECT_NAME"],
        existing_token=os.environ["RHOAI_TOKEN"],
        verify_ssl=verify_ssl,
    )
