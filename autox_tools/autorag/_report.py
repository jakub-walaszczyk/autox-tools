"""PDF report generation for experiment results.

Uses matplotlib to produce portrait-oriented multi-page reports with
convergence charts and per-metric evolution with confidence intervals.
All matplotlib imports are guarded so the module can be imported even when
matplotlib is not installed — ``require_matplotlib()`` raises a clear
error at runtime when ``--pdf`` is requested without the library.
"""

from __future__ import annotations

import sys
from typing import Any

from autox_tools.autorag._display import format_duration, is_lower_better
from autox_tools.autorag._patterns import PatternMetrics, RunPatternData, natural_sort_key

try:
    import matplotlib as mpl

    mpl.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


def require_matplotlib() -> None:
    """Exit with an install hint when matplotlib is unavailable."""
    if not HAS_MATPLOTLIB:
        sys.exit(
            "matplotlib is required for PDF reports.\n"
            "Install it with:  uv pip install matplotlib\n"
            "Or:               uv sync --extra reports"
        )


# ---------------------------------------------------------------------------
# Style and constants
# ---------------------------------------------------------------------------

_PALETTE = [
    "#2E86C1", "#E74C3C", "#27AE60", "#F39C12",
    "#8E44AD", "#1ABC9C", "#D35400", "#34495E",
]

_PAGE_W = 8.5
_PAGE_H = 11.0


def _set_academic_style() -> None:
    """Configure matplotlib for clean, publication-ready portrait figures."""
    plt.rcParams.update({
        "font.family": "serif",
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.labelsize": 11,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "legend.fontsize": 9,
        "figure.titlesize": 14,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "grid.linestyle": "--",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.dpi": 150,
        "savefig.dpi": 150,
        "figure.figsize": (_PAGE_W, _PAGE_H),
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_ci_bounds(
    pattern: PatternMetrics, metric_name: str,
) -> tuple[float | None, float | None]:
    """Extract confidence interval bounds from a pattern's raw score data."""
    scores = pattern.raw_data.get("scores")
    if not isinstance(scores, dict):
        return None, None
    score_data = scores.get(metric_name)
    if not isinstance(score_data, dict):
        return None, None
    ci_low = score_data.get("ci_low")
    ci_high = score_data.get("ci_high")
    if isinstance(ci_low, (int, float)) and isinstance(ci_high, (int, float)):
        return float(ci_low), float(ci_high)
    return None, None


def _fmt_param(value: Any) -> str:
    """Format a pipeline parameter value for title page display."""
    if value is None:
        return "—"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}: {v}" for k, v in sorted(value.items()))
    return str(value)


# ---------------------------------------------------------------------------
# Title page
# ---------------------------------------------------------------------------


def _render_title_page(
    pdf: Any,
    title: str,
    subtitle: str,
    info_lines: list[str],
    params: dict[str, Any] | None = None,
) -> None:
    """Render a title page with run metadata and optional pipeline parameters."""
    fig, ax = plt.subplots(figsize=(_PAGE_W, _PAGE_H))
    ax.axis("off")

    ax.text(
        0.5, 0.88, title,
        transform=ax.transAxes, ha="center", va="center",
        fontsize=22, fontweight="bold", fontfamily="serif",
    )
    ax.text(
        0.5, 0.82, subtitle,
        transform=ax.transAxes, ha="center", va="center",
        fontsize=14, fontfamily="serif", color="#555555",
    )

    info_text = "\n".join(info_lines)
    ax.text(
        0.5, 0.62, info_text,
        transform=ax.transAxes, ha="center", va="center",
        fontsize=10, fontfamily="monospace", color="#333333",
        linespacing=1.8,
    )

    if params:
        items = sorted(params.items())[:15]
        rows = [[str(k), _fmt_param(v)] for k, v in items]

        ax.text(
            0.5, 0.38, "Pipeline Parameters",
            transform=ax.transAxes, ha="center", va="center",
            fontsize=12, fontweight="bold", fontfamily="serif", color="#333333",
        )

        table = ax.table(
            cellText=rows,
            colLabels=["Parameter", "Value"],
            cellLoc="left",
            loc="center",
            bbox=[0.10, 0.04, 0.80, 0.30],
        )
        table.auto_set_font_size(False)
        table.set_fontsize(9)

        for (row, col), cell in table.get_celld().items():
            cell.set_edgecolor("#CCCCCC")
            if row == 0:
                cell.set_facecolor("#2E86C1")
                cell.set_text_props(color="white", fontweight="bold")
            elif row % 2 == 0:
                cell.set_facecolor("#F0F4F8")
            else:
                cell.set_facecolor("white")
            if col == 0:
                cell.set_text_props(fontfamily="monospace")

    pdf.savefig(fig)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Convergence subplot
# ---------------------------------------------------------------------------


def _plot_convergence(
    ax: Any,
    ordered: list[PatternMetrics],
    names: list[str],
    primary_metric: str | None,
) -> None:
    """Plot primary metric convergence with best-so-far envelope."""
    if not primary_metric:
        ax.text(
            0.5, 0.5, "No primary metric detected",
            transform=ax.transAxes, ha="center", va="center", fontsize=12,
        )
        ax.set_title("Convergence", fontweight="bold")
        return

    values = [p.metrics.get(primary_metric) for p in ordered]
    valid_x = [i for i, v in enumerate(values) if v is not None]
    valid_y = [v for v in values if v is not None]

    if not valid_y:
        return

    lower_better = is_lower_better(primary_metric)
    best_so_far: list[float] = []
    current_best = float("inf") if lower_better else float("-inf")
    for v in valid_y:
        current_best = min(current_best, v) if lower_better else max(current_best, v)
        best_so_far.append(current_best)

    ax.plot(
        valid_x, valid_y, marker="o", linewidth=1, alpha=0.5,
        color=_PALETTE[0], label="Per-iteration score",
    )
    ax.plot(
        valid_x, best_so_far, marker="s", linewidth=2.5,
        color=_PALETTE[2], label="Best so far",
    )

    best_val = min(valid_y) if lower_better else max(valid_y)
    best_idx = valid_y.index(best_val)
    ax.scatter(
        valid_x[best_idx], best_val, s=150, zorder=5,
        color=_PALETTE[1],
        label=f"Best: {names[valid_x[best_idx]]} ({best_val:.4f})",
    )

    ax.set_xlabel("Iteration")
    ax.set_ylabel(primary_metric)
    ax.set_ylim(0, 1.1)
    ax.set_title("Convergence", fontweight="bold")
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=45, ha="right", fontsize=8)
    ax.legend(loc="best", fontsize=8)


