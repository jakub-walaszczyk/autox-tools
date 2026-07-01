"""CLI entry point for PostgreSQL/pgvector management commands.

Usage::

    uv run vs pgvector list [--counts] [--json]
    uv run vs pgvector describe <table>
    uv run vs pgvector drop <pattern> [--yes] [--dry-run]
    uv run vs pgvector count [pattern]
    uv run vs pgvector query <table> <where> [--output-fields F1,F2] [--limit N]
    uv run vs pgvector export <table> [--filter EXPR] [--limit N] [--output FILE]
    uv run vs pgvector rename <old> <new>
    uv run vs pgvector vacuum <table>
    uv run vs pgvector health
    uv run vs pgvector indexes <table>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import TYPE_CHECKING

from autox_tools._output import human_size, print_json
from autox_tools.vs.pgvector._client import connect

if TYPE_CHECKING:
    from psycopg import Connection


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

_VECTOR_TABLES_SQL = """
SELECT DISTINCT c.table_name
FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_name = c.table_name AND t.table_schema = c.table_schema
WHERE c.table_schema = 'public'
  AND c.udt_name = 'vector'
  AND t.table_type = 'BASE TABLE'
ORDER BY c.table_name
"""

_TABLE_EXISTS_SQL = """
SELECT 1 FROM information_schema.tables
WHERE table_schema = 'public' AND table_name = %s AND table_type = 'BASE TABLE'
"""

_TABLE_COLUMNS_SQL = """
SELECT column_name, udt_name, is_nullable, column_default,
       character_maximum_length, numeric_precision
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = %s
ORDER BY ordinal_position
"""

_TABLE_INDEXES_SQL = """
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public' AND tablename = %s
ORDER BY indexname
"""

_ROW_COUNT_SQL = "SELECT count(*) FROM {table}"

_ROW_COUNT_ESTIMATE_SQL = """
SELECT reltuples::bigint FROM pg_class
WHERE relname = %s AND relnamespace = 'public'::regnamespace
"""

_HEALTH_VERSION_SQL = "SELECT version()"

_HEALTH_EXTENSION_SQL = """
SELECT extname, extversion FROM pg_extension WHERE extname = 'vector'
"""

_INDEX_DETAIL_SQL = """
SELECT i.indexname, i.indexdef,
       pg_relation_size(c.oid) AS size_bytes
FROM pg_indexes i
JOIN pg_class c ON c.relname = i.indexname
WHERE i.schemaname = 'public' AND i.tablename = %s
ORDER BY i.indexname
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _table_exists(conn: Connection, name: str) -> bool:
    row = conn.execute(_TABLE_EXISTS_SQL, (name,)).fetchone()
    return row is not None


def _list_vector_tables(conn: Connection) -> list[str]:
    return [row[0] for row in conn.execute(_VECTOR_TABLES_SQL).fetchall()]


def _row_count(conn: Connection, table: str) -> int:
    row = conn.execute(_ROW_COUNT_SQL.format(table=_quote_ident(table))).fetchone()
    return row[0] if row else 0


def _quote_ident(name: str) -> str:
    """Quote a SQL identifier to prevent injection."""
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", name):
        safe = name.replace('"', '""')
        return f'"{safe}"'
    return name


def _match_tables(tables: list[str], pattern: str) -> list[str]:
    try:
        regex = re.compile(pattern)
    except re.error:
        regex = re.compile(re.escape(pattern))
    return sorted(t for t in tables if regex.fullmatch(t) or t.startswith(pattern))




# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_list(conn: Connection, args: argparse.Namespace) -> None:
    """List tables that contain vector columns."""
    tables = _list_vector_tables(conn)

    if args.json:
        rows = []
        for name in tables:
            entry: dict[str, object] = {"name": name}
            if args.counts:
                entry["row_count"] = _row_count(conn, name)
            rows.append(entry)
        print_json({"total": len(tables), "tables": rows})
        return

    print(f"Total tables with vector columns: {len(tables)}\n")
    for name in tables:
        suffix = ""
        if args.counts:
            suffix = f"  ({_row_count(conn, name):,} rows)"
        print(f"  - {name}{suffix}")


def cmd_describe(conn: Connection, args: argparse.Namespace) -> None:
    """Show schema, indexes, and row count for a table."""
    name = args.name
    if not _table_exists(conn, name):
        sys.exit(f"Table '{name}' does not exist.")

    columns = conn.execute(_TABLE_COLUMNS_SQL, (name,)).fetchall()
    indexes = conn.execute(_TABLE_INDEXES_SQL, (name,)).fetchall()
    count = _row_count(conn, name)

    if args.json:
        print_json({
            "table": name,
            "row_count": count,
            "columns": [
                {
                    "name": c[0],
                    "type": c[1],
                    "nullable": c[2] == "YES",
                    "default": c[3],
                }
                for c in columns
            ],
            "indexes": [{"name": i[0], "definition": i[1]} for i in indexes],
        })
        return

    print(f"Table     : {name}")
    print(f"Row count : {count:,}")
    print()

    if columns:
        print("Columns:")
        for c in columns:
            nullable = "NULL" if c[2] == "YES" else "NOT NULL"
            default = f"  default={c[3]}" if c[3] else ""
            print(f"  - {c[0]}  type={c[1]}  {nullable}{default}")
        print()

    if indexes:
        print(f"Indexes ({len(indexes)}):")
        for i in indexes:
            print(f"  - {i[0]}")
            print(f"    {i[1]}")
        print()


