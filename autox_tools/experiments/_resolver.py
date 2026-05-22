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

import os
from dataclasses import dataclass
from typing import Any

from autox_tools.pipelines.cli import _get_pipeline_name
from autox_tools.s3.cli import _paginate_objects


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
            return None
        return ArtifactLocation(bucket=bucket, prefix=explicit_prefix, source="explicit")

    run = kfp_client.get_run(run_id)
    run_obj = getattr(run, "run", run)

    location = _try_run_params(run_obj)
    if location:
        return _refine_prefix(s3_client, location, run_id)

    pipeline_name = _get_pipeline_name(run_obj)
    bucket = explicit_bucket or os.getenv("ARTIFACTS_S3_BUCKET", "")
    if bucket:
        location = _try_scan(s3_client, bucket, run_id, pipeline_name)
        if location:
            return location

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

    if artifact_root.startswith("s3://"):
        cleaned = artifact_root[5:]
        parts = cleaned.split("/", 1)
        bucket = parts[0]
        prefix = parts[1] if len(parts) == 2 else ""
        return ArtifactLocation(bucket=bucket, prefix=prefix, source="run_params")

    bucket = os.getenv("ARTIFACTS_S3_BUCKET", "")
    if bucket:
        return ArtifactLocation(bucket=bucket, prefix=artifact_root, source="run_params")

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
        candidates.append(f"{pipeline_name}/{run_id}/")

    for prefix in candidates:
        probe = _paginate_objects(s3_client, bucket, prefix, max_keys=1)
        if probe.get("Contents"):
            return ArtifactLocation(bucket=bucket, prefix=prefix, source="scan")

    return None
