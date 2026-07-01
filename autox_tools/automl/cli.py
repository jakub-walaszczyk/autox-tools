"""AutoML CLI — minimal skeleton for future AutoML experiment tooling.

Provides a single ``info`` subcommand as a starting point.  Additional
subcommands (results, compare, artifacts, …) will be added as the
AutoML evaluation pipeline matures.
"""

from __future__ import annotations

import argparse
from typing import Any

from autox_tools._output import print_json


def cmd_info(args: argparse.Namespace) -> None:
    """Show AutoML tooling status."""
    info = {
        "status": "available",
        "description": "AutoML experiment management CLI",
        "subcommands": ["info"],
    }
    if args.json:
        print_json(info)
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

    from autox_tools.config._loader import add_profile_args
    add_profile_args(parser)

    args = parser.parse_args()

    commands: dict[str, Any] = {
        "info": cmd_info,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