def cmd_drop(conn: Connection, args: argparse.Namespace) -> None:
    """Drop tables matching a prefix or regex pattern."""
    tables = _list_vector_tables(conn)
    matched = _match_tables(tables, args.pattern)
    if not matched:
        print(f"No vector tables matching '{args.pattern}'.")
        return

    label = "Would drop" if args.dry_run else "About to drop"
    print(f"{label} {len(matched)} table(s) matching '{args.pattern}':")
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
        conn.execute(f"DROP TABLE IF EXISTS {_quote_ident(name)} CASCADE")
        print(f"  Dropped: {name}")
    print(f"\n{len(matched)} table(s) dropped.")


def cmd_count(conn: Connection, args: argparse.Namespace) -> None:
    """Show row counts for all or pattern-matched vector tables."""
    tables = _list_vector_tables(conn)
    if args.pattern:
        tables = _match_tables(tables, args.pattern)

    if not tables:
        print("No matching tables.")
        return

    rows: list[dict[str, object]] = []
    total_rows = 0
    for name in tables:
        count = _row_count(conn, name)
        total_rows += count
        rows.append({"name": name, "row_count": count})

    if args.json:
        print_json({"total_tables": len(rows), "total_rows": total_rows, "tables": rows})
        return

    max_name = max(len(str(r["name"])) for r in rows)
    header = f"  {'Table':<{max_name}}   Rows"
    print(header)
    print(f"  {'─' * max_name}   {'─' * 12}")
    for r in rows:
        print(f"  {r['name']:<{max_name}}   {r['row_count']:>12,}")
    print(f"\n  Total: {total_rows:,} rows across {len(rows)} table(s)")


def cmd_query(conn: Connection, args: argparse.Namespace) -> None:
    """Run a WHERE clause against a table and print results."""
    table = args.table
    if not _table_exists(conn, table):
        sys.exit(f"Table '{table}' does not exist.")

    fields = "*"
    if args.output_fields:
        fields = ", ".join(_quote_ident(f.strip()) for f in args.output_fields.split(","))

    sql = f"SELECT {fields} FROM {_quote_ident(table)} WHERE {args.where} LIMIT %s"
    cur = conn.execute(sql, (args.limit,))
    col_names = [desc[0] for desc in cur.description] if cur.description else []
    results = cur.fetchall()

    if args.json:
        print_json([dict(zip(col_names, row, strict=False)) for row in results])
        return

    if not results:
        print("No results.")
        return

    print(f"Returned {len(results)} row(s):\n")
    for i, row in enumerate(results):
        print(f"--- row {i} ---")
        for col, val in zip(col_names, row, strict=False):
            display = f"[{len(val)} dims]" if isinstance(val, list) and len(val) > 8 else val
            print(f"  {col}: {display}")
        print()


def cmd_export(conn: Connection, args: argparse.Namespace) -> None:
    """Export table data to a JSONL file."""
    table = args.table
    if not _table_exists(conn, table):
        sys.exit(f"Table '{table}' does not exist.")

    where = f"WHERE {args.filter}" if args.filter else ""
    sql = f"SELECT * FROM {_quote_ident(table)} {where} LIMIT %s"

    cur = conn.execute(sql, (args.limit,))
    col_names = [desc[0] for desc in cur.description] if cur.description else []
    rows = cur.fetchall()

    dest = args.output or f"{table}.jsonl"
    with open(dest, "w") as fh:
        for row in rows:
            fh.write(json.dumps(dict(zip(col_names, row, strict=False)), default=str) + "\n")

    print(f"Exported {len(rows)} row(s) to {dest}")


def cmd_rename(conn: Connection, args: argparse.Namespace) -> None:
    """Rename a table."""
    if not _table_exists(conn, args.old):
        sys.exit(f"Table '{args.old}' does not exist.")
    if _table_exists(conn, args.new):
        sys.exit(f"Table '{args.new}' already exists.")

    conn.execute(f"ALTER TABLE {_quote_ident(args.old)} RENAME TO {_quote_ident(args.new)}")
    print(f"Renamed '{args.old}' -> '{args.new}'")


