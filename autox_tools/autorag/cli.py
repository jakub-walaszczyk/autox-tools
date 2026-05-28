"""CLI entry point for AutoRAG experiment management.

Usage::

    uv run autorag results <run-id> [--prefix PREFIX] [--bucket BUCKET]
    uv run autorag compare <run-id-1> <run-id-2> [--metrics M1,M2,...] [--prefix1 P1] [--prefix2 P2]
    uv run autorag export <run-id> [--output DIR] [--prefix PREFIX] [--bucket BUCKET]
    uv run autorag info <run-id>
    uv run autorag artifacts <run-id> [--pattern NAME|all] [--download DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from autox_tools.autorag._artifacts import (
    _CATEGORY_LABELS,
    ArtifactCategory,
    list_and_categorize,
)
from autox_tools.autorag._display import (
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
from autox_tools.autorag._patterns import (
    RunPatternData,
    collect_run_data,
    discover_patterns,
    find_rag_patterns_prefix,
    parse_names,
)
from autox_tools.autorag._resolver import resolve

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
# Commands - results / compare / export / info
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
        from autox_tools.autorag._report import generate_results_pdf, require_matplotlib
        require_matplotlib()
        generate_results_pdf(data, pdf_path, names)
        print(f"PDF report saved to {pdf_path}")
        return

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
        from autox_tools.autorag._report import generate_compare_pdf, require_matplotlib
        require_matplotlib()
        generate_compare_pdf(data1, data2, pdf_path, names)
        print(f"PDF report saved to {pdf_path}")
        return

    print(format_compare_header(
        data1.run_id, data2.run_id, data1.display_name, data2.display_name,
        pipeline1=data1.pipeline_name, pipeline2=data2.pipeline_name,
        duration1=data1.duration_seconds, duration2=data2.duration_seconds,
        state1=data1.state, state2=data2.state,
    ))

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
# Commands - artifacts
# ---------------------------------------------------------------------------


def _match_pattern_name(query: str, patterns: list[str]) -> list[str]:
    """Case-insensitive substring match for pattern names; exact match preferred."""
    q = query.lower()
    exact = [p for p in patterns if p.lower() == q]
    if exact:
        return exact
    return [p for p in patterns if q in p.lower()]


def _download_objects(
    s3_client: Any,
    bucket: str,
    objects: list[dict[str, Any]],
    base_prefix: str,
    download_dir: str,
) -> None:
    """Download S3 objects to a local directory, preserving relative paths."""
    from autox_tools.s3.cli import _human_size

    os.makedirs(download_dir, exist_ok=True)
    downloaded = 0
    total_bytes = 0

    for obj in objects:
        key = obj["Key"]
        if key.endswith("/"):
            continue
        rel = key[len(base_prefix):].lstrip("/") if base_prefix else key
        local_path = os.path.join(download_dir, rel)
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        s3_client.download_file(bucket, key, local_path)
        downloaded += 1
        total_bytes += obj.get("Size", 0)
        print(f"  Downloaded: {rel} ({_human_size(obj.get('Size', 0))})")

    print(f"\n  {downloaded} file(s), {_human_size(total_bytes)} total to {download_dir}/")


def _cmd_artifacts_pattern(
    s3_client: Any,
    bucket: str,
    key_prefix: str,
    args: argparse.Namespace,
) -> None:
    """Handle ``--pattern`` mode: list or download RAG pattern artifacts."""
    from autox_tools.s3.cli import _human_size, _paginate_objects

    rag_prefix = find_rag_patterns_prefix(s3_client, bucket, key_prefix)
    if not rag_prefix:
        print("No rag_patterns/ folder found under this run's artifacts.")
        sys.exit(1)

    patterns = discover_patterns(s3_client, bucket, rag_prefix)
    if not patterns:
        print(f"No patterns found under {rag_prefix}")
        sys.exit(1)

    if args.pattern == "all":
        _cmd_artifacts_pattern_all(
            s3_client, bucket, rag_prefix, patterns, args,
        )
        return

    matched = _match_pattern_name(args.pattern, patterns)
    if not matched:
        print(f"Pattern '{args.pattern}' not found. Available patterns:")
        for p in patterns:
            print(f"  {p}")
        sys.exit(1)

    if len(matched) > 1 and not args.artifact:
        print(f"'{args.pattern}' matches multiple patterns:")
        for p in matched:
            print(f"  {p}")
        print("\nSpecify a more precise name.")
        sys.exit(1)

    pattern_name = matched[0]
    pattern_prefix = f"{rag_prefix}{pattern_name}/"
    result = _paginate_objects(s3_client, bucket, pattern_prefix)
    objects = result.get("Contents", [])

    if args.artifact:
        _cmd_artifacts_single_file(
            s3_client, bucket, pattern_prefix, objects,
            pattern_name, args,
        )
        return

    if args.json:
        _print_json({
            "pattern": pattern_name,
            "prefix": pattern_prefix,
            "total": len(objects),
            "artifacts": [
                {"key": o["Key"], "size_bytes": o.get("Size", 0)} for o in objects
            ],
        })
        return

    print(f"Pattern: {pattern_name}")
    print(f"Prefix : {pattern_prefix}\n")
    for obj in objects:
        rel = obj["Key"][len(pattern_prefix):]
        if not rel:
            continue
        print(f"  {rel:<60} {_human_size(obj.get('Size', 0)):>10}")
    print(f"\n  {len(objects)} artifact(s)")

    if args.download:
        print()
        _download_objects(s3_client, bucket, objects, pattern_prefix, args.download)


def _cmd_artifacts_pattern_all(
    s3_client: Any,
    bucket: str,
    rag_prefix: str,
    patterns: list[str],
    args: argparse.Namespace,
) -> None:
    """List all RAG patterns with file counts and sizes."""
    from autox_tools.s3.cli import _human_size, _paginate_objects

    summaries: list[dict[str, Any]] = []
    all_objects: list[dict[str, Any]] = []

    for pname in patterns:
        prefix = f"{rag_prefix}{pname}/"
        result = _paginate_objects(s3_client, bucket, prefix)
        objs = result.get("Contents", [])
        total_size = sum(o.get("Size", 0) for o in objs)
        summaries.append({
            "name": pname,
            "file_count": len(objs),
            "total_size": total_size,
        })
        all_objects.extend(objs)

    if args.json:
        _print_json({"patterns": summaries, "total_patterns": len(summaries)})
        return

    max_name = max(len(s["name"]) for s in summaries) if summaries else 10
    print(f"RAG Patterns ({len(summaries)}):\n")
    for s in summaries:
        print(
            f"  {s['name']:<{max_name}}  "
            f"{s['file_count']:>5} file(s)  "
            f"{_human_size(s['total_size']):>10}"
        )

    total_files = sum(s["file_count"] for s in summaries)
    total_bytes = sum(s["total_size"] for s in summaries)
    print(f"\n  Total: {len(summaries)} pattern(s), {total_files} file(s), {_human_size(total_bytes)}")

    if args.download:
        print()
        _download_objects(s3_client, bucket, all_objects, rag_prefix, args.download)


def _cmd_artifacts_single_file(
    s3_client: Any,
    bucket: str,
    pattern_prefix: str,
    objects: list[dict[str, Any]],
    pattern_name: str,
    args: argparse.Namespace,
) -> None:
    """Handle ``--artifact`` mode: show, download, or print a single file."""
    from autox_tools.s3.cli import _human_size

    query = args.artifact.lower()
    matches = [
        o for o in objects
        if query in os.path.basename(o["Key"]).lower()
    ]
    if not matches:
        print(f"Artifact '{args.artifact}' not found in pattern '{pattern_name}'.")
        print("Available artifacts:")
        for o in objects:
            rel = o["Key"][len(pattern_prefix):]
            if rel:
                print(f"  {rel}")
        sys.exit(1)

    if len(matches) > 1:
        exact = [o for o in matches if os.path.basename(o["Key"]).lower() == query]
        if len(exact) == 1:
            matches = exact
        else:
            print(f"'{args.artifact}' matches multiple artifacts:")
            for o in matches:
                print(f"  {os.path.basename(o['Key'])}")
            print("\nSpecify a more precise name.")
            sys.exit(1)

    obj = matches[0]
    key = obj["Key"]
    size = obj.get("Size", 0)
    filename = os.path.basename(key)

    if args.print_content:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        sys.stdout.buffer.write(response["Body"].read())
        return

    if args.json:
        _print_json({
            "pattern": pattern_name,
            "artifact": filename,
            "key": key,
            "size_bytes": size,
        })
        return

    print(f"Pattern  : {pattern_name}")
    print(f"Artifact : {filename} ({_human_size(size)})")
    print(f"S3 key   : {key}")

    if args.download:
        local_path = os.path.join(args.download, filename)
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        s3_client.download_file(bucket, key, local_path)
        print(f"\n  Downloaded to {local_path}")


def _cmd_artifacts_summary(
    s3_client: Any,
    bucket: str,
    key_prefix: str,
    args: argparse.Namespace,
) -> None:
    """Default mode: show a summary of artifacts with category counts."""
    from autox_tools.s3.cli import _human_size

    artifacts = list_and_categorize(s3_client, bucket, key_prefix)

    cat_counts: dict[ArtifactCategory, int] = {}
    cat_sizes: dict[ArtifactCategory, int] = {}
    for a in artifacts:
        cat_counts[a.category] = cat_counts.get(a.category, 0) + 1
        cat_sizes[a.category] = cat_sizes.get(a.category, 0) + a.size_bytes

    rag_prefix = find_rag_patterns_prefix(s3_client, bucket, key_prefix)
    patterns: list[str] = []
    if rag_prefix:
        patterns = discover_patterns(s3_client, bucket, rag_prefix)

    total_size = sum(cat_sizes.values())

    if args.json:
        _print_json({
            "run_id": args.run_id,
            "bucket": bucket,
            "prefix": key_prefix,
            "total_artifacts": len(artifacts),
            "total_size": total_size,
            "categories": {
                cat.value: {"count": cat_counts.get(cat, 0), "size": cat_sizes.get(cat, 0)}
                for cat in ArtifactCategory
            },
            "patterns": patterns,
        })
        return

    print(f"Artifacts for run {args.run_id}")
    print(f"Bucket: {bucket}  Prefix: {key_prefix}\n")

    for cat in ArtifactCategory:
        count = cat_counts.get(cat, 0)
        if not count:
            continue
        label = _CATEGORY_LABELS[cat]
        print(f"  {label:<25} {count:>6} file(s)  {_human_size(cat_sizes[cat]):>10}")

    print(f"\n  Total: {len(artifacts)} artifact(s), {_human_size(total_size)}")

    if patterns:
        print(f"\n  RAG Patterns ({len(patterns)}):")
        for p in patterns:
            print(f"    {p}")
        print("\n  Use --pattern <name> to browse, --pattern all to list all.")

    if args.download:
        objects = [{"Key": a.key, "Size": a.size_bytes} for a in artifacts]
        if len(objects) > 1000:
            print(f"\n  Downloading {len(objects)} objects...")
        print()
        _download_objects(s3_client, bucket, objects, key_prefix, args.download)


def cmd_artifacts(kfp_client: Any, s3_client: Any, args: argparse.Namespace) -> None:
    """List or download S3 artifacts from an AutoRAG pipeline run."""
    location = resolve(
        kfp_client, s3_client, args.run_id,
        explicit_prefix=args.prefix, explicit_bucket=args.bucket,
    )
    if not location:
        sys.exit(f"Could not locate artifacts for run {args.run_id}.")

    if args.pattern:
        _cmd_artifacts_pattern(s3_client, location.bucket, location.prefix, args)
        return

    if args.artifact:
        sys.exit("--artifact requires --pattern. Usage: --pattern <name> --artifact <file>")

    if args.print_content:
        sys.exit("--print requires --pattern and --artifact.")

    _cmd_artifacts_summary(s3_client, location.bucket, location.prefix, args)


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
        prog="autorag",
        description="Manage AutoRAG experiment results, artifacts, and analysis.",
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

    # artifacts
    p = sub.add_parser("artifacts", help="List or download S3 artifacts from a run")
    p.add_argument("run_id", help="KFP run ID (UUID)")
    p.add_argument("--prefix", help="Explicit S3 prefix override (skip auto-resolution)")
    p.add_argument("--bucket", help="Explicit bucket override")
    p.add_argument(
        "--pattern",
        help="RAG pattern name or 'all' (e.g. --pattern Pattern1, --pattern all)",
    )
    p.add_argument(
        "--artifact",
        help="Artifact filename within a pattern (requires --pattern)",
    )
    p.add_argument(
        "--print", action="store_true", default=False, dest="print_content",
        help="Output artifact content to stdout (requires --pattern and --artifact)",
    )
    p.add_argument("--download", metavar="DIR", help="Download artifacts to directory")

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
        "artifacts": cmd_artifacts,
    }
    commands[args.command](kfp_client, s3_client, args)


if __name__ == "__main__":
    main()
