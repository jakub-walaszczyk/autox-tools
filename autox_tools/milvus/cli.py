"""CLI entry point for Milvus management commands.

Usage::

    uv run milvus list [--counts] [--json]
    uv run milvus describe <collection>
    uv run milvus drop <pattern> [--yes] [--dry-run]
    uv run milvus count [pattern]
    uv run milvus query <collection> <filter> [--output-fields F1,F2] [--limit N]
    uv run milvus export <collection> [--filter EXPR] [--limit N] [--output FILE]
    uv run milvus rename <old> <new>
    uv run milvus compact <collection>
    uv run milvus flush <collection>
    uv run milvus health
    uv run milvus partitions <collection>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import TYPE_CHECKING

from autox_tools.milvus._client import connect

if TYPE_CHECKING:
    from pymilvus import MilvusClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _match_collections(collections: list[str], pattern: str) -> list[str]:
    """Return collections matching *pattern* as a prefix or full regex."""
    try:
        regex = re.compile(pattern)
    except re.error:
        regex = re.compile(re.escape(pattern))
    return sorted(c for c in collections if regex.fullmatch(c) or c.startswith(pattern))


def _print_json(data: object) -> None:
    print(json.dumps(data, indent=2, default=str))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_list(client: MilvusClient, args: argparse.Namespace) -> None:
    """List all collections, optionally with row counts."""
    collections = sorted(client.list_collections())

    if args.json:
        rows = []
        for name in collections:
            entry: dict[str, object] = {"name": name}
            if args.counts:
                stats = client.get_collection_stats(name)
                entry["row_count"] = int(stats.get("row_count", 0))
            rows.append(entry)
        _print_json({"total": len(collections), "collections": rows})
        return

    print(f"Total collections: {len(collections)}\n")
    for name in collections:
        suffix = ""
        if args.counts:
            stats = client.get_collection_stats(name)
            suffix = f"  ({stats.get('row_count', '?')} rows)"
        print(f"  - {name}{suffix}")


def cmd_describe(client: MilvusClient, args: argparse.Namespace) -> None:
    """Show schema, indexes, partitions, and stats for a collection."""
    name = args.name
    if not client.has_collection(name):
        sys.exit(f"Collection '{name}' does not exist.")

    info = client.describe_collection(name)
    stats = client.get_collection_stats(name)
    indexes = client.list_indexes(name)
    partitions = client.list_partitions(name)

    if args.json:
        idx_details = {}
        for idx_name in indexes:
            idx_details[idx_name] = client.describe_index(name, idx_name)
        _print_json({
            "collection": name,
            "description": info.get("description", ""),
            "auto_id": info.get("auto_id"),
            "num_shards": info.get("num_shards"),
            "row_count": int(stats.get("row_count", 0)),
            "fields": info.get("fields", []),
            "indexes": idx_details,
            "partitions": partitions,
        })
        return

    print(f"Collection : {name}")
    print(f"Description: {info.get('description', '') or '(none)'}")
    print(f"Auto ID    : {info.get('auto_id')}")
    print(f"Num shards : {info.get('num_shards')}")
    print(f"Row count  : {stats.get('row_count', 'N/A')}")
    print()

    fields = info.get("fields", [])
    if fields:
        print("Fields:")
        for f in fields:
            parts = [f"  - {f['name']}", f"type={f.get('type')}"]
            if f.get("is_primary"):
                parts.append("PRIMARY")
            if f.get("params"):
                parts.append(f"params={f['params']}")
            print("  ".join(parts))
        print()

    if indexes:
        print("Indexes:")
        for idx_name in indexes:
            idx = client.describe_index(name, idx_name)
            print(f"  - {idx_name}: {idx}")
        print()

    if partitions:
        print(f"Partitions ({len(partitions)}):")
        for p in partitions:
            print(f"  - {p}")
        print()


def cmd_drop(client: MilvusClient, args: argparse.Namespace) -> None:
    """Drop collections matching a prefix or regex pattern."""
    matched = _match_collections(client.list_collections(), args.pattern)
    if not matched:
        print(f"No collections matching '{args.pattern}'.")
        return

    label = "Would drop" if args.dry_run else "About to drop"
    print(f"{label} {len(matched)} collection(s) matching '{args.pattern}':")
    for name in matched:
        print(f"  - {name}")

    if args.dry_run:
        return

    if not args.yes:
        answer = input("\nConfirm? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return

    for name in matched:
        client.drop_collection(name)
        print(f"  Dropped: {name}")
    print(f"\n{len(matched)} collection(s) dropped.")


def cmd_count(client: MilvusClient, args: argparse.Namespace) -> None:
    """Show row counts for all or pattern-matched collections."""
    collections = sorted(client.list_collections())
    if args.pattern:
        collections = _match_collections(collections, args.pattern)

    if not collections:
        print("No matching collections.")
        return

    rows: list[dict[str, object]] = []
    total_rows = 0
    for name in collections:
        stats = client.get_collection_stats(name)
        count = int(stats.get("row_count", 0))
        total_rows += count
        rows.append({"name": name, "row_count": count})

    if args.json:
        _print_json({"total_collections": len(rows), "total_rows": total_rows, "collections": rows})
        return

    max_name = max(len(r["name"]) for r in rows) if rows else 10  # type: ignore[arg-type]
    header = f"  {'Collection':<{max_name}}   Rows"
    print(header)
    print(f"  {'─' * max_name}   {'─' * 12}")
    for r in rows:
        print(f"  {r['name']:<{max_name}}   {r['row_count']:>12,}")
    print(f"\n  Total: {total_rows:,} rows across {len(rows)} collection(s)")


def cmd_query(client: MilvusClient, args: argparse.Namespace) -> None:
    """Run a filter expression against a collection and print results."""
    name = args.collection
    if not client.has_collection(name):
        sys.exit(f"Collection '{name}' does not exist.")

    output_fields: list[str] | None = None
    if args.output_fields:
        output_fields = [f.strip() for f in args.output_fields.split(",")]

    results = client.query(
        collection_name=name,
        filter=args.filter,
        output_fields=output_fields,
        limit=args.limit,
    )

    if args.json:
        _print_json(results)
        return

    if not results:
        print("No results.")
        return

    print(f"Returned {len(results)} row(s):\n")
    for i, row in enumerate(results):
        print(f"--- row {i} ---")
        for k, v in row.items():
            val = f"[{len(v)} dims]" if isinstance(v, list) and len(v) > 8 else v
            print(f"  {k}: {val}")
        print()


def cmd_export(client: MilvusClient, args: argparse.Namespace) -> None:
    """Export collection data to a JSONL file."""
    name = args.collection
    if not client.has_collection(name):
        sys.exit(f"Collection '{name}' does not exist.")

    info = client.describe_collection(name)
    all_fields = [f["name"] for f in info.get("fields", [])]

    filt = args.filter or 'pk != ""'
    batch_size = min(args.limit, 1000)
    collected: list[dict] = []
    offset = 0

    while len(collected) < args.limit:
        remaining = args.limit - len(collected)
        fetch = min(batch_size, remaining)
        batch = client.query(
            collection_name=name,
            filter=filt,
            output_fields=all_fields,
            limit=fetch,
            offset=offset,
        )
        if not batch:
            break
        collected.extend(batch)
        offset += len(batch)
        if len(batch) < fetch:
            break

    dest = args.output or f"{name}.jsonl"
    with open(dest, "w") as fh:
        for row in collected:
            fh.write(json.dumps(row, default=str) + "\n")

    print(f"Exported {len(collected)} row(s) to {dest}")


def cmd_rename(client: MilvusClient, args: argparse.Namespace) -> None:
    """Rename a collection."""
    if not client.has_collection(args.old):
        sys.exit(f"Collection '{args.old}' does not exist.")
    if client.has_collection(args.new):
        sys.exit(f"Collection '{args.new}' already exists.")

    client.rename_collection(args.old, args.new)
    print(f"Renamed '{args.old}' -> '{args.new}'")


def cmd_compact(client: MilvusClient, args: argparse.Namespace) -> None:
    """Trigger compaction on a collection."""
    name = args.collection
    if not client.has_collection(name):
        sys.exit(f"Collection '{name}' does not exist.")

    job_id = client.compact(name)
    print(f"Compaction started for '{name}' (job ID: {job_id})")


def cmd_flush(client: MilvusClient, args: argparse.Namespace) -> None:
    """Flush pending writes to storage."""
    name = args.collection
    if not client.has_collection(name):
        sys.exit(f"Collection '{name}' does not exist.")

    client.flush(name)
    print(f"Flushed '{name}'")


def cmd_health(client: MilvusClient, args: argparse.Namespace) -> None:
    """Show server connectivity and collection summary."""
    collections = client.list_collections()
    total_rows = 0
    for c in collections:
        stats = client.get_collection_stats(c)
        total_rows += int(stats.get("row_count", 0))

    if args.json:
        _print_json({
            "status": "connected",
            "total_collections": len(collections),
            "total_rows": total_rows,
        })
        return

    print("Status       : connected")
    print(f"Collections  : {len(collections)}")
    print(f"Total rows   : {total_rows:,}")


def cmd_partitions(client: MilvusClient, args: argparse.Namespace) -> None:
    """List partitions for a collection."""
    name = args.collection
    if not client.has_collection(name):
        sys.exit(f"Collection '{name}' does not exist.")

    partitions = client.list_partitions(name)

    if args.json:
        _print_json({"collection": name, "partitions": partitions})
        return

    print(f"Partitions for '{name}' ({len(partitions)}):\n")
    for p in partitions:
        print(f"  - {p}")


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="milvus",
        description="Manage remote Milvus vector database instances.",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    sub = parser.add_subparsers(dest="command", required=True)

    # list
    p = sub.add_parser("list", help="List all collections")
    p.add_argument("--counts", action="store_true", help="Include row counts (slower)")

    # describe
    p = sub.add_parser("describe", help="Show collection details")
    p.add_argument("name", help="Collection name")

    # drop
    p = sub.add_parser("drop", help="Drop collections matching a pattern")
    p.add_argument("pattern", help="Prefix or regex for collection names")
    p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    p.add_argument("--dry-run", action="store_true", help="Show what would be dropped without acting")

    # count
    p = sub.add_parser("count", help="Show row counts for collections")
    p.add_argument("pattern", nargs="?", default=None, help="Optional prefix/regex filter")

    # query
    p = sub.add_parser("query", help="Run a filter expression query")
    p.add_argument("collection", help="Collection name")
    p.add_argument("filter", help="Filter expression (e.g. 'id > 100')")
    p.add_argument("--output-fields", help="Comma-separated field names to return")
    p.add_argument("--limit", type=int, default=10, help="Max rows to return (default: 10)")

    # export
    p = sub.add_parser("export", help="Export collection data to JSONL")
    p.add_argument("collection", help="Collection name")
    p.add_argument("--filter", help="Filter expression to select rows")
    p.add_argument("--limit", type=int, default=10_000, help="Max rows to export (default: 10000)")
    p.add_argument("--output", "-o", help="Output file path (default: <collection>.jsonl)")

    # rename
    p = sub.add_parser("rename", help="Rename a collection")
    p.add_argument("old", help="Current collection name")
    p.add_argument("new", help="New collection name")

    # compact
    p = sub.add_parser("compact", help="Trigger compaction on a collection")
    p.add_argument("collection", help="Collection name")

    # flush
    p = sub.add_parser("flush", help="Flush pending writes to storage")
    p.add_argument("collection", help="Collection name")

    # health
    sub.add_parser("health", help="Check connection and show summary")

    # partitions
    p = sub.add_parser("partitions", help="List partitions for a collection")
    p.add_argument("collection", help="Collection name")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    client = connect()

    commands = {
        "list": cmd_list,
        "describe": cmd_describe,
        "drop": cmd_drop,
        "count": cmd_count,
        "query": cmd_query,
        "export": cmd_export,
        "rename": cmd_rename,
        "compact": cmd_compact,
        "flush": cmd_flush,
        "health": cmd_health,
        "partitions": cmd_partitions,
    }
    commands[args.command](client, args)


if __name__ == "__main__":
    main()