def cmd_vacuum(conn: Connection, args: argparse.Namespace) -> None:
    """Run VACUUM on a table."""
    table = args.table
    if not _table_exists(conn, table):
        sys.exit(f"Table '{table}' does not exist.")

    conn.execute(f"VACUUM {_quote_ident(table)}")
    print(f"Vacuumed '{table}'")


def cmd_health(conn: Connection, args: argparse.Namespace) -> None:
    """Show server connectivity, version, and pgvector extension status."""
    version_row = conn.execute(_HEALTH_VERSION_SQL).fetchone()
    pg_version = version_row[0] if version_row else "unknown"

    ext_row = conn.execute(_HEALTH_EXTENSION_SQL).fetchone()
    ext_version = ext_row[1] if ext_row else "not installed"

    tables = _list_vector_tables(conn)
    total_rows = sum(_row_count(conn, t) for t in tables)

    if args.json:
        print_json({
            "status": "connected",
            "pg_version": pg_version,
            "pgvector_version": ext_version,
            "total_tables": len(tables),
            "total_rows": total_rows,
        })
        return

    print("Status           : connected")
    print(f"PostgreSQL       : {pg_version}")
    print(f"pgvector ext     : {ext_version}")
    print(f"Vector tables    : {len(tables)}")
    print(f"Total rows       : {total_rows:,}")


def cmd_indexes(conn: Connection, args: argparse.Namespace) -> None:
    """List indexes for a table, with sizes."""
    table = args.table
    if not _table_exists(conn, table):
        sys.exit(f"Table '{table}' does not exist.")

    indexes = conn.execute(_INDEX_DETAIL_SQL, (table,)).fetchall()

    if args.json:
        print_json({
            "table": table,
            "indexes": [
                {"name": i[0], "definition": i[1], "size_bytes": i[2]}
                for i in indexes
            ],
        })
        return

    if not indexes:
        print(f"No indexes on '{table}'.")
        return

    print(f"Indexes for '{table}' ({len(indexes)}):\n")
    for i in indexes:
        print(f"  - {i[0]}  ({human_size(i[2])})")
        print(f"    {i[1]}")
    print()


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def _build_parser(prog: str = "pgvector") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Manage PostgreSQL/pgvector vector database instances.",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    sub = parser.add_subparsers(dest="command", required=True)

    # list
    p = sub.add_parser("list", help="List tables with vector columns")
    p.add_argument("--counts", action="store_true", help="Include row counts (slower)")

    # describe
    p = sub.add_parser("describe", help="Show table details")
    p.add_argument("name", help="Table name")

    # drop
    p = sub.add_parser("drop", help="Drop tables matching a pattern")
    p.add_argument("pattern", help="Prefix or regex for table names")
    p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    p.add_argument("--dry-run", action="store_true", help="Show what would be dropped without acting")

    # count
    p = sub.add_parser("count", help="Show row counts for tables")
    p.add_argument("pattern", nargs="?", default=None, help="Optional prefix/regex filter")

    # query
    p = sub.add_parser("query", help="Run a WHERE clause query")
    p.add_argument("table", help="Table name")
    p.add_argument("where", help="WHERE clause (e.g. \"id > 100\")")
    p.add_argument("--output-fields", help="Comma-separated column names to return")
    p.add_argument("--limit", type=int, default=10, help="Max rows to return (default: 10)")

    # export
    p = sub.add_parser("export", help="Export table data to JSONL")
    p.add_argument("table", help="Table name")
    p.add_argument("--filter", help="WHERE clause to select rows")
    p.add_argument("--limit", type=int, default=10_000, help="Max rows to export (default: 10000)")
    p.add_argument("--output", "-o", help="Output file path (default: <table>.jsonl)")

    # rename
    p = sub.add_parser("rename", help="Rename a table")
    p.add_argument("old", help="Current table name")
    p.add_argument("new", help="New table name")

    # vacuum
    p = sub.add_parser("vacuum", help="Run VACUUM on a table")
    p.add_argument("table", help="Table name")

    # health
    sub.add_parser("health", help="Check connection and show summary")

    # indexes
    p = sub.add_parser("indexes", help="List indexes for a table")
    p.add_argument("table", help="Table name")

    return parser


def main(prog: str = "pgvector") -> None:
    parser = _build_parser(prog=prog)

    from autox_tools.config._loader import add_profile_args, resolve
    add_profile_args(parser, target=True)

    args = parser.parse_args()
    cfg = resolve("pgvector", args)
    conn = connect(cfg)

    commands = {
        "list": cmd_list,
        "describe": cmd_describe,
        "drop": cmd_drop,
        "count": cmd_count,
        "query": cmd_query,
        "export": cmd_export,
        "rename": cmd_rename,
        "vacuum": cmd_vacuum,
        "health": cmd_health,
        "indexes": cmd_indexes,
    }
    try:
        commands[args.command](conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
