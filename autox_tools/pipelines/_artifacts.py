"""S3 artifact listing, categorization, and download for pipeline runs.

Resolves the S3 location from KFP run metadata, discovers pipeline
component folders, categorises objects (evaluation results, notebooks,
leaderboard, RAG patterns, etc.), and provides both summary and
per-component views.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Any

from autox_tools._output import human_size, print_json
from autox_tools._s3_utils import download_objects, paginate_objects
from autox_tools.pipelines._kfp import get_pipeline_name

if TYPE_CHECKING:
    import argparse


_CATEGORY_LABELS = {
    "evaluation": "Evaluation Results",
    "indexing_notebooks": "Indexing Notebooks",
    "inference_notebooks": "Inference Notebooks",
    "leaderboard": "Leaderboard",
    "rag_patterns": "RAG Patterns",
    "other": "Other",
}


def _categorize_object(key: str) -> str:
    """Assign an S3 object key to an artifact category.

    ``rag_patterns/`` is checked first — files nested inside pattern folders
    (notebooks, evaluation JSONs, etc.) belong to the patterns category regardless
    of their extension.
    """
    if "rag_patterns" in key.lower() or "pattern.json" in key:
        return "rag_patterns"
    if "evaluation_results.json" in key:
        return "evaluation"
    if key.endswith(".ipynb") and "indexing" in key.lower():
        return "indexing_notebooks"
    if key.endswith(".ipynb") and "inference" in key.lower():
        return "inference_notebooks"
    if key.endswith(".html") or "leaderboard" in key.lower():
        return "leaderboard"
    return "other"


def _match_name(query: str, names: list[str]) -> list[str]:
    """Case-insensitive substring match; exact match preferred."""
    q = query.lower()
    exact = [n for n in names if n.lower() == q]
    if exact:
        return exact
    return [n for n in names if q in n.lower()]


def _discover_components(
    s3_client: Any,
    bucket: str,
    key_prefix: str,
) -> list[str]:
    """Enumerate pipeline component folder names under the run prefix."""
    result = paginate_objects(s3_client, bucket, key_prefix, delimiter="/")
    components: list[str] = []
    for cp in result.get("CommonPrefixes", []):
        name = cp["Prefix"][len(key_prefix):].rstrip("/")
        if name:
            components.append(name)
    return sorted(components)


def _resolve_artifact_s3(
    run_obj: Any,
    pipeline_name: str,
    *,
    artifacts_s3_cfg: Any = None,
) -> tuple[Any, str, str, str]:
    """Resolve S3 client, bucket, prefix, and artifact root from KFP run config.

    Returns ``(s3_client, bucket, key_prefix, artifact_root)``.
    Exits on missing credentials or bucket.
    """
    runtime_config = getattr(run_obj, "runtime_config", None)
    artifact_root = None
    if runtime_config:
        artifact_root = getattr(runtime_config, "pipeline_root", None)
        if not artifact_root:
            params = getattr(runtime_config, "parameters", {}) or {}
            if isinstance(params, dict):
                artifact_root = params.get("output", None)

    if not artifact_root:
        artifact_root = f"{pipeline_name}/"

    from autox_tools.pipelines._artifacts_s3 import connect as artifacts_s3_connect

    if artifacts_s3_cfg is not None:
        s3_client = artifacts_s3_connect(artifacts_s3_cfg)
    else:
        s3_endpoint = os.getenv("ARTIFACTS_AWS_S3_ENDPOINT")
        if not s3_endpoint:
            sys.exit(
                "Artifacts S3 credentials not configured -- set ARTIFACTS_AWS_S3_ENDPOINT, "
                "ARTIFACTS_AWS_ACCESS_KEY_ID, and ARTIFACTS_AWS_SECRET_ACCESS_KEY."
            )
        s3_client = artifacts_s3_connect()

    config_bucket = getattr(artifacts_s3_cfg, "bucket", "") if artifacts_s3_cfg else ""

    if artifact_root.startswith("s3://"):
        cleaned = artifact_root[5:]
        parts = cleaned.split("/", 1)
        bucket = parts[0]
        key_prefix = parts[1] if len(parts) == 2 else ""
    else:
        bucket = os.getenv("ARTIFACTS_S3_BUCKET", "") or config_bucket
        if not bucket:
            sys.exit(
                "ARTIFACTS_S3_BUCKET is required when the KFP run config does not "
                "include a full s3:// artifact root.\n"
                f"  artifact_root: {artifact_root}"
            )
        key_prefix = artifact_root

    return s3_client, bucket, key_prefix, artifact_root


def _refine_prefix_for_run(
    s3_client: Any,
    bucket: str,
    key_prefix: str,
    run_id: str,
) -> str:
    """Narrow ``key_prefix`` to include the run ID when it is not already present.

    KFP's ``pipeline_root`` often points at the pipeline level
    (``pipeline-name/``) rather than the run level
    (``pipeline-name/run-id/``).  When that happens, S3 listings return
    run-ID folders instead of component folders.  This helper checks for
    the more specific path and returns it when objects exist there.
    """
    if run_id in key_prefix:
        return key_prefix

    candidate = f"{key_prefix}{run_id}/"
    probe = paginate_objects(s3_client, bucket, candidate, max_keys=1)
    if probe.get("Contents"):
        return candidate
    return key_prefix


# ---------------------------------------------------------------------------
# Component view
# ---------------------------------------------------------------------------


def _cmd_artifacts_component(
    s3_client: Any,
    bucket: str,
    key_prefix: str,
    args: argparse.Namespace,
) -> None:
    """Handle ``--component`` mode: list or download a single component's artifacts."""
    components = _discover_components(s3_client, bucket, key_prefix)
    if not components:
        print(f"No component folders found under {key_prefix}")
        sys.exit(1)

    if args.component == "all":
        summaries: list[dict[str, Any]] = []
        for cname in components:
            prefix = f"{key_prefix}{cname}/"
            result = paginate_objects(s3_client, bucket, prefix)
            objs = result.get("Contents", [])
            total_size = sum(o.get("Size", 0) for o in objs)
            summaries.append({
                "name": cname,
                "file_count": len(objs),
                "total_size": total_size,
            })

        if args.json:
            print_json({"components": summaries, "total_components": len(summaries)})
            return

        max_name = max(len(s["name"]) for s in summaries) if summaries else 10
        print(f"Components ({len(summaries)}):\n")
        for s in summaries:
            print(
                f"  {s['name']:<{max_name}}  "
                f"{s['file_count']:>5} file(s)  "
                f"{human_size(s['total_size']):>10}"
            )
        total_files = sum(s["file_count"] for s in summaries)
        total_bytes = sum(s["total_size"] for s in summaries)
        print(f"\n  Total: {len(summaries)} component(s), {total_files} file(s), {human_size(total_bytes)}")
        return

    matched = _match_name(args.component, components)
    if not matched:
        print(f"Component '{args.component}' not found. Available components:")
        for c in components:
            print(f"  {c}")
        sys.exit(1)

    if len(matched) > 1:
        print(f"'{args.component}' matches multiple components:")
        for c in matched:
            print(f"  {c}")
        print("\nSpecify a more precise name.")
        sys.exit(1)

    comp_name = matched[0]
    comp_prefix = f"{key_prefix}{comp_name}/"
    result = paginate_objects(s3_client, bucket, comp_prefix)
    objects = result.get("Contents", [])

    if args.json:
        print_json({
            "component": comp_name,
            "prefix": comp_prefix,
            "total": len(objects),
            "artifacts": [
                {"key": o["Key"], "size_bytes": o.get("Size", 0)} for o in objects
            ],
        })
        return

    print(f"Component: {comp_name}")
    print(f"Prefix   : {comp_prefix}\n")
    for obj in objects:
        rel = obj["Key"][len(comp_prefix):]
        if not rel:
            continue
        print(f"  {rel:<60} {human_size(obj.get('Size', 0)):>10}")
    print(f"\n  {len(objects)} artifact(s)")

    if args.download:
        print()
        download_objects(s3_client, bucket, objects, comp_prefix, args.download)


