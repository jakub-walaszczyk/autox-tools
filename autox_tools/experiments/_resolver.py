"""Resolve S3 artifact locations for a KFP pipeline run.

Tries multiple strategies in order of reliability:

1. **Explicit override** -- user-supplied ``--prefix`` / ``--bucket``.
2. **KFP run metadata** -- ``runtime_config.pipeline_root`` or pipeline
   parameters containing an S3 path.
3. **Convention-based scan** -- probe common prefix patterns in the
   artifacts bucket.

Each strategy returns an ``ArtifactLocation`` on success or ``None`` to
fall through to the next.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from autox_tools.pipelines.cli import _get_pipeline_name
from autox_tools.s3.cli import _paginate_objects

logger = logging.getLogger(__name__)


@dataclass
class ArtifactLocation:
    """Resolved S3 location for a run's artifacts."""

    bucket: str
    prefix: str
    source: str  # "run_params", "scan", "explicit"


def resolve(
    kfp_client: Any,
    s3_client: Any,
    run_id: str,
    *,
    explicit_prefix: str | None = None,
    explicit_bucket: str | None = None,
) -> ArtifactLocation | None:
    """Resolve the S3 artifact location for a pipeline run.

    Tries multiple strategies in order of reliability.  Returns ``None``
    if no artifacts can be located.
    """
    if explicit_prefix is not None:
        bucket = explicit_bucket or os.getenv("ARTIFACTS_S3_BUCKET", "")
        if not bucket:
            logger.debug("Explicit prefix given but no bucket (set ARTIFACTS_S3_BUCKET)")
            return None
        return ArtifactLocation(bucket=bucket, prefix=explicit_prefix, source="explicit")

    run = kfp_client.get_run(run_id)
    run_obj = getattr(run, "run", run)

    location = _try_run_params(run_obj)
    if location:
        logger.debug("Resolved via run params: s3://%s/%s", location.bucket, location.prefix)
        return _refine_prefix(s3_client, location, run_id)

    logger.debug("Run params did not yield a location for %s", run_id)

    pipeline_name = _get_pipeline_name(run_obj)
    bucket = explicit_bucket or os.getenv("ARTIFACTS_S3_BUCKET", "")
    if bucket:
        location = _try_scan(s3_client, bucket, run_id, pipeline_name)
        if location:
            logger.debug("Resolved via scan: s3://%s/%s", location.bucket, location.prefix)
            return location
        logger.debug(
            "Scan found no artifacts for %s in bucket %s (pipeline=%s)",
            run_id, bucket, pipeline_name,
        )
    else:
        logger.debug("No bucket available for scan (set ARTIFACTS_S3_BUCKET)")

    return None


def _try_run_params(run_obj: Any) -> ArtifactLocation | None:
    """Extract artifact location from KFP run parameters."""
    runtime_config = getattr(run_obj, "runtime_config", None)
    if not runtime_config:
        return None

    artifact_root = getattr(runtime_config, "pipeline_root", None)
    if not artifact_root:
        params = getattr(runtime_config, "parameters", {}) or {}
        if isinstance(params, dict):
            artifact_root = params.get("output", None)

    if not artifact_root:
        return None

    parsed = _parse_object_url(artifact_root)
    if parsed:
        return ArtifactLocation(bucket=parsed[0], prefix=parsed[1], source="run_params")

    bucket = os.getenv("ARTIFACTS_S3_BUCKET", "")
    if bucket:
        return ArtifactLocation(bucket=bucket, prefix=artifact_root, source="run_params")

    return None


def _parse_object_url(url: str) -> tuple[str, str] | None:
    """Extract ``(bucket, prefix)`` from an S3-style URL.

    Handles ``s3://``, ``minio://``, and ``https://<host>/<bucket>/...``
    endpoint formats.  Returns ``None`` for unrecognised schemes.
    """
    for scheme in ("s3://", "minio://"):
        if url.startswith(scheme):
            cleaned = url[len(scheme):]
            parts = cleaned.split("/", 1)
            return parts[0], (parts[1] if len(parts) == 2 else "")

    if url.startswith("https://") or url.startswith("http://"):
        without_scheme = url.split("://", 1)[1]
        segments = without_scheme.split("/", 2)
        if len(segments) >= 2:
            return segments[1], (segments[2] if len(segments) == 3 else "")

    return None


def _refine_prefix(
    s3_client: Any, location: ArtifactLocation, run_id: str,
) -> ArtifactLocation:
    """Narrow prefix to include run ID when not already present."""
    if run_id in location.prefix:
        return location

    candidate = f"{location.prefix}{run_id}/"
    probe = _paginate_objects(s3_client, location.bucket, candidate, max_keys=1)
    if probe.get("Contents"):
        return ArtifactLocation(
            bucket=location.bucket,
            prefix=candidate,
            source=location.source,
        )
    return location


_SCAN_TEMPLATES = [
    "artifacts/{run_id}/",
    "{run_id}/",
    "pipeline_runs/{run_id}/",
    "runs/{run_id}/",
]

_PIPELINE_SCAN_TEMPLATES = [
    "{pipeline_name}/{run_id}/",
    "pipelines/{pipeline_name}/{run_id}/",
    "{pipeline_name}/runs/{run_id}/",
]


def _try_scan(
    s3_client: Any,
    bucket: str,
    run_id: str,
    pipeline_name: str | None = None,
) -> ArtifactLocation | None:
    """Probe common prefix patterns for artifacts."""
    candidates = [t.format(run_id=run_id) for t in _SCAN_TEMPLATES]
    if pipeline_name:
        candidates.extend(
            t.format(pipeline_name=pipeline_name, run_id=run_id)
            for t in _PIPELINE_SCAN_TEMPLATES
        )

    for prefix in candidates:
        probe = _paginate_objects(s3_client, bucket, prefix, max_keys=1)
        if probe.get("Contents"):
            return ArtifactLocation(bucket=bucket, prefix=prefix, source="scan")

    return None
