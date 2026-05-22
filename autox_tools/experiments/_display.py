"""Terminal display helpers for experiment data."""

from __future__ import annotations

_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB")

LOWER_IS_BETTER_KEYWORDS = {"latency", "error", "loss", "time", "duration", "cost"}


def format_size(size_bytes: int) -> str:
    """Format byte count as human-readable string."""
    size = float(size_bytes)
    for unit in _SIZE_UNITS[:-1]:
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} {_SIZE_UNITS[-1]}"


def format_duration(seconds: float) -> str:
    """Format seconds as human-readable duration."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m {secs}s"


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