# ---------------------------------------------------------------------------
# Summary view
# ---------------------------------------------------------------------------


def _cmd_artifacts_summary(
    s3_client: Any,
    bucket: str,
    key_prefix: str,
    args: argparse.Namespace,
) -> None:
    """Default mode: show a summary of artifacts with category counts."""
    result = paginate_objects(s3_client, bucket, key_prefix)
    objects = result.get("Contents", [])

    categories: dict[str, int] = {k: 0 for k in _CATEGORY_LABELS}
    cat_sizes: dict[str, int] = {k: 0 for k in _CATEGORY_LABELS}

    for obj in objects:
        cat = _categorize_object(obj["Key"])
        categories[cat] += 1
        cat_sizes[cat] += obj.get("Size", 0)

    total_size = sum(cat_sizes.values())

    if args.json:
        print_json({
            "run_id": args.run_id,
            "bucket": bucket,
            "prefix": key_prefix,
            "total_artifacts": len(objects),
            "total_size": total_size,
            "categories": {
                k: {"count": categories[k], "size": cat_sizes[k]}
                for k in _CATEGORY_LABELS
            },
        })
        return

    print(f"Artifacts for run {args.run_id}")
    print(f"Bucket: {bucket}  Prefix: {key_prefix}\n")

    for cat_key, label in _CATEGORY_LABELS.items():
        count = categories[cat_key]
        if not count:
            continue
        print(f"  {label:<25} {count:>6} file(s)  {human_size(cat_sizes[cat_key]):>10}")

    print(f"\n  Total: {len(objects)} artifact(s), {human_size(total_size)}")

    if args.download:
        if len(objects) > 1000:
            print(f"\n  Downloading {len(objects)} objects...")
        print()
        download_objects(s3_client, bucket, objects, key_prefix, args.download)


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