# ---------------------------------------------------------------------------
# All-metrics-with-CI subplot
# ---------------------------------------------------------------------------


def _plot_metrics_with_ci(
    ax: Any,
    ordered: list[PatternMetrics],
    names: list[str],
) -> None:
    """Plot all metrics across iterations with optional CI shading."""
    all_metrics = sorted({m for p in ordered for m in p.metrics})
    if not all_metrics:
        return

    has_any_ci = False

    for i, metric in enumerate(all_metrics):
        color = _PALETTE[i % len(_PALETTE)]
        means: list[float] = []
        lows: list[float] = []
        highs: list[float] = []
        valid_x: list[int] = []
        ci_complete = True

        for j, p in enumerate(ordered):
            val = p.metrics.get(metric)
            if val is not None:
                valid_x.append(j)
                means.append(val)
                lo, hi = _extract_ci_bounds(p, metric)
                if lo is not None and hi is not None:
                    lows.append(lo)
                    highs.append(hi)
                else:
                    ci_complete = False

        if not means:
            continue

        ax.plot(valid_x, means, marker="o", linewidth=2, color=color, label=metric)

        if ci_complete and lows:
            ax.fill_between(
                valid_x,
                [float(v) for v in lows],
                [float(v) for v in highs],
                alpha=0.15, color=color,
            )
            has_any_ci = True

    ax.set_xlabel("Iteration")
    ax.set_ylabel("Mean Score")
    ax.set_ylim(0, 1.1)
    title = "Metric Scores Across Iterations"
    if has_any_ci:
        title += " (shaded: confidence intervals)"
    ax.set_title(title, fontweight="bold")
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=45, ha="right", fontsize=8)
    ax.legend(loc="best", fontsize=8)


# ---------------------------------------------------------------------------
# Single-run charts page (2 charts, portrait)
# ---------------------------------------------------------------------------


def _render_charts_page(pdf: Any, data: RunPatternData) -> None:
    """Render convergence and metrics charts on a single portrait page."""
    if not data.patterns:
        return

    ordered = sorted(data.patterns, key=lambda p: natural_sort_key(p.name))
    names = [p.name for p in ordered]

    fig, (ax_top, ax_bot) = plt.subplots(2, 1, figsize=(_PAGE_W, _PAGE_H))
    fig.subplots_adjust(hspace=0.50, top=0.94, bottom=0.08, left=0.12, right=0.95)

    _plot_convergence(ax_top, ordered, names, data.primary_metric)
    _plot_metrics_with_ci(ax_bot, ordered, names)

    pdf.savefig(fig)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Comparison charts page (2 charts, portrait)
