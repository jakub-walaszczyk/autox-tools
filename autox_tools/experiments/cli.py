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
    list_and_categorize,
)
from autox_tools.experiments._display import (
    delta_indicator,
    filter_metric_dict,
    filter_metric_names,
    format_best_patterns,
    format_compare_header,
    format_duration,
    format_leaderboard,
    format_pattern_detail,
    format_pattern_settings,
    format_pipeline_params,
    format_run_header,
    format_size,
    format_summary_metrics,
    is_lower_better,
    short_id,
)
from autox_tools.experiments._patterns import (
    RunPatternData,
    collect_run_data,
    parse_names,
)
from autox_tools.experiments._resolver import resolve

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _print_json(data: object) -> None:
    print(json.dumps(data, indent=2, default=str))


def _get_display_name(
    run_id: str, names: dict[str, str],
) -> str:
    """Resolve a human-readable display name for a run."""
    return names.get(run_id, run_id)


def _find_best_pattern(
    data: RunPatternData,
    sort_metric: str | None,
) -> Any | None:
    """Return the top-ranked pattern for the given metric."""
    if not data.patterns or not sort_metric:
        return None
    reverse = not is_lower_better(sort_metric)
    ranked = sorted(
        data.patterns,
        key=lambda p: p.metrics.get(
            sort_metric,
            float("-inf") if reverse else float("inf"),
        ),
        reverse=reverse,
    )
    return ranked[0]


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_results(kfp_client: Any, s3_client: Any, args: argparse.Namespace) -> None:
    """Display experiment results for a pipeline run."""
    names = parse_names(getattr(args, "names", None))

    data = collect_run_data(
        kfp_client, s3_client, args.run_id,
        explicit_prefix=args.prefix,
        explicit_bucket=args.bucket,
        names=names,
    )
    if data is None:
        sys.exit(f"Could not locate artifacts for run {args.run_id}.")

    if not data.summary_metrics and not data.patterns:
        sys.exit(
            f"No evaluation_results.json or metrics.json found for run {args.run_id}."
        )

    if args.json:
        _print_json(_run_data_to_dict(data))
        return

    pdf_path = getattr(args, "pdf", None)
    if pdf_path:
        from autox_tools.experiments._report import generate_results_pdf, require_matplotlib
        require_matplotlib()
        generate_results_pdf(data, pdf_path, names)
        print(f"PDF report saved to {pdf_path}")
        return

    # Console output
    print(format_run_header(
        data.run_id, data.display_name, data.source_key,
        pipeline_name=data.pipeline_name,
        state=data.state,
        duration_seconds=data.duration_seconds,
        created_at=data.created_at,
    ))

    if data.pipeline_params:
        print(format_pipeline_params(data.pipeline_params))

    filtered_summary = filter_metric_dict(data.summary_metrics)
    print(format_summary_metrics(filtered_summary, data.primary_metric))

    if data.patterns:
        sort_by = getattr(args, "sort_by", None) or data.primary_metric
        all_metrics = filter_metric_names(_collect_all_metric_names(data))
        print(format_leaderboard(data.patterns, sort_by, all_metrics))

        if getattr(args, "detailed", False):
            top_n = getattr(args, "top_n", 1) or 1
            print(format_best_patterns(data.patterns, sort_by, n=top_n))

            print(f"\n  {'=' * 60}")
            print("  Per-Pattern Detail")
            print(f"  {'=' * 60}")
            for pattern in data.patterns:
                print(format_pattern_detail(pattern))

    print()


def _try_collect(
    kfp_client: Any,
    s3_client: Any,
    run_id: str,
    *,
    explicit_prefix: str | None = None,
    explicit_bucket: str | None = None,
    names: dict[str, str] | None = None,
) -> RunPatternData | None:
    """Wrapper around ``collect_run_data`` that catches unexpected errors."""
    try:
        return collect_run_data(
            kfp_client, s3_client, run_id,
            explicit_prefix=explicit_prefix,
            explicit_bucket=explicit_bucket,
            names=names,
        )
    except Exception as exc:
        print(f"Warning: failed to fetch data for run {run_id}: {exc}", file=sys.stderr)
        return None


