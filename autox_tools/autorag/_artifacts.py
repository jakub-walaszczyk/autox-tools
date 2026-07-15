"""Artifact categorization and metric extraction for experiment results.

Categorizes S3 objects by their role in the experiment pipeline (evaluation
results, notebooks, leaderboard, etc.) and extracts numeric metrics from
evaluation result files.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from autox_tools._s3_utils import paginate_objects


class ArtifactCategory(Enum):
    EVALUATION_RESULTS = "evaluation_results"
    INDEXING_NOTEBOOK = "indexing_notebook"
    INFERENCE_NOTEBOOK = "inference_notebook"
    LEADERBOARD = "leaderboard"
    RAG_PATTERN = "rag_pattern"
    MODEL_METRICS = "model_metrics"
    RESPONSE_BODY = "response_body"
    OTHER = "other"


_CATEGORY_LABELS: dict[ArtifactCategory, str] = {
    ArtifactCategory.EVALUATION_RESULTS: "Evaluation Results",
    ArtifactCategory.INDEXING_NOTEBOOK: "Indexing Notebooks",
    ArtifactCategory.INFERENCE_NOTEBOOK: "Inference Notebooks",
    ArtifactCategory.LEADERBOARD: "Leaderboard",
    ArtifactCategory.RAG_PATTERN: "RAG Patterns",
    ArtifactCategory.MODEL_METRICS: "Model Metrics",
    ArtifactCategory.RESPONSE_BODY: "Response Body",
    ArtifactCategory.OTHER: "Other",
}


@dataclass
class CategorizedArtifact:
    """An S3 object tagged with its experiment role."""

    key: str
    category: ArtifactCategory
    size_bytes: int
    last_modified: str


def categorize(key: str) -> ArtifactCategory:
    """Categorize an S3 object key by its role in the experiment."""
    filename = key.rsplit("/", 1)[-1].lower()
    key_lower = key.lower()

    if "rag_patterns" in key_lower or filename == "pattern.json":
        return ArtifactCategory.RAG_PATTERN
    if filename == "evaluation_results.json":
        return ArtifactCategory.EVALUATION_RESULTS
    if filename == "metrics.json":
        return ArtifactCategory.MODEL_METRICS
    if filename.endswith(".ipynb") and "indexing" in key_lower:
        return ArtifactCategory.INDEXING_NOTEBOOK
    if filename.endswith(".ipynb") and "inference" in key_lower:
        return ArtifactCategory.INFERENCE_NOTEBOOK
    if "leaderboard" in key_lower or filename.endswith(".html"):
        return ArtifactCategory.LEADERBOARD
    if "v1_responses_body.json" in key_lower:
        return ArtifactCategory.RESPONSE_BODY

    return ArtifactCategory.OTHER


def list_and_categorize(
    s3_client: Any, bucket: str, prefix: str,
) -> list[CategorizedArtifact]:
    """List all S3 objects under *prefix* and categorize each."""
    result = paginate_objects(s3_client, bucket, prefix)
    artifacts: list[CategorizedArtifact] = []
    for obj in result.get("Contents", []):
        key = obj["Key"]
        if key.endswith("/"):
            continue
        modified = obj.get("LastModified", "")
        if hasattr(modified, "isoformat"):
            modified = modified.isoformat()
        artifacts.append(CategorizedArtifact(
            key=key,
            category=categorize(key),
            size_bytes=obj.get("Size", 0),
            last_modified=str(modified),
        ))
    return artifacts


def _extract_from_metrics_list(entries: list[dict]) -> dict[str, float] | None:
    """Average per-metric scores from a list of ``{name, score}`` dicts.

    Returns ``None`` if the list doesn't match the expected shape.
    """
    if not entries or not isinstance(entries[0], dict) or "name" not in entries[0]:
        return None
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for entry in entries:
        name = entry.get("name")
        score = entry.get("score")
        if name and isinstance(score, (int, float)):
            totals[name] = totals.get(name, 0.0) + score
            counts[name] = counts.get(name, 0) + 1
    if not totals:
        return None
    return {name: totals[name] / counts[name] for name in totals}


def extract_metrics(data: dict | list) -> dict[str, float]:
    """Extract numeric metrics from evaluation results.

    Handles (in order):
    1. List of per-question dicts with a ``metrics`` list of
       ``{name, score}`` entries — averages scores across questions.
    2. List of flat dicts — averages numeric fields across entries.
    3. Dict with ``evaluation.metrics`` list — extracts ``mean`` values.
    4. Dict with ``scores`` dict — extracts ``mean`` values.
    5. Dict with ``metrics`` sub-dict — extracts numeric values.
    6. Flat dict — extracts numeric values.
    """
    if isinstance(data, list):
        dicts = [e for e in data if isinstance(e, dict)]
        if not dicts:
            return {}

        has_metrics_list = any(
            isinstance(e.get("metrics"), list) for e in dicts
        )
        if has_metrics_list:
            all_metric_entries: list[dict] = []
            for entry in dicts:
                m = entry.get("metrics")
                if isinstance(m, list):
                    all_metric_entries.extend(m)
            result = _extract_from_metrics_list(all_metric_entries)
            if result:
                return result

        numeric_keys: set[str] = set()
        for entry in dicts:
            numeric_keys.update(
                k for k, v in entry.items() if isinstance(v, (int, float))
            )
        aggregated: dict[str, float] = {}
        for key in numeric_keys:
            values = [
                e[key] for e in dicts
                if key in e and isinstance(e[key], (int, float))
            ]
            if values:
                aggregated[key] = sum(values) / len(values)
        return aggregated

    evaluation = data.get("evaluation")
    if isinstance(evaluation, dict):
        metrics_list = evaluation.get("metrics")
        if isinstance(metrics_list, list):
            result: dict[str, float] = {}
            for entry in metrics_list:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name")
                scores = entry.get("scores")
                if name and isinstance(scores, dict):
                    mean = scores.get("mean")
                    if isinstance(mean, (int, float)):
                        result[name] = float(mean)
            if result:
                return result

    scores = data.get("scores")
    if isinstance(scores, dict):
        result = {}
        for name, score_data in scores.items():
            if isinstance(score_data, dict) and "mean" in score_data:
                mean = score_data["mean"]
                if isinstance(mean, (int, float)):
                    result[name] = float(mean)
        if result:
            return result

    source = data
    if "metrics" in data and isinstance(data["metrics"], dict):
        source = data["metrics"]
    return {k: v for k, v in source.items() if isinstance(v, (int, float))}
