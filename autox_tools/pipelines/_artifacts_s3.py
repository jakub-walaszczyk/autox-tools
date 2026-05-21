"""S3 connection factory for pipeline artifacts storage.

Pipeline artifacts (evaluation results, notebooks, leaderboard HTML, RAG
patterns) are stored on a separate S3-compatible endpoint from the main
data-storage bucket used by the ``s3`` CLI tool.

Required env vars (or .env file):
    ARTIFACTS_AWS_S3_ENDPOINT       -- Artifacts S3 endpoint URL
    ARTIFACTS_AWS_ACCESS_KEY_ID     -- Access key
    ARTIFACTS_AWS_SECRET_ACCESS_KEY -- Secret key

Optional:
    ARTIFACTS_AWS_DEFAULT_REGION -- Region name (default: "us-east-1")
    ARTIFACTS_S3_VERIFY_TLS      -- Set to "false" to skip TLS certificate verification
"""

from __future__ import annotations

import os
import sys
from typing import Any

import boto3
from botocore.config import Config
from dotenv import find_dotenv, load_dotenv

_REQUIRED_VARS = (
    "ARTIFACTS_AWS_S3_ENDPOINT",
    "ARTIFACTS_AWS_ACCESS_KEY_ID",
    "ARTIFACTS_AWS_SECRET_ACCESS_KEY",
)


def connect() -> Any:
    """Build a boto3 S3 client for pipeline artifacts storage."""
    load_dotenv(find_dotenv(usecwd=True))

    missing = [v for v in _REQUIRED_VARS if not os.getenv(v)]
    if missing:
        sys.exit(f"Missing required environment variables: {', '.join(missing)}")

    verify = os.getenv("ARTIFACTS_S3_VERIFY_TLS", "true").lower() != "false"

    return boto3.client(
        "s3",
        endpoint_url=os.environ["ARTIFACTS_AWS_S3_ENDPOINT"],
        aws_access_key_id=os.environ["ARTIFACTS_AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["ARTIFACTS_AWS_SECRET_ACCESS_KEY"],
        region_name=os.getenv("ARTIFACTS_AWS_DEFAULT_REGION", "us-east-1"),
        verify=verify,
        config=Config(s3={"addressing_style": "path"}),
    )
