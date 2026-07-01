"""Terminal display helpers for experiment data."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from autox_tools._output import format_duration

if TYPE_CHECKING:
    from datetime import datetime

    from autox_tools.autorag._patterns import PatternMetrics

MAX_WIDTH = 100
SEPARATOR = "=" * MAX_WIDTH

LOWER_IS_BETTER_KEYWORDS = {"latency", "error", "loss", "time", "duration", "cost"}

EXCLUDED_METRICS = frozenset({"max_combinations", "iteration"})
EXCLUDED_METRIC_KEYWORDS = frozenset({"duration"})


def short_id(run_id: str) -> str:
    """Return the first 8 characters of a run ID."""
    return run_id[:8]


def _is_excluded_metric(name: str) -> bool:
    """Check whether a metric should be hidden from console display."""
    lower = name.lower()
    if lower in EXCLUDED_METRICS:
        return True
    return any(kw in lower for kw in EXCLUDED_METRIC_KEYWORDS)


def filter_metric_names(names: list[str]) -> list[str]:
    """Remove excluded metrics from a list of metric names."""
    return [n for n in names if not _is_excluded_metric(n)]


def filter_metric_dict(metrics: dict[str, float]) -> dict[str, float]:
    """Remove excluded metrics from a metric dictionary."""
    return {k: v for k, v in metrics.items() if not _is_excluded_metric(k)}


def delta_indicator(value: float, lower_is_better: bool = False) -> str:
    """Format a delta value with direction indicator.

    ``▲`` marks an improvement, ``▼`` marks a regression.
    """
    sign = "+" if value > 0 else ""
    arrow = ""
    if value != 0:
        improving = (value < 0) if lower_is_better else (value > 0)
        arrow = " ▲" if improving else " ▼"
    return f"{sign}{value:.4f}{arrow}"


def is_lower_better(metric_name: str) -> bool:
    """Heuristic: determine if lower values are better for a metric."""
    name_lower = metric_name.lower()
    return any(kw in name_lower for kw in LOWER_IS_BETTER_KEYWORDS)


# ---------------------------------------------------------------------------
# Rich formatting helpers
# ---------------------------------------------------------------------------


def format_run_header(
    run_id: str,
    display_name: str | None = None,
    source_key: str | None = None,
    *,
    pipeline_name: str | None = None,
    state: str | None = None,
    duration_seconds: float | None = None,
    created_at: datetime | None = None,
) -> str:
    """Format the metadata header for a single run."""
    lines: list[str] = []
    title = display_name or run_id
    lines.append(SEPARATOR)
    lines.append(f"  Experiment Results: {title}")
    if display_name and display_name != run_id:
        lines.append(f"  Run ID:    {run_id}")
    if pipeline_name:
        lines.append(f"  Pipeline:  {pipeline_name}")
    if state:
        lines.append(f"  State:     {state}")
    if duration_seconds is not None:
        lines.append(f"  Duration:  {format_duration(duration_seconds)}")
    if created_at:
        ts = created_at.strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"  Created:   {ts}")
    if source_key:
        lines.append(f"  Source:    {source_key}")
    lines.append(SEPARATOR)
    return "\n".join(lines)


def format_compare_header(
    id1: str, id2: str,
    name1: str | None = None, name2: str | None = None,
    *,
    pipeline1: str | None = None, pipeline2: str | None = None,
    duration1: float | None = None, duration2: float | None = None,
    state1: str | None = None, state2: str | None = None,
) -> str:
    """Format the header for a two-run comparison."""
    label1 = name1 or short_id(id1)
    label2 = name2 or short_id(id2)
    lines = [
        SEPARATOR,
        f"  Comparison: {label1}  vs  {label2}",
    ]
    if label1 != id1 or label2 != id2:
        lines.append(f"  Run A: {id1}")
        lines.append(f"  Run B: {id2}")

    def _run_detail(label: str, pipeline: str | None, state: str | None, duration: float | None) -> str | None:
        parts: list[str] = []
        if pipeline:
            parts.append(pipeline)
        if state:
            parts.append(state)
        if duration is not None:
            parts.append(format_duration(duration))
        return f"  {label}: {' | '.join(parts)}" if parts else None

    detail_a = _run_detail("Run A", pipeline1, state1, duration1)
    detail_b = _run_detail("Run B", pipeline2, state2, duration2)
    if detail_a:
        lines.append(detail_a)
    if detail_b:
        lines.append(detail_b)

    lines.append(SEPARATOR)
    return "\n".join(lines)


def _fmt(value: float) -> str:
    """Format a metric value to 4 decimal places."""
    return f"{value:.4f}" if isinstance(value, float) else str(value)


def format_summary_metrics(
    metrics: dict[str, float],
    primary_metric: str | None = None,
) -> str:
    """Format summary metrics as an aligned block.

    The primary metric is listed first, followed by the rest
    in alphabetical order.
    """
    if not metrics:
        return "  (no summary metrics)"

    title = "  Summary Metrics"
    if primary_metric:
        title += f" (optimization: {primary_metric})"
    lines: list[str] = [f"\n{title}", "  " + "-" * 60]

    names = sorted(metrics.keys())
    if primary_metric and primary_metric in metrics:
        names.remove(primary_metric)
        names.insert(0, primary_metric)

    max_name = max(len(k) for k in names)
    for name in names:
        lines.append(f"  {name:<{max_name}}  {_fmt(metrics[name])}")
    return "\n".join(lines)


def format_leaderboard(
    patterns: list[PatternMetrics],
    primary_metric: str | None,
    all_metrics: list[str] | None = None,
) -> str:
    """Format a ranked leaderboard table of patterns.

    Patterns are ranked by *primary_metric* (descending for
    higher-is-better, ascending for lower-is-better).
    """
    from tabulate import tabulate as _tabulate

    if not patterns:
        return "  (no pattern data available)"

    if primary_metric is None:
        metric_set: set[str] = set()
        for p in patterns:
            metric_set.update(p.metrics)
        primary_metric = sorted(metric_set)[0] if metric_set else None

    if primary_metric is None:
        return "  (no metrics to rank by)"

    if all_metrics is None:
        metric_set = set()
        for p in patterns:
            metric_set.update(p.metrics)
        all_metrics = sorted(metric_set)

    if primary_metric in all_metrics:
        all_metrics = [primary_metric] + [m for m in all_metrics if m != primary_metric]

    reverse = not is_lower_better(primary_metric)
    ranked = sorted(
        patterns,
        key=lambda p: p.metrics.get(primary_metric, float("-inf") if reverse else float("inf")),
        reverse=reverse,
    )

    headers = ["#", "Pattern", *all_metrics]
    rows: list[list[str]] = []
    for rank, p in enumerate(ranked, 1):
        row: list[str] = [str(rank), p.name]
        for m in all_metrics:
            v = p.metrics.get(m)
            row.append(_fmt(v) if v is not None else "—")
        rows.append(row)

    lines = [
        f"\n  Leaderboard (ranked by {primary_metric})",
        "  " + "-" * 60,
    ]
    table = _tabulate(rows, headers=headers, tablefmt="simple")
    for line in table.splitlines():
        lines.append(f"  {line}")
    return "\n".join(lines)


def format_pattern_detail(pattern: PatternMetrics) -> str:
    """Format one pattern's detailed metrics as an indented block."""
    lines = [f"\n  [{pattern.name}]"]
    if not pattern.metrics:
        lines.append("    (no metrics)")
        return "\n".join(lines)
    max_name = max(len(k) for k in pattern.metrics)
    for name, value in sorted(pattern.metrics.items()):
        lines.append(f"    {name:<{max_name}}  {_fmt(value)}")
    return "\n".join(lines)


