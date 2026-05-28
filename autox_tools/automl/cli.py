"""AutoML CLI — minimal skeleton for future AutoML experiment tooling.

Provides a single ``info`` subcommand as a starting point.  Additional
subcommands (results, compare, artifacts, …) will be added as the
AutoML evaluation pipeline matures.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _print_json(data: Any) -> None:
    """Dump *data* as pretty-printed JSON to stdout."""
    json.dump(data, sys.stdout, indent=2, default=str)
    print()


def cmd_info(args: argparse.Namespace) -> None:
    """Show AutoML tooling status."""
    info = {
        "status": "available",
        "description": "AutoML experiment management CLI",
        "subcommands": ["info"],
    }
    if args.json:
        _print_json(info)
    else:
        print("AutoML CLI")
        print("=" * 40)
        print("  Status: available")
        print("  This is a placeholder for future AutoML experiment tooling.")
        print("\n  Planned subcommands: results, compare, artifacts, export")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="automl",
        description="Manage and inspect AutoML experiment results.",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("info", help="Show AutoML tooling status")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    commands: dict[str, Any] = {
        "info": cmd_info,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
