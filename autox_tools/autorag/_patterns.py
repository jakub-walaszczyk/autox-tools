"""Pattern discovery, per-pattern data fetching, and data models.

Discovers RAG pattern folders under a run's S3 prefix, downloads
``pattern.json`` from each, and assembles a unified ``RunPatternData``
object suitable for rich console display and PDF report generation.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime

from autox_tools._s3_utils import paginate_objects
from autox_tools.autorag._artifacts import extract_metrics
from autox_tools.autorag._resolver import ArtifactLocation, resolve
from autox_tools.pipelines._kfp import get_pipeline_name, get_run_state

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class PatternMetrics:
    """Metrics extracted from a single pattern's ``pattern.json``."""

    name: str
    metrics: dict[str, float]
    raw_data: dict[str, Any] = field(repr=False)


@dataclass
class RunPatternData:
    """All metric data collected for one pipeline run."""

    run_id: str
    display_name: str
    summary_metrics: dict[str, float]
    patterns: list[PatternMetrics]
    primary_metric: str | None
    source_key: str | None = None
    pipeline_name: str | None = None
    state: str | None = None
    created_at: datetime | None = None
    duration_seconds: float | None = None
    pipeline_params: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Natural sorting
# ---------------------------------------------------------------------------

_NATURAL_SPLIT = re.compile(r"(\d+)")


def natural_sort_key(name: str) -> list[int | str]:
    """Sort key that orders ``Pattern2`` before ``Pattern11``."""
    parts: list[int | str] = []
    for segment in _NATURAL_SPLIT.split(name):
        if segment.isdigit():
            parts.append(int(segment))
        else:
            parts.append(segment.lower())
    return parts


# ---------------------------------------------------------------------------
# Pattern discovery
# ---------------------------------------------------------------------------


def find_rag_patterns_prefix(
    s3_client: Any, bucket: str, key_prefix: str,
) -> str | None:
    """Discover the ``rag_patterns/`` base prefix by sampling object keys.

    The folder sits at an unpredictable depth (e.g.
    ``<prefix>/<component>/<task-id>/rag_patterns/``).
    """
    result = paginate_objects(s3_client, bucket, key_prefix, max_keys=200)
    for obj in result.get("Contents", []):
        key: str = obj["Key"]
        idx = key.find("/rag_patterns/")
        if idx >= 0:
            return key[: idx + len("/rag_patterns/")]
    return None


def discover_patterns(
    s3_client: Any, bucket: str, rag_prefix: str,
) -> list[str]:
    """Enumerate RAG pattern folder names under *rag_prefix*.

    Returns names in natural sort order so ``Pattern2`` appears before
    ``Pattern11``.
    """
    result = paginate_objects(s3_client, bucket, rag_prefix, delimiter="/")
    patterns: list[str] = []
    for cp in result.get("CommonPrefixes", []):
        name = cp["Prefix"][len(rag_prefix):].rstrip("/")
        if name:
            patterns.append(name)
    return sorted(patterns, key=natural_sort_key)


# ---------------------------------------------------------------------------
# Per-pattern data fetching
# ---------------------------------------------------------------------------


def _download_json(s3_client: Any, bucket: str, key: str) -> dict | list | None:
    """Download and parse a JSON file from S3.  Returns ``None`` on failure."""
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        result: dict | list = json.loads(response["Body"].read())
        return result
    except Exception:
        return None


def _extract_pattern_scores(data: dict) -> dict[str, float]:
    """Extract metric scores from a pattern's ``scores`` structure.

    Each entry under ``scores`` has ``mean``, ``ci_low``, ``ci_high``.
    Falls back to generic ``extract_metrics`` when no ``scores`` dict exists.
    """
    scores = data.get("scores")
    if isinstance(scores, dict):
        result: dict[str, float] = {}
        for name, score_data in scores.items():
            if isinstance(score_data, dict) and "mean" in score_data:
                mean = score_data["mean"]
                if isinstance(mean, (int, float)):
                    result[name] = float(mean)
        if result:
            return result
    return extract_metrics(data)


def _detect_final_score_metric(
    data: dict, metrics: dict[str, float],
) -> str | None:
    """Identify which score metric ``final_score`` corresponds to."""
    final = data.get("final_score")
    if not isinstance(final, (int, float)):
        return None
    for name, value in metrics.items():
        if abs(value - final) < 1e-9:
            return name
    return None