_PATTERN_NOISE_KEYS = frozenset({
    "name", "iteration", "max_combinations", "duration_seconds",
    "final_score", "scores", "metrics",
})

_SETTING_SKIP_KEYS = frozenset({
    "context_template_text", "user_message_text", "system_message_text",
})


def _format_flat_dict(
    data: dict[str, Any], indent: str = "    ",
) -> list[str]:
    """Format a flat dict as aligned key-value lines, skipping nulls."""
    items = {
        k: v for k, v in data.items()
        if v is not None and k not in _SETTING_SKIP_KEYS
    }
    if not items:
        return []
    max_key = max(len(str(k)) for k in items)
    lines: list[str] = []
    for key in sorted(items.keys()):
        value = items[key]
        if isinstance(value, dict):
            inner = _format_flat_dict(value, indent + "  ")
            if inner:
                lines.append(f"{indent}{key}:")
                lines.extend(inner)
        elif isinstance(value, list):
            formatted = ", ".join(str(v) for v in value)
            line = f"{indent}{key:<{max_key}}  {formatted}"
            if len(line) > MAX_WIDTH:
                lines.append(f"{indent}{key}:")
                for v in value:
                    lines.append(f"{indent}  - {v}")
            else:
                lines.append(line)
        else:
            lines.append(f"{indent}{key}: {value}")
    return lines


