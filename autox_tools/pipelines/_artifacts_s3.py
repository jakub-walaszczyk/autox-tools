"""S3 connection factory for pipeline artifacts storage.

Delegates to :mod:`autox_tools._s3_connect` with the ``ARTIFACTS_``
env-var prefix, reading ``ARTIFACTS_AWS_S3_ENDPOINT``,
``ARTIFACTS_AWS_ACCESS_KEY_ID``, etc.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from autox_tools._s3_connect import connect as _shared_connect

if TYPE_CHECKING:
    from autox_tools.config._models import S3Config


def connect(cfg: S3Config | None = None) -> Any:
    """Build a boto3 S3 client from *cfg* or environment variables."""
    return _shared_connect(cfg, env_prefix="ARTIFACTS_")