def cmd_compare(kfp_client: Any, s3_client: Any, args: argparse.Namespace) -> None:
    """Side-by-side metric comparison of two pipeline runs."""
    from tabulate import tabulate

    names = parse_names(getattr(args, "names", None))

    data1 = _try_collect(
        kfp_client, s3_client, args.run_id_1,
        explicit_prefix=args.prefix1,
        explicit_bucket=args.bucket,
        names=names,
    )
    data2 = _try_collect(
        kfp_client, s3_client, args.run_id_2,
        explicit_prefix=args.prefix2,
        explicit_bucket=args.bucket,
        names=names,
    )

    errors: list[str] = []
    if data1 is None:
        errors.append(f"Could not locate artifacts for run {args.run_id_1}.")
    elif not data1.summary_metrics and not data1.patterns:
        errors.append(f"No results found for run {args.run_id_1}.")
    if data2 is None:
        errors.append(f"Could not locate artifacts for run {args.run_id_2}.")
    elif not data2.summary_metrics and not data2.patterns:
        errors.append(f"No results found for run {args.run_id_2}.")
    if errors:
        sys.exit("\n".join(errors))

    if args.json:
        _print_json(_compare_data_to_dict(data1, data2, args.metrics))
        return

    pdf_path = getattr(args, "pdf", None)
    if pdf_path:
        from autox_tools.experiments._report import generate_compare_pdf, require_matplotlib
        require_matplotlib()
        generate_compare_pdf(data1, data2, pdf_path, names)
        print(f"PDF report saved to {pdf_path}")
        return

    # Console output
    print(format_compare_header(
        data1.run_id, data2.run_id, data1.display_name, data2.display_name,
        pipeline1=data1.pipeline_name, pipeline2=data2.pipeline_name,
        duration1=data1.duration_seconds, duration2=data2.duration_seconds,
        state1=data1.state, state2=data2.state,
    ))

    # Filter and sort metrics for display
    filtered1 = filter_metric_dict(data1.summary_metrics)
    filtered2 = filter_metric_dict(data2.summary_metrics)
    all_metrics = sorted(set(filtered1) | set(filtered2))
    if args.metrics:
        requested = {m.strip() for m in args.metrics.split(",")}
        all_metrics = [m for m in all_metrics if m in requested]

    primary = data1.primary_metric or data2.primary_metric
    if primary and primary in all_metrics:
        all_metrics.remove(primary)
        all_metrics.insert(0, primary)

    label1 = (
        data1.display_name
        if data1.display_name != data1.run_id
        else short_id(data1.run_id)
    )
    label2 = (
        data2.display_name
        if data2.display_name != data2.run_id
        else short_id(data2.run_id)
    )

    print("\n  Summary Metrics")
    print(f"  {'-' * 60}")

    rows: list[list[str]] = []
    for m in all_metrics:
        v1 = filtered1.get(m)
        v2 = filtered2.get(m)
        v1_str = f"{v1:.4f}" if isinstance(v1, float) else (str(v1) if v1 is not None else "—")
        v2_str = f"{v2:.4f}" if isinstance(v2, float) else (str(v2) if v2 is not None else "—")

        if v1 is not None and v2 is not None:
            delta = v2 - v1
            delta_str = delta_indicator(delta, is_lower_better(m))
        else:
            delta_str = "—"

        rows.append([m, v1_str, v2_str, delta_str])

    table = tabulate(rows, headers=["Metric", label1, label2, "Delta"], tablefmt="simple")
    for line in table.splitlines():
        print(f"  {line}")

    if primary and primary in filtered1 and primary in filtered2:
        lib = is_lower_better(primary)
        winner = label1 if (filtered1[primary] < filtered2[primary]) == lib else label2
        print(f"\n  Winner by {primary}: {winner}")

    # Per-pattern comparison (detailed mode only)
    if getattr(args, "detailed", False) and data1.patterns and data2.patterns:
        pm = data1.primary_metric or data2.primary_metric
        all_m1 = filter_metric_names(_collect_all_metric_names(data1))
        all_m2 = filter_metric_names(_collect_all_metric_names(data2))

        print(f"\n  {'=' * 60}")
        print("  Per-Pattern Leaderboard Comparison")
        print(f"  {'=' * 60}")

        print(f"\n  --- {label1} ---")
        print(format_leaderboard(data1.patterns, pm, all_m1))
        print(f"\n  --- {label2} ---")
        print(format_leaderboard(data2.patterns, pm, all_m2))

        best1 = _find_best_pattern(data1, pm)
        best2 = _find_best_pattern(data2, pm)
        if best1 or best2:
            print("\n  Best Pattern Settings")
            print(f"  {'-' * 60}")
            if best1:
                print(f"\n  {label1} ({best1.name}):")
                print(format_pattern_settings(best1))
            if best2:
                print(f"\n  {label2} ({best2.name}):")
                print(format_pattern_settings(best2))

    print()


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

    short_id_str = args.run_id[:8]
    output_dir = args.output or f"./experiment-{short_id_str}"

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
# JSON serialization helpers
# ---------------------------------------------------------------------------