def format_pattern_settings(pattern: PatternMetrics) -> str:
    """Format the pattern configuration from raw_data.

    Prefers the nested ``settings`` dict when present (AutoRAG format).
    Falls back to showing all non-metric, non-noise top-level keys.
    """
    raw = pattern.raw_data
    settings = raw.get("settings")

    if isinstance(settings, dict) and settings:
        lines: list[str] = []
        for section_name in sorted(settings.keys()):
            section = settings[section_name]
            label = section_name.replace("_", " ").title()
            if isinstance(section, dict):
                inner = _format_flat_dict(section)
                if inner:
                    lines.append(f"    {label}:")
                    lines.extend(f"      {line.strip()}" for line in inner)
            else:
                lines.append(f"    {label}: {section}")
        return "\n".join(lines) if lines else "    (no settings data)"

    remaining = {
        k: v for k, v in raw.items()
        if k not in pattern.metrics and k not in _PATTERN_NOISE_KEYS
    }
    if not remaining:
        return "    (no settings data)"
    return "\n".join(_format_flat_dict(remaining))


def _format_param_value(value: Any) -> str:
    """Format a single pipeline parameter value for display."""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}: {v}" for k, v in sorted(value.items()))
    return str(value)


def format_pipeline_params(params: dict[str, Any]) -> str:
    """Format pipeline input parameters as an aligned key-value block."""
    if not params:
        return ""

    lines = ["\n  Pipeline Parameters", "  " + "-" * 60]
    max_key = max(len(str(k)) for k in params)
    for key in sorted(params.keys()):
        formatted = _format_param_value(params[key])
        line = f"  {key:<{max_key}}  {formatted}"
        if len(line) > MAX_WIDTH and isinstance(params[key], (list, dict)):
            lines.append(f"  {key}:")
            if isinstance(params[key], dict):
                for k, v in sorted(params[key].items()):
                    lines.append(f"    {k}: {v}")
            else:
                for v in params[key]:
                    lines.append(f"    - {v}")
        else:
            lines.append(line)
    return "\n".join(lines)


def _format_scores_with_ci(pattern: PatternMetrics) -> list[str]:
    """Format pattern scores with confidence intervals when available."""
    scores_raw = pattern.raw_data.get("scores")
    if not isinstance(scores_raw, dict):
        return _format_flat_dict(pattern.metrics)

    lines: list[str] = []
    max_name = max(len(k) for k in pattern.metrics) if pattern.metrics else 0
    for name in sorted(pattern.metrics):
        mean = pattern.metrics[name]
        score_data = scores_raw.get(name, {})
        ci_low = score_data.get("ci_low") if isinstance(score_data, dict) else None
        ci_high = score_data.get("ci_high") if isinstance(score_data, dict) else None
        val_str = f"{mean:.4f}"
        if ci_low is not None and ci_high is not None:
            val_str += f"  [{ci_low:.4f} - {ci_high:.4f}]"
        lines.append(f"    {name:<{max_name}}  {val_str}")
    return lines


def format_best_patterns(
    patterns: list[PatternMetrics],
    sort_metric: str | None,
    n: int = 1,
) -> str:
    """Format the top N patterns with scores and settings."""
    if not patterns or not sort_metric:
        return ""

    reverse = not is_lower_better(sort_metric)
    ranked = sorted(
        patterns,
        key=lambda p: p.metrics.get(
            sort_metric,
            float("-inf") if reverse else float("inf"),
        ),
        reverse=reverse,
    )

    top = ranked[:n]
    lines: list[str] = []
    for rank, p in enumerate(top, 1):
        score = p.metrics.get(sort_metric)
        score_str = f"{score:.4f}" if isinstance(score, float) else str(score)
        if n == 1:
            lines.append(f"\n  Top Pattern: {p.name} ({sort_metric}: {score_str})")
        else:
            lines.append(f"\n  #{rank} {p.name} ({sort_metric}: {score_str})")
        lines.append("  " + "-" * 60)

        score_lines = _format_scores_with_ci(p)
        if score_lines:
            lines.append("    Scores:")
            lines.extend(f"      {line.strip()}" for line in score_lines)
            lines.append("")

        settings_str = format_pattern_settings(p)
        if "(no settings data)" not in settings_str:
            lines.append("    Settings:")
            lines.append(settings_str)

    return "\n".join(lines)
