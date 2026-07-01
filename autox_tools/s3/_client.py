"""S3 connection factory for data storage.

Delegates to :mod:`autox_tools._s3_connect` with the default (empty)
env-var prefix, reading ``AWS_S3_ENDPOINT``, ``AWS_ACCESS_KEY_ID``, etc.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from autox_tools._s3_connect import connect as _shared_connect

if TYPE_CHECKING:
    from autox_tools.config._models import S3Config


def connect(cfg: S3Config | None = None) -> Any:
    """Build a boto3 S3 client from *cfg* or environment variables."""
    return _shared_connect(cfg)
