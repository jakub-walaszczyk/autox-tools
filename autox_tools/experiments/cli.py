"""CLI entry point for experiment result management commands.

Usage::

    uv run experiments results <run-id> [--prefix PREFIX] [--bucket BUCKET]
    uv run experiments compare <run-id-1> <run-id-2> [--metrics M1,M2,...] [--prefix1 P1] [--prefix2 P2]
    uv run experiments export <run-id> [--output DIR] [--prefix PREFIX] [--bucket BUCKET]
    uv run experiments info <run-id>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from autox_tools.experiments._artifacts import (
    _CATEGORY_LABELS,
    ArtifactCategory,
    extract_metrics,
    list_and_categorize,
)
from autox_tools.experiments._display import (
    delta_indicator,
    format_duration,
    format_size,
    is_lower_better,
)
from autox_tools.experiments._resolver import resolve
from autox_tools.s3.cli import _paginate_objects

# ---------------------------------------------------------------------------
# Helpers
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


def _print_json(data: object) -> None:
    print(json.dumps(data, indent=2, default=str))


def _download_json(s3_client: Any, bucket: str, key: str) -> dict | list | None:
    """Download and parse a JSON file from S3.  Returns None on failure."""
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        return json.loads(response["Body"].read())
    except Exception:
        return None


def _find_results_recursive(
    s3_client: Any, bucket: str, prefix: str,
) -> tuple[dict | list | None, str | None]:
    """Scan the full prefix tree for result files.

    Falls back to a recursive S3 listing when the result files are nested
    deeper than the well-known subpaths (e.g. inside
    ``rag-templates-optimization/<id>/rag_patterns/``).  Prefers
    ``evaluation_results.json`` over ``metrics.json``.
    """
    result = _paginate_objects(s3_client, bucket, prefix)
    eval_key: str | None = None
    metrics_key: str | None = None
    for obj in result.get("Contents", []):
        key: str = obj["Key"]
        basename = key.rsplit("/", 1)[-1]
        if basename == "evaluation_results.json" and eval_key is None:
            eval_key = key
        elif basename == "metrics.json" and metrics_key is None:
            metrics_key = key
        if eval_key:
            break

    chosen = eval_key or metrics_key
    if chosen:
        data = _download_json(s3_client, bucket, chosen)
        if data is not None:
            return data, chosen
    return None, None


def _find_results(
    s3_client: Any, bucket: str, prefix: str,
) -> tuple[dict | list | None, str | None]:
    """Locate and download the evaluation results file.

    Tries ``evaluation_results.json`` first (AutoRAG), then
    ``metrics.json`` (AutoML), at well-known sub-paths under *prefix*.
    When none of the shallow paths match, falls back to a recursive
    listing of the entire prefix tree.
    Returns ``(parsed_json, s3_key)`` or ``(None, None)``.
    """
    prefix = prefix.rstrip("/") + "/" if prefix and not prefix.endswith("/") else prefix
    for subpath in _EVAL_RESULTS_SUBPATHS + _METRICS_SUBPATHS:
        key = f"{prefix}{subpath}"
        data = _download_json(s3_client, bucket, key)
        if data is not None:
            return data, key
    return _find_results_recursive(s3_client, bucket, prefix)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_results(kfp_client: Any, s3_client: Any, args: argparse.Namespace) -> None:
    """Display experiment results for a pipeline run."""
    location = resolve(
        kfp_client, s3_client, args.run_id,
        explicit_prefix=args.prefix, explicit_bucket=args.bucket,
    )
    if not location:
        sys.exit(f"Could not locate artifacts for run {args.run_id}.")

    data, key = _find_results(s3_client, location.bucket, location.prefix)
    if data is None:
        sys.exit(
            f"No evaluation_results.json or metrics.json found under "
            f"s3://{location.bucket}/{location.prefix}"
        )

    if args.json:
        _print_json(data)
        return

    metrics = extract_metrics(data)

    print(f"Experiment Results: run {args.run_id}")
    print(f"Source: s3://{location.bucket}/{key}\n")

    if metrics:
        max_name = max(len(k) for k in metrics)
        for name, value in sorted(metrics.items()):
            formatted = f"{value:.4f}" if isinstance(value, float) else str(value)
            print(f"  {name:<{max_name}}  {formatted}")

    if isinstance(data, list):
        print(f"\nPatterns evaluated: {len(data)}")
    else:
        patterns_evaluated = data.get("patterns_evaluated")
        best_pattern = data.get("best_pattern")
        if patterns_evaluated is not None:
            print(f"\nPatterns evaluated: {patterns_evaluated}")
        if best_pattern:
            bp_name = best_pattern.get("name", best_pattern) if isinstance(best_pattern, dict) else best_pattern
            print(f"Best pattern: {bp_name}")


def cmd_compare(kfp_client: Any, s3_client: Any, args: argparse.Namespace) -> None:
    """Side-by-side metric comparison of two pipeline runs."""
    from tabulate import tabulate

    loc1 = resolve(
        kfp_client, s3_client, args.run_id_1,
        explicit_prefix=args.prefix1, explicit_bucket=args.bucket,
    )
    loc2 = resolve(
        kfp_client, s3_client, args.run_id_2,
        explicit_prefix=args.prefix2, explicit_bucket=args.bucket,
    )

    if not loc1:
        sys.exit(f"Could not locate artifacts for run {args.run_id_1}.")
    if not loc2:
        sys.exit(f"Could not locate artifacts for run {args.run_id_2}.")

    data1, _ = _find_results(s3_client, loc1.bucket, loc1.prefix)
    data2, _ = _find_results(s3_client, loc2.bucket, loc2.prefix)

    if data1 is None:
        sys.exit(f"No results found for run {args.run_id_1}.")
    if data2 is None:
        sys.exit(f"No results found for run {args.run_id_2}.")

    metrics1 = extract_metrics(data1)
    metrics2 = extract_metrics(data2)

    all_metrics = sorted(set(metrics1) | set(metrics2))
    if args.metrics:
        requested = {m.strip() for m in args.metrics.split(",")}
        all_metrics = [m for m in all_metrics if m in requested]

    if args.json:
        deltas = {}
        for m in all_metrics:
            v1 = metrics1.get(m)
            v2 = metrics2.get(m)
            if v1 is not None and v2 is not None:
                deltas[m] = v2 - v1
        _print_json({
            "run_1": {"id": args.run_id_1, "metrics": {m: metrics1.get(m) for m in all_metrics}},
            "run_2": {"id": args.run_id_2, "metrics": {m: metrics2.get(m) for m in all_metrics}},
            "deltas": deltas,
        })
        return

    short1 = args.run_id_1[-8:]
    short2 = args.run_id_2[-8:]
    print(f"Comparison: run ...{short1} vs run ...{short2}\n")

    rows: list[list[str]] = []
    for m in all_metrics:
        v1 = metrics1.get(m)
        v2 = metrics2.get(m)
        v1_str = f"{v1:.4f}" if isinstance(v1, float) else (str(v1) if v1 is not None else "—")
        v2_str = f"{v2:.4f}" if isinstance(v2, float) else (str(v2) if v2 is not None else "—")

        if v1 is not None and v2 is not None:
            delta = v2 - v1
            delta_str = delta_indicator(delta, is_lower_better(m))
        else:
            delta_str = "—"

        rows.append([m, v1_str, v2_str, delta_str])

    print(tabulate(rows, headers=["Metric", "Run 1", "Run 2", "Delta"], tablefmt="simple"))

    primary = all_metrics[0] if all_metrics else None
    if primary and primary in metrics1 and primary in metrics2:
        lib = is_lower_better(primary)
        winner = "Run 1" if (metrics1[primary] < metrics2[primary]) == lib else "Run 2"
        print(f"\nWinner by {primary}: {winner}")


def cmd_export(kfp_client: Any, s3_client: Any, args: argparse.Namespace) -> None:
    """Download all experiment artifacts for a pipeline run."""
    location = resolve(
        kfp_client, s3_client, args.run_id,
        explicit_prefix=args.prefix, explicit_bucket=args.bucket,
    )
    if not location:
        sys.exit(f"Could not locate artifacts for run {args.run_id}.")

    artifacts = list_and_categorize(s3_client, location.bucket, location.prefix)
    if not artifacts:
        sys.exit(f"No artifacts found under s3://{location.bucket}/{location.prefix}")

    short_id = args.run_id[:8]
    output_dir = args.output or f"./experiment-{short_id}"

    print(f"Exporting artifacts for run {args.run_id}...\n")

    cat_counts: dict[ArtifactCategory, int] = {}
    cat_sizes: dict[ArtifactCategory, int] = {}
    for a in artifacts:
        cat_counts[a.category] = cat_counts.get(a.category, 0) + 1
        cat_sizes[a.category] = cat_sizes.get(a.category, 0) + a.size_bytes

    max_label = max(len(_CATEGORY_LABELS[c]) for c in cat_counts)
    for cat, count in sorted(cat_counts.items(), key=lambda x: x[0].value):
        label = _CATEGORY_LABELS[cat]
        size = format_size(cat_sizes[cat])
        print(f"  {label:<{max_label}}  {count:>5} file(s)  {size:>10}")

    total_size = sum(a.size_bytes for a in artifacts)
    print(f"\nTotal: {len(artifacts)} files, {format_size(total_size)} -> {output_dir}/\n")

    os.makedirs(output_dir, exist_ok=True)
    prefix_stripped = location.prefix
    downloaded: list[str] = []

    for a in artifacts:
        rel = a.key[len(prefix_stripped):].lstrip("/") if prefix_stripped else a.key
        local_path = os.path.join(output_dir, rel)
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        s3_client.download_file(location.bucket, a.key, local_path)
        downloaded.append(local_path)

    print("Downloaded:")
    for path in downloaded:
        print(f"  {path}")


def cmd_info(kfp_client: Any, s3_client: Any, args: argparse.Namespace) -> None:
    """Show run metadata and artifact summary."""
    try:
        run = kfp_client.get_run(args.run_id)
    except Exception as exc:
        sys.exit(f"Failed to get run: {exc}")

    run_obj = getattr(run, "run", run)

    state = getattr(run_obj, "state", None)
    if state is not None and hasattr(state, "value"):
        state = state.value
    state = str(state) if state else "Unknown"

    display_name = getattr(run_obj, "display_name", None) or getattr(run_obj, "name", "")
    created_at = getattr(run_obj, "created_at", None)
    finished_at = getattr(run_obj, "finished_at", None)

    duration = None
    if created_at and finished_at:
        duration = format_duration((finished_at - created_at).total_seconds())

    location = resolve(
        kfp_client, s3_client, args.run_id,
        explicit_prefix=getattr(args, "prefix", None),
        explicit_bucket=getattr(args, "bucket", None),
    )

    artifacts = []
    if location:
        artifacts = list_and_categorize(s3_client, location.bucket, location.prefix)

    if args.json:
        _print_json({
            "run_id": args.run_id,
            "name": display_name,
            "state": state,
            "created": str(created_at) if created_at else None,
            "finished": str(finished_at) if finished_at else None,
            "duration": duration,
            "artifacts_location": (
                f"s3://{location.bucket}/{location.prefix}" if location else None
            ),
            "artifact_count": len(artifacts),
            "artifact_size_bytes": sum(a.size_bytes for a in artifacts),
            "artifacts": [
                {"key": a.key, "category": a.category.value, "size_bytes": a.size_bytes}
                for a in artifacts
            ],
        })
        return

    print(f"Run: {args.run_id}")
    if display_name:
        print(f"Name: {display_name}")
    print(f"State: {state}")
    if duration:
        print(f"Duration: {duration}")
    if created_at:
        print(f"Created: {created_at}")

    if location:
        print(f"\nArtifacts: s3://{location.bucket}/{location.prefix}")
        if artifacts:
            max_key = max(
                len(a.key[len(location.prefix):].lstrip("/"))
                for a in artifacts
            ) if artifacts else 10
            for a in artifacts:
                rel = a.key[len(location.prefix):].lstrip("/")
                print(f"  {rel:<{max_key}}  {format_size(a.size_bytes):>10}")
            total = sum(a.size_bytes for a in artifacts)
            print(f"  Total: {len(artifacts)} files, {format_size(total)}")
        else:
            print("  (no artifacts found)")
    else:
        print("\nArtifacts: could not resolve location")


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="experiments",
        description="Manage and compare experiment results from AutoRAG and AutoML pipeline runs.",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    sub = parser.add_subparsers(dest="command", required=True)

    # results
    p = sub.add_parser("results", help="Display experiment results for a run")
    p.add_argument("run_id", help="KFP run ID (UUID)")
    p.add_argument("--prefix", help="Explicit S3 prefix override (skip auto-resolution)")
    p.add_argument("--bucket", help="Explicit bucket override")

    # compare
    p = sub.add_parser("compare", help="Side-by-side metric comparison of two runs")
    p.add_argument("run_id_1", help="First KFP run ID")
    p.add_argument("run_id_2", help="Second KFP run ID")
    p.add_argument("--metrics", help="Comma-separated metric names to compare (default: all)")
    p.add_argument("--prefix1", help="Explicit S3 prefix for run 1")
    p.add_argument("--prefix2", help="Explicit S3 prefix for run 2")
    p.add_argument("--bucket", help="Explicit bucket override (shared for both runs)")

    # export
    p = sub.add_parser("export", help="Download all experiment artifacts")
    p.add_argument("run_id", help="KFP run ID (UUID)")
    p.add_argument("--output", "-o", help="Local destination directory (default: ./experiment-<short-id>/)")
    p.add_argument("--prefix", help="Explicit S3 prefix override")
    p.add_argument("--bucket", help="Explicit bucket override")

    # info
    p = sub.add_parser("info", help="Show run metadata and artifact summary")
    p.add_argument("run_id", help="KFP run ID (UUID)")
    p.add_argument("--prefix", help="Explicit S3 prefix override")
    p.add_argument("--bucket", help="Explicit bucket override")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    from autox_tools.pipelines._artifacts_s3 import connect as s3_connect
    from autox_tools.pipelines._kfp import connect as kfp_connect

    kfp_client = kfp_connect()
    s3_client = s3_connect()

    commands: dict[str, Any] = {
        "results": cmd_results,
        "compare": cmd_compare,
        "export": cmd_export,
        "info": cmd_info,
    }
    commands[args.command](kfp_client, s3_client, args)


if __name__ == "__main__":
    main()
