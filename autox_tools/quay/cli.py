"""Discover Quay repositories and resolve exact container image references."""

from __future__ import annotations

import argparse
import sys

import yaml
from tabulate import tabulate

from autox_tools._output import human_size, print_json
from autox_tools.config._loader import find_config, resolve
from autox_tools.quay._client import ImageNotFoundError, QuayClient, QuayError, resolve_settings
from autox_tools.quay._filters import TagFilters, commit_value
from autox_tools.quay._references import parse_reference, registry_host


def _positive(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be a positive integer") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("limit must be a positive integer")
    return number


def _commit(value: str) -> str:
    try:
        return commit_value(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _globals(parser: argparse.ArgumentParser, *, subcommand: bool = False) -> None:
    # Suppress subparser defaults so options supplied before the command survive.
    default = argparse.SUPPRESS if subcommand else None
    parser.add_argument("--json", action="store_true", default=default, help="Output structured JSON")
    parser.add_argument("--profile", "-p", default=default, help="Configuration profile")
    parser.add_argument("--target", "-t", default=default, help="Named Quay configuration")


def _limits(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--limit", type=_positive, default=20, help="Matching result limit (default: 20)")
    group.add_argument("--all", action="store_true", help="Follow all available pages")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quay", description="Read-only Quay repository and image discovery.")
    _globals(parser)
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search", help="Find repositories across organizations")
    search.add_argument("query")
    search.add_argument("--org", help="Restrict search to this namespace")
    _limits(search)
    tags = commands.add_parser("tags", help="List active tags")
    tags.add_argument("repository")
    tags.add_argument("--prefix", default="", help="Case-sensitive tag prefix")
    tags.add_argument("--match", default="*", help="Case-sensitive shell glob")
    tags.add_argument("--commit", type=_commit, default=None, help="Hex commit token prefix (7-64 characters)")
    tags.add_argument("--include-artifacts", action="store_true", help="Include conventionally auxiliary tags")
    _limits(tags)
    for sub in (search, tags):
        sub.add_argument("--output", choices=("json", "table"), default="json", help="Output format (default: json)")
    latest = commands.add_parser("latest", help="Resolve an exact image tag or digest with manifest details")
    latest.add_argument("image", help="Exact repository:tag or repository@sha256:digest")
    latest.add_argument("--limit", "-n", type=_positive, default=1,
                        help="Number of recent assignments of this exact tag (default: 1)")
    latest.add_argument("--output", choices=("json", "table", "ref", "digest"), default="json",
                        help="Output format (default: json)")
    for sub in commands.choices.values():
        _globals(sub, subcommand=True)
    return parser


def _print_images(items: list[dict]) -> None:
    print(tabulate(
        [(item["tag"] or "—", item["updated_at"] or "—", item["digest"] or "—",
          human_size(item["size"]), item["media_type"] or (
              "manifest list" if item["is_manifest_list"] else
              "image" if item["is_manifest_list"] is False else "—"
          )) for item in items],
        headers=("Tag", "Updated (UTC)", "Digest", "Size", "Type"),
    ))


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.json and args.output != "json":
        parser.error("--json cannot be combined with another --output format")
    client = None
    try:
        cfg = resolve("quay", args)
        if cfg is None and (args.target or args.profile) and find_config() is None:
            raise QuayError("No .autox.yaml found for the requested profile or target")
        settings = resolve_settings(cfg)
        reference = None
        if args.command != "search":
            try:
                reference = parse_reference(
                    args.image if args.command == "latest" else args.repository,
                    registry_host(settings.base_url), settings.namespace, image=args.command == "latest",
                )
            except ValueError as exc:
                parser.error(str(exc))
            if args.command == "latest" and args.output == "ref" and reference.tag is None:
                parser.error("--output ref requires a tagged image; use --output digest for a digest reference")
            if args.command == "latest" and args.limit > 1:
                if reference.tag is None:
                    parser.error("-n requires a tagged reference; a digest identifies one image")
                if args.output == "ref":
                    parser.error("Use --output digest for historical images; tagged references can move")
        elif not args.query.strip():
            parser.error("search query must not be empty")

        client = QuayClient(settings)
        if args.command == "search":
            result = client.search(args.query, args.org, None if args.all else args.limit)
        elif args.command == "latest":
            assert reference is not None
            result = client.latest(reference, args.limit)
        else:
            assert reference is not None
            filters = TagFilters(args.prefix, args.match, args.commit or "", args.include_artifacts)
            result = client.tags(reference.repository, filters, None if args.all else args.limit)

        if args.output == "json":
            print_json(result)
        elif args.command == "latest":
            items = result["items"] if args.limit > 1 else [result]
            if args.output != "table":
                values = [item[f"{args.output if args.output == 'digest' else 'tag'}_ref"] for item in items]
                if not all(values):
                    raise QuayError("Quay did not provide the requested pull reference")
                print("\n".join(values))
            else:
                print(f"Repository: {client.host}/{items[0]['repository']}")
                _print_images(items)
                for item in items:
                    if item["platforms"]:
                        print(tabulate(item["platforms"], headers="keys"))
        else:
            if args.command == "tags":
                _print_images(result["items"])
            else:
                print(tabulate(
                    [(item["repository"], item["description"]) for item in result["items"]],
                    headers=("Repository", "Description"),
                ))
                print("Search discovery may be capped by Quay.", file=sys.stderr)
            print(f"\n{result['returned']} result(s)")
            if result["truncated"]:
                print("Results truncated; --all follows all available pages.", file=sys.stderr)
    except ImageNotFoundError as exc:
        print(f"quay: {exc}", file=sys.stderr)
        sys.exit(3)
    except yaml.YAMLError:
        print("quay: Invalid YAML configuration", file=sys.stderr)
        sys.exit(1)
    except (QuayError, OSError) as exc:
        print(f"quay: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    main()