def fetch_pattern_metrics(
    s3_client: Any,
    bucket: str,
    rag_prefix: str,
    pattern_name: str,
) -> PatternMetrics | None:
    """Download ``pattern.json`` for one pattern and extract metrics."""
    key = f"{rag_prefix}{pattern_name}/pattern.json"
    data = _download_json(s3_client, bucket, key)
    if data is None or not isinstance(data, dict):
        return None
    metrics = _extract_pattern_scores(data)
    return PatternMetrics(name=pattern_name, metrics=metrics, raw_data=data)


def fetch_all_pattern_metrics(
    s3_client: Any, bucket: str, prefix: str,
) -> list[PatternMetrics]:
    """Discover all RAG patterns under *prefix* and fetch their metrics.

    Pattern downloads run concurrently (boto3 clients are thread-safe).
    """
    rag_prefix = find_rag_patterns_prefix(s3_client, bucket, prefix)
    if not rag_prefix:
        return []
    names = discover_patterns(s3_client, bucket, rag_prefix)
    if not names:
        return []

    def _fetch(name: str) -> PatternMetrics | None:
        return fetch_pattern_metrics(s3_client, bucket, rag_prefix, name)

    with ThreadPoolExecutor(max_workers=min(len(names), 8)) as pool:
        results = pool.map(_fetch, names)

    return [pm for pm in results if pm is not None]


# ---------------------------------------------------------------------------
# Primary metric detection
# ---------------------------------------------------------------------------

_PRIMARY_METRIC_CANDIDATES = [
    "answer_correctness",
    "accuracy",
    "f1_score",
    "f1",
    "faithfulness",
    "context_relevancy",
]


def detect_primary_metric(
    summary_metrics: dict[str, float],
    patterns: list[PatternMetrics] | None = None,
    pipeline_params: dict[str, Any] | None = None,
) -> str | None:
    """Detect the optimization metric for ranking patterns.

    Resolution order:
    1. Explicit ``optimization_metric`` from pipeline parameters.
    2. ``final_score`` value matching a pattern score mean.
    3. Well-known metric names (answer_correctness, accuracy, …).
    4. First non-excluded metric alphabetically.
    """
    from autox_tools.autorag._display import _is_excluded_metric

    pool = set(summary_metrics)
    if patterns:
        for p in patterns:
            pool.update(p.metrics)

    if pipeline_params:
        explicit = pipeline_params.get("optimization_metric")
        if explicit and explicit in pool:
            return explicit

    if patterns:
        for p in patterns:
            detected = _detect_final_score_metric(p.raw_data, p.metrics)
            if detected:
                return detected

    for candidate in _PRIMARY_METRIC_CANDIDATES:
        if candidate in pool:
            return candidate

    filtered = sorted(m for m in pool if not _is_excluded_metric(m))
    if filtered:
        return filtered[0]
    return None


# ---------------------------------------------------------------------------
# Name parsing
# ---------------------------------------------------------------------------


def parse_names(names_str: str | None) -> dict[str, str]:
    """Parse ``--names "id1=Label 1,id2=Label 2"`` into a lookup dict."""
    if not names_str:
        return {}
    result: dict[str, str] = {}
    for entry in names_str.split(","):
        entry = entry.strip()
        if "=" in entry:
            key, value = entry.split("=", 1)
            result[key.strip()] = value.strip()
    return result


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

_EVAL_RESULTS_SUBPATHS = [
    "evaluation_results.json",
    "evaluation/evaluation_results.json",
    "results/evaluation_results.json",
]

_METRICS_SUBPATHS = [
    "metrics.json",
    "results/metrics.json",
]


