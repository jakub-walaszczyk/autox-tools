"""Shared output formatting utilities used across CLI modules."""

from __future__ import annotations

import json
import sys
from typing import Any

_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB")


def print_json(data: Any) -> None:
    """Dump *data* as pretty-printed JSON to stdout."""
    json.dump(data, sys.stdout, indent=2, default=str)
    print()


def human_size(nbytes: int | None) -> str:
    """Format byte count as a human-readable string."""
    if nbytes is None:
        return "—"
    size = float(nbytes)
    for unit in _SIZE_UNITS[:-1]:
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} {_SIZE_UNITS[-1]}"


def format_duration(seconds: float) -> str:
    """Format seconds as a human-readable duration string."""
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m {secs}s"
