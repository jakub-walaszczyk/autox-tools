"""Shared S3 connection factory.

Builds a boto3 S3 client from either a ``S3Config`` object or environment
variables.  The *env_prefix* parameter selects which set of env vars to
read — ``""`` for the data-storage endpoint (``AWS_S3_ENDPOINT``, …) and
``"ARTIFACTS_"`` for the artifacts endpoint (``ARTIFACTS_AWS_S3_ENDPOINT``, …).
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Any

import boto3
from botocore.config import Config
from dotenv import find_dotenv, load_dotenv

if TYPE_CHECKING:
    from autox_tools.config._models import S3Config


def connect(cfg: S3Config | None = None, *, env_prefix: str = "") -> Any:
    """Build a boto3 S3 client from *cfg* or environment variables.

    When *cfg* is ``None``, credentials are read from env vars named
    ``{env_prefix}AWS_S3_ENDPOINT``, ``{env_prefix}AWS_ACCESS_KEY_ID``,
    ``{env_prefix}AWS_SECRET_ACCESS_KEY``, etc.
    """
    if cfg is not None:
        return boto3.client(
            "s3",
            endpoint_url=cfg.endpoint,
            aws_access_key_id=cfg.access_key_id,
            aws_secret_access_key=cfg.secret_access_key,
            region_name=cfg.region,
            verify=cfg.verify_tls,
            config=Config(s3={"addressing_style": "path"}),
        )

    load_dotenv(find_dotenv(usecwd=True))

    required = (
        f"{env_prefix}AWS_S3_ENDPOINT",
        f"{env_prefix}AWS_ACCESS_KEY_ID",
        f"{env_prefix}AWS_SECRET_ACCESS_KEY",
    )
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        sys.exit(f"Missing required environment variables: {', '.join(missing)}")

    verify_key = f"{env_prefix}S3_VERIFY_TLS"
    verify = os.getenv(verify_key, "true").lower() != "false"

    return boto3.client(
        "s3",
        endpoint_url=os.environ[f"{env_prefix}AWS_S3_ENDPOINT"],
        aws_access_key_id=os.environ[f"{env_prefix}AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ[f"{env_prefix}AWS_SECRET_ACCESS_KEY"],
        region_name=os.getenv(f"{env_prefix}AWS_DEFAULT_REGION", "us-east-1"),
        verify=verify,
        config=Config(s3={"addressing_style": "path"}),
    )