def _find_summary_results(
    s3_client: Any, bucket: str, prefix: str,
) -> tuple[dict | list | None, str | None]:
    """Locate and download the top-level evaluation results file.

    Tries well-known sub-paths, then falls back to a recursive scan.
    """
    norm = prefix.rstrip("/") + "/" if prefix and not prefix.endswith("/") else prefix
    for subpath in _EVAL_RESULTS_SUBPATHS + _METRICS_SUBPATHS:
        key = f"{norm}{subpath}"
        data = _download_json(s3_client, bucket, key)
        if data is not None:
            return data, key

    result = paginate_objects(s3_client, bucket, norm)
    eval_key: str | None = None
    metrics_key: str | None = None
    for obj in result.get("Contents", []):
        k: str = obj["Key"]
        basename = k.rsplit("/", 1)[-1]
        if basename == "evaluation_results.json" and eval_key is None:
            eval_key = k
        elif basename == "metrics.json" and metrics_key is None:
            metrics_key = k
        if eval_key:
            break

    chosen = eval_key or metrics_key
    if chosen:
        data = _download_json(s3_client, bucket, chosen)
        if data is not None:
            return data, chosen
    return None, None


def _extract_run_metadata(
    kfp_client: Any, run_id: str,
) -> dict[str, Any]:
    """Extract pipeline metadata from the KFP run object.

    Returns a dict with ``pipeline_name``, ``state``, ``created_at``,
    ``duration_seconds``, ``display_name``, and ``pipeline_params``.
    Never raises — returns partial data on failure.
    """
    result: dict[str, Any] = {
        "pipeline_name": None,
        "state": None,
        "created_at": None,
        "duration_seconds": None,
        "display_name": None,
        "pipeline_params": {},
    }
    try:
        run = kfp_client.get_run(run_id)
    except Exception:
        logger.debug("Could not fetch KFP run %s for metadata", run_id, exc_info=True)
        return result

    run_obj = getattr(run, "run", run)

    result["pipeline_name"] = get_pipeline_name(run_obj)
    result["state"] = get_run_state(run_obj)
    result["display_name"] = (
        getattr(run_obj, "display_name", None)
        or getattr(run_obj, "name", None)
    )
    result["created_at"] = getattr(run_obj, "created_at", None)
    finished_at = getattr(run_obj, "finished_at", None)
    if result["created_at"] and finished_at:
        result["duration_seconds"] = (finished_at - result["created_at"]).total_seconds()

    runtime_config = getattr(run_obj, "runtime_config", None)
    if runtime_config:
        params = getattr(runtime_config, "parameters", None) or {}
        if isinstance(params, dict):
            result["pipeline_params"] = {
                k: v for k, v in params.items() if k != "output"
            }

    return result


def collect_run_data(
    kfp_client: Any,
    s3_client: Any,
    run_id: str,
    *,
    explicit_prefix: str | None = None,
    explicit_bucket: str | None = None,
    names: dict[str, str] | None = None,
) -> RunPatternData | None:
    """Collect all metric data for a pipeline run.

    Resolves the S3 location, downloads summary results and per-pattern
    ``pattern.json`` files, and returns a unified ``RunPatternData``.
    Returns ``None`` if the artifact location cannot be resolved.
    """
    try:
        location: ArtifactLocation | None = resolve(
            kfp_client, s3_client, run_id,
            explicit_prefix=explicit_prefix,
            explicit_bucket=explicit_bucket,
        )
    except Exception:
        logger.debug("Artifact resolution failed for run %s", run_id, exc_info=True)
        return None
    if not location:
        return None

    data, source_key = _find_summary_results(
        s3_client, location.bucket, location.prefix,
    )
    summary_metrics = extract_metrics(data) if data else {}

    patterns = fetch_all_pattern_metrics(
        s3_client, location.bucket, location.prefix,
    )

    if not summary_metrics and patterns:
        best = max(
            patterns,
            key=lambda p: max(p.metrics.values()) if p.metrics else 0,
        )
        summary_metrics = dict(best.metrics)

    meta = _extract_run_metadata(kfp_client, run_id)

    display_name = run_id
    if names and run_id in names:
        display_name = names[run_id]
    elif meta["display_name"]:
        display_name = meta["display_name"]

    primary = detect_primary_metric(summary_metrics, patterns, meta["pipeline_params"])

    return RunPatternData(
        run_id=run_id,
        display_name=display_name,
        summary_metrics=summary_metrics,
        patterns=patterns,
        primary_metric=primary,
        source_key=source_key,
        pipeline_name=meta["pipeline_name"],
        state=meta["state"],
        created_at=meta["created_at"],
        duration_seconds=meta["duration_seconds"],
        pipeline_params=meta["pipeline_params"],
    )
