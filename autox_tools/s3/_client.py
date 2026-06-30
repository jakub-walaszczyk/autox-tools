"""S3 connection factory.

Accepts an optional ``S3Config`` for profile-based configuration.
Falls back to environment variables when no config is provided.

Required env vars (or .env file) for the fallback path:
    AWS_S3_ENDPOINT       -- S3 endpoint URL (e.g. "https://minio.apps.cluster.example.com")
    AWS_ACCESS_KEY_ID     -- Access key
    AWS_SECRET_ACCESS_KEY -- Secret key

Optional:
    AWS_DEFAULT_REGION -- Region name (default: "us-east-1")
    S3_VERIFY_TLS      -- Set to "false" to skip TLS certificate verification
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

_REQUIRED_VARS = ("AWS_S3_ENDPOINT", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")


def connect(cfg: S3Config | None = None) -> Any:
    """Build a boto3 S3 client from *cfg* or environment variables."""
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

    missing = [v for v in _REQUIRED_VARS if not os.getenv(v)]
    if missing:
        sys.exit(f"Missing required environment variables: {', '.join(missing)}")

    verify = os.getenv("S3_VERIFY_TLS", "true").lower() != "false"

    return boto3.client(
        "s3",
        endpoint_url=os.environ["AWS_S3_ENDPOINT"],
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        verify=verify,
        config=Config(s3={"addressing_style": "path"}),
    )