# ---------------------------------------------------------------------------


def _plot_comparison_convergence(
    ax: Any,
    data1: RunPatternData,
    data2: RunPatternData,
    label1: str,
    label2: str,
) -> None:
    """Overlay convergence curves for two runs."""
    primary = data1.primary_metric or data2.primary_metric
    if not primary:
        ax.text(
            0.5, 0.5, "No primary metric detected",
            transform=ax.transAxes, ha="center", va="center", fontsize=12,
        )
        ax.set_title("Convergence Comparison", fontweight="bold")
        return

    lower_better = is_lower_better(primary)

    for run_data, label, color in [
        (data1, label1, _PALETTE[0]),
        (data2, label2, _PALETTE[1]),
    ]:
        ordered = sorted(run_data.patterns, key=lambda p: natural_sort_key(p.name))
        values = [p.metrics.get(primary) for p in ordered]
        valid_x = [i for i, v in enumerate(values) if v is not None]
        valid_y = [v for v in values if v is not None]

        if not valid_y:
            continue

        best_so_far: list[float] = []
        current = float("inf") if lower_better else float("-inf")
        for v in valid_y:
            current = min(current, v) if lower_better else max(current, v)
            best_so_far.append(current)

        ax.plot(
            valid_x, valid_y, marker="o", linewidth=1, alpha=0.4, color=color,
        )
        ax.plot(
            valid_x, best_so_far, marker="s", linewidth=2,
            color=color, label=f"{label} (best so far)",
        )

    ax.set_xlabel("Iteration")
    ax.set_ylabel(primary)
    ax.set_ylim(0, 1.1)
    ax.set_title("Convergence Comparison", fontweight="bold")
    ax.legend(loc="best", fontsize=8)


def _plot_metric_comparison_with_ci(
    ax: Any,
    data1: RunPatternData,
    data2: RunPatternData,
    metric: str,
    label1: str,
    label2: str,
) -> None:
    """Plot a single metric for two runs with CI bands."""
    for run_data, label, color in [
        (data1, label1, _PALETTE[0]),
        (data2, label2, _PALETTE[1]),
    ]:
        ordered = sorted(run_data.patterns, key=lambda p: natural_sort_key(p.name))
        means: list[float] = []
        lows: list[float] = []
        highs: list[float] = []
        valid_x: list[int] = []
        ci_complete = True

        for j, p in enumerate(ordered):
            val = p.metrics.get(metric)
            if val is not None:
                valid_x.append(j)
                means.append(val)
                lo, hi = _extract_ci_bounds(p, metric)
                if lo is not None and hi is not None:
                    lows.append(lo)
                    highs.append(hi)
                else:
                    ci_complete = False

        if not means:
            continue

        ax.plot(valid_x, means, marker="o", linewidth=2, color=color, label=label)

        if ci_complete and lows:
            ax.fill_between(
                valid_x,
                [float(v) for v in lows],
                [float(v) for v in highs],
                alpha=0.15, color=color,
            )

    ax.set_xlabel("Iteration")
    ax.set_ylabel(metric)
    ax.set_ylim(0, 1.1)
    ax.set_title(f"{metric} — Comparison", fontweight="bold")
    ax.legend(loc="best", fontsize=8)


def _render_params_table(
    ax: Any,
    data: RunPatternData,
    label: str,
) -> None:
    """Render a styled parameter table on an axis."""
    ax.axis("off")
    ax.set_title(f"Pipeline Parameters — {label}", fontweight="bold", fontsize=11)

    if not data.pipeline_params:
        ax.text(
            0.5, 0.5, "(no parameters)",
            transform=ax.transAxes, ha="center", va="center",
            fontsize=10, color="#888888",
        )
        return

    items = sorted(data.pipeline_params.items())[:15]
    rows = [[str(k), _fmt_param(v)] for k, v in items]

    table = ax.table(
        cellText=rows,
        colLabels=["Parameter", "Value"],
        cellLoc="left",
        loc="center",
        bbox=[0.05, 0.0, 0.90, 0.85],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)

    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor("#CCCCCC")
        if row == 0:
            cell.set_facecolor("#2E86C1")
            cell.set_text_props(color="white", fontweight="bold")
        elif row % 2 == 0:
            cell.set_facecolor("#F0F4F8")
        else:
            cell.set_facecolor("white")
        if col == 0:
            cell.set_text_props(fontfamily="monospace")


