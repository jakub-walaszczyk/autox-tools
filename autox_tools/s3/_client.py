"""S3 connection factory driven by environment variables.

Required env vars (or .env file):
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
from typing import Any

import boto3
from dotenv import find_dotenv, load_dotenv

_REQUIRED_VARS = ("AWS_S3_ENDPOINT", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")


def connect() -> Any:
    """Build a boto3 S3 client from environment configuration."""
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
    )
