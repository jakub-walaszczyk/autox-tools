"""KFP client factory driven by environment variables.

Required env vars (or .env file)::

    RHOAI_KFP_URL          KFP API endpoint URL (must end with ``/``)
    RHOAI_TOKEN            Bearer token for authentication
    RHOAI_PROJECT_NAME     OpenShift namespace where pipelines run

Optional::

    KFP_VERIFY_SSL         Set to ``false`` to skip TLS verification (default: ``true``)
"""

from __future__ import annotations

import os
import sys

from dotenv import find_dotenv, load_dotenv

_REQUIRED_VARS = ("RHOAI_KFP_URL", "RHOAI_TOKEN", "RHOAI_PROJECT_NAME")


def connect():
    """Build a ``kfp.Client`` from environment configuration."""
    import kfp

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