def cmd_artifacts(kfp_client: Any, args: argparse.Namespace, **kwargs: Any) -> None:
    """List or download S3 artifacts from a pipeline run."""
    artifacts_s3_cfg = kwargs.get("artifacts_s3_cfg")
    run = kfp_client.get_run(args.run_id)
    run_obj = getattr(run, "run", run)
    pipeline_name = get_pipeline_name(run_obj) or "unknown"

    has_s3 = bool(os.getenv("ARTIFACTS_AWS_S3_ENDPOINT")) or artifacts_s3_cfg is not None
    if not has_s3 and not args.component:
        runtime_config = getattr(run_obj, "runtime_config", None)
        artifact_root = None
        if runtime_config:
            artifact_root = getattr(runtime_config, "pipeline_root", None)
        if args.json:
            print_json({
                "run_id": args.run_id,
                "artifact_root": artifact_root,
                "note": "Artifacts S3 credentials not configured.",
            })
        else:
            print(f"Artifact root: {artifact_root or 'unknown'}")
            print(
                "Artifacts S3 credentials not configured -- set ARTIFACTS_AWS_S3_ENDPOINT, "
                "ARTIFACTS_AWS_ACCESS_KEY_ID, and ARTIFACTS_AWS_SECRET_ACCESS_KEY."
            )
        return

    s3_client, bucket, key_prefix, _artifact_root = _resolve_artifact_s3(
        run_obj, pipeline_name, artifacts_s3_cfg=artifacts_s3_cfg,
    )
    key_prefix = _refine_prefix_for_run(s3_client, bucket, key_prefix, args.run_id)

    if args.component:
        _cmd_artifacts_component(s3_client, bucket, key_prefix, args)
        return

    _cmd_artifacts_summary(s3_client, bucket, key_prefix, args)