def _run_data_to_dict(data: RunPatternData) -> dict:
    """Serialize RunPatternData for JSON output."""
    result: dict[str, Any] = {
        "run_id": data.run_id,
        "display_name": data.display_name,
        "summary_metrics": data.summary_metrics,
        "primary_metric": data.primary_metric,
    }
    if data.patterns:
        result["patterns"] = [
            {"name": p.name, "metrics": p.metrics}
            for p in data.patterns
        ]
    return result


def _compare_data_to_dict(
    data1: RunPatternData,
    data2: RunPatternData,
    metrics_filter: str | None = None,
) -> dict:
    """Serialize comparison data for JSON output."""
    all_metrics = sorted(set(data1.summary_metrics) | set(data2.summary_metrics))
    if metrics_filter:
        requested = {m.strip() for m in metrics_filter.split(",")}
        all_metrics = [m for m in all_metrics if m in requested]

    deltas = {}
    for m in all_metrics:
        v1 = data1.summary_metrics.get(m)
        v2 = data2.summary_metrics.get(m)
        if v1 is not None and v2 is not None:
            deltas[m] = v2 - v1

    result: dict[str, Any] = {
        "run_1": {
            "id": data1.run_id,
            "display_name": data1.display_name,
            "metrics": {m: data1.summary_metrics.get(m) for m in all_metrics},
        },
        "run_2": {
            "id": data2.run_id,
            "display_name": data2.display_name,
            "metrics": {m: data2.summary_metrics.get(m) for m in all_metrics},
        },
        "deltas": deltas,
    }

    if data1.patterns:
        result["run_1"]["patterns"] = [
            {"name": p.name, "metrics": p.metrics} for p in data1.patterns
        ]
    if data2.patterns:
        result["run_2"]["patterns"] = [
            {"name": p.name, "metrics": p.metrics} for p in data2.patterns
        ]

    return result


def _collect_all_metric_names(data: RunPatternData) -> list[str]:
    """Gather all metric names from patterns, sorted."""
    names: set[str] = set()
    for p in data.patterns:
        names.update(p.metrics)
    return sorted(names)


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
    p.add_argument("--pdf", metavar="PATH", help="Generate a PDF report at PATH")
    p.add_argument("--names", help='Display name mapping (e.g. "run-id=My Experiment")')
    p.add_argument("--sort-by", dest="sort_by", metavar="METRIC", help="Metric to rank the leaderboard by")
    p.add_argument("--detailed", "-d", action="store_true", help="Show pattern settings and per-pattern detail")
    p.add_argument(
        "--top-n", dest="top_n", type=int, default=1, metavar="N",
        help="Number of top patterns to show in detailed mode (default: 1)",
    )

    # compare
    p = sub.add_parser("compare", help="Side-by-side metric comparison of two runs")
    p.add_argument("run_id_1", help="First KFP run ID")
    p.add_argument("run_id_2", help="Second KFP run ID")
    p.add_argument("--metrics", help="Comma-separated metric names to compare (default: all)")
    p.add_argument("--prefix1", help="Explicit S3 prefix for run 1")
    p.add_argument("--prefix2", help="Explicit S3 prefix for run 2")
    p.add_argument("--bucket", help="Explicit bucket override (shared for both runs)")
    p.add_argument("--pdf", metavar="PATH", help="Generate a PDF report at PATH")
    p.add_argument("--names", help='Display name mapping (e.g. "id1=Baseline,id2=New Config")')
    p.add_argument("--detailed", "-d", action="store_true", help="Show per-run leaderboards and pattern settings")

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