def _render_params_comparison_page(
    pdf: Any,
    data1: RunPatternData,
    data2: RunPatternData,
    label1: str,
    label2: str,
) -> None:
    """Render pipeline parameters for both runs as two tables on one page."""
    fig, (ax_top, ax_bot) = plt.subplots(2, 1, figsize=(_PAGE_W, _PAGE_H))
    fig.subplots_adjust(hspace=0.25, top=0.94, bottom=0.05, left=0.08, right=0.92)

    _render_params_table(ax_top, data1, label1)
    _render_params_table(ax_bot, data2, label2)

    pdf.savefig(fig)
    plt.close(fig)


def _render_comparison_charts(
    pdf: Any,
    data1: RunPatternData,
    data2: RunPatternData,
    label1: str,
    label2: str,
) -> None:
    """Render comparison charts: convergence + one page per metric pair."""
    shared_metrics = sorted(
        {m for p in data1.patterns for m in p.metrics}
        & {m for p in data2.patterns for m in p.metrics},
    )

    plots: list[Any] = []
    plots.append(
        lambda ax: _plot_comparison_convergence(ax, data1, data2, label1, label2)
    )
    for metric in shared_metrics:
        plots.append(
            lambda ax, m=metric: _plot_metric_comparison_with_ci(
                ax, data1, data2, m, label1, label2,
            )
        )

    for i in range(0, len(plots), 2):
        fig, (ax_top, ax_bot) = plt.subplots(2, 1, figsize=(_PAGE_W, _PAGE_H))
        fig.subplots_adjust(hspace=0.50, top=0.94, bottom=0.08, left=0.12, right=0.95)

        plots[i](ax_top)

        if i + 1 < len(plots):
            plots[i + 1](ax_bot)
        else:
            ax_bot.axis("off")

        pdf.savefig(fig)
        plt.close(fig)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_results_pdf(
    data: RunPatternData,
    output_path: str,
    names: dict[str, str],
) -> None:
    """Generate a single-run experiment report as a multi-page PDF."""
    require_matplotlib()
    _set_academic_style()

    display = names.get(data.run_id, data.display_name)

    info_lines = [f"Run ID:   {data.run_id}"]
    if data.pipeline_name:
        info_lines.append(f"Pipeline: {data.pipeline_name}")
    if data.state:
        info_lines.append(f"State:    {data.state}")
    if data.duration_seconds is not None:
        info_lines.append(f"Duration: {format_duration(data.duration_seconds)}")
    if data.created_at:
        info_lines.append(f"Created:  {data.created_at.strftime('%Y-%m-%d %H:%M UTC')}")
    info_lines.append(f"Patterns: {len(data.patterns)}")
    if data.primary_metric:
        info_lines.append(f"Primary:  {data.primary_metric}")

    with PdfPages(output_path) as pdf:
        _render_title_page(
            pdf,
            title="Experiment Results Report",
            subtitle=display,
            info_lines=info_lines,
            params=data.pipeline_params or None,
        )

        if data.patterns:
            _render_charts_page(pdf, data)


def generate_compare_pdf(
    data1: RunPatternData,
    data2: RunPatternData,
    output_path: str,
    names: dict[str, str],
) -> None:
    """Generate a two-run comparison report as a multi-page PDF."""
    require_matplotlib()
    _set_academic_style()

    label1 = names.get(data1.run_id, data1.display_name)
    label2 = names.get(data2.run_id, data2.display_name)

    info_lines: list[str] = []
    for run_label, run_data in [
        (f"Run 1 — {label1}", data1),
        (f"Run 2 — {label2}", data2),
    ]:
        info_lines.append(run_label)
        info_lines.append(f"  ID:       {run_data.run_id}")
        if run_data.pipeline_name:
            info_lines.append(f"  Pipeline: {run_data.pipeline_name}")
        if run_data.state:
            info_lines.append(f"  State:    {run_data.state}")
        if run_data.duration_seconds is not None:
            info_lines.append(f"  Duration: {format_duration(run_data.duration_seconds)}")
        info_lines.append(f"  Patterns: {len(run_data.patterns)}")
        info_lines.append("")

    with PdfPages(output_path) as pdf:
        _render_title_page(
            pdf,
            title="Experiment Comparison Report",
            subtitle=f"{label1}  vs  {label2}",
            info_lines=info_lines,
        )

        _render_params_comparison_page(pdf, data1, data2, label1, label2)

        if data1.patterns and data2.patterns:
            _render_comparison_charts(pdf, data1, data2, label1, label2)
