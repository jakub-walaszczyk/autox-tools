"""Unit tests for the pgvector CLI tool.

All tests mock psycopg -- no real PostgreSQL server required.
"""

from __future__ import annotations

import argparse
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from autox_tools._output import human_size
from autox_tools.vs.pgvector import cli

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _mock_conn() -> MagicMock:
    """Build a mock psycopg Connection."""
    return MagicMock()


def _args(**kwargs: object) -> argparse.Namespace:
    defaults: dict[str, object] = {
        "json": False,
        "command": "health",
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _mock_cursor(rows: list[tuple], columns: list[str] | None = None) -> MagicMock:
    """Build a mock cursor with fetchall/fetchone results."""
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.fetchone.return_value = rows[0] if rows else None
    if columns:
        cur.description = [(c,) for c in columns]
    else:
        cur.description = None
    return cur


# ---------------------------------------------------------------------------
# _client.py tests
# ---------------------------------------------------------------------------

class TestClientConnect:
    def test_missing_env_vars_exits(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("autox_tools.vs.pgvector._client.load_dotenv"),
            patch("autox_tools.vs.pgvector._client.find_dotenv", return_value=""),
            pytest.raises(SystemExit, match="Missing required environment variables"),
        ):
            from autox_tools.vs.pgvector._client import connect
            connect()

    def test_connect_builds_connection(self):
        env = {
            "PGVECTOR_HOST": "localhost",
            "PGVECTOR_PORT": "5432",
            "PGVECTOR_DATABASE": "testdb",
            "PGVECTOR_USER": "user",
            "PGVECTOR_PASSWORD": "pass",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch("autox_tools.vs.pgvector._client.load_dotenv"),
            patch("autox_tools.vs.pgvector._client.find_dotenv", return_value=""),
            patch("autox_tools.vs.pgvector._client.Connection") as mock_cls,
        ):
            from autox_tools.vs.pgvector._client import connect
            connect()
            mock_cls.connect.assert_called_once_with(
                host="localhost",
                port=5432,
                dbname="testdb",
                user="user",
                password="pass",
                sslmode="prefer",
                autocommit=True,
            )

    def test_connect_without_optional_vars(self):
        env = {
            "PGVECTOR_HOST": "db.example.com",
            "PGVECTOR_PORT": "5433",
            "PGVECTOR_DATABASE": "vectors",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch("autox_tools.vs.pgvector._client.load_dotenv"),
            patch("autox_tools.vs.pgvector._client.find_dotenv", return_value=""),
            patch("autox_tools.vs.pgvector._client.Connection") as mock_cls,
        ):
            from autox_tools.vs.pgvector._client import connect
            connect()
            _, kwargs = mock_cls.connect.call_args
            assert kwargs["user"] == ""
            assert kwargs["password"] == ""
            assert kwargs["sslmode"] == "prefer"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_quote_ident_simple(self):
        assert cli._quote_ident("my_table") == "my_table"

    def test_quote_ident_needs_quoting(self):
        assert cli._quote_ident("my-table") == '"my-table"'

    def test_quote_ident_with_double_quotes(self):
        assert cli._quote_ident('my"table') == '"my""table"'

    def test_match_tables_prefix(self):
        tables = ["embeddings_v1", "embeddings_v2", "other"]
        assert cli._match_tables(tables, "embeddings") == ["embeddings_v1", "embeddings_v2"]

    def test_match_tables_regex(self):
        tables = ["embed_a", "embed_b", "logs"]
        assert cli._match_tables(tables, "embed_.*") == ["embed_a", "embed_b"]

    def test_human_size(self):
        assert human_size(0) == "0 B"
        assert human_size(1024) == "1.0 KB"
        assert human_size(1_048_576) == "1.0 MB"
        assert human_size(None) == "—"


# ---------------------------------------------------------------------------
# cmd_list tests
# ---------------------------------------------------------------------------

class TestCmdList:
    def test_list_tables(self, capsys: pytest.CaptureFixture[str]):
        conn = _mock_conn()
        conn.execute.return_value = _mock_cursor([("docs",), ("embeddings",)])

        cli.cmd_list(conn, _args(command="list", counts=False))

        out = capsys.readouterr().out
        assert "docs" in out
        assert "embeddings" in out
        assert "2" in out

    def test_list_with_counts(self, capsys: pytest.CaptureFixture[str]):
        conn = _mock_conn()
        tables_cursor = _mock_cursor([("docs",)])
        count_cursor = _mock_cursor([(42,)])
        conn.execute.side_effect = [tables_cursor, count_cursor]

        cli.cmd_list(conn, _args(command="list", counts=True))

        out = capsys.readouterr().out
        assert "docs" in out
        assert "42" in out

    def test_list_json(self, capsys: pytest.CaptureFixture[str]):
        conn = _mock_conn()
        conn.execute.return_value = _mock_cursor([("docs",), ("embeddings",)])

        cli.cmd_list(conn, _args(command="list", counts=False, json=True))

        data = json.loads(capsys.readouterr().out)
        assert data["total"] == 2
        assert data["tables"][0]["name"] == "docs"

    def test_list_empty(self, capsys: pytest.CaptureFixture[str]):
        conn = _mock_conn()
        conn.execute.return_value = _mock_cursor([])

        cli.cmd_list(conn, _args(command="list", counts=False))

        out = capsys.readouterr().out
        assert "0" in out


# ---------------------------------------------------------------------------
# cmd_describe tests
# ---------------------------------------------------------------------------

class TestCmdDescribe:
    def test_describe_table(self, capsys: pytest.CaptureFixture[str]):
        conn = _mock_conn()
        exists_cur = _mock_cursor([(1,)])
        columns_cur = _mock_cursor([
            ("id", "int4", "NO", None, None, None),
            ("embedding", "vector", "YES", None, None, None),
        ])
        indexes_cur = _mock_cursor([("idx_embed", "CREATE INDEX idx_embed ON docs USING hnsw ...")])
        count_cur = _mock_cursor([(100,)])
        conn.execute.side_effect = [exists_cur, columns_cur, indexes_cur, count_cur]

        cli.cmd_describe(conn, _args(command="describe", name="docs"))

        out = capsys.readouterr().out
        assert "docs" in out
        assert "id" in out
        assert "embedding" in out
        assert "vector" in out
        assert "100" in out
        assert "idx_embed" in out

    def test_describe_nonexistent(self):
        conn = _mock_conn()
        conn.execute.return_value = _mock_cursor([])
        conn.execute.return_value.fetchone.return_value = None

        with pytest.raises(SystemExit, match="does not exist"):
            cli.cmd_describe(conn, _args(command="describe", name="nonexistent"))

    def test_describe_json(self, capsys: pytest.CaptureFixture[str]):
        conn = _mock_conn()
        exists_cur = _mock_cursor([(1,)])
        columns_cur = _mock_cursor([("id", "int4", "NO", None, None, None)])
        indexes_cur = _mock_cursor([])
        count_cur = _mock_cursor([(50,)])
        conn.execute.side_effect = [exists_cur, columns_cur, indexes_cur, count_cur]

        cli.cmd_describe(conn, _args(command="describe", name="docs", json=True))

        data = json.loads(capsys.readouterr().out)
        assert data["table"] == "docs"
        assert data["row_count"] == 50
        assert data["columns"][0]["name"] == "id"


# ---------------------------------------------------------------------------
# cmd_drop tests
# ---------------------------------------------------------------------------

class TestCmdDrop:
    def test_drop_dry_run(self, capsys: pytest.CaptureFixture[str]):
        conn = _mock_conn()
        conn.execute.return_value = _mock_cursor([("test_a",), ("test_b",), ("prod",)])

        cli.cmd_drop(conn, _args(command="drop", pattern="test", yes=False, dry_run=True))

        out = capsys.readouterr().out
        assert "Would drop" in out
        assert "test_a" in out
        assert "test_b" in out

    def test_drop_no_match(self, capsys: pytest.CaptureFixture[str]):
        conn = _mock_conn()
        conn.execute.return_value = _mock_cursor([("docs",)])

        cli.cmd_drop(conn, _args(command="drop", pattern="nonexistent", yes=False, dry_run=False))

        out = capsys.readouterr().out
        assert "No vector tables matching" in out

    def test_drop_confirmed(self, capsys: pytest.CaptureFixture[str]):
        conn = _mock_conn()
        tables_cur = _mock_cursor([("test_a",)])
        conn.execute.side_effect = [tables_cur, None]

        cli.cmd_drop(conn, _args(command="drop", pattern="test_a", yes=True, dry_run=False))

        out = capsys.readouterr().out
        assert "Dropped" in out
        assert "1 table(s) dropped" in out


# ---------------------------------------------------------------------------
# cmd_count tests
# ---------------------------------------------------------------------------

class TestCmdCount:
    def test_count_all(self, capsys: pytest.CaptureFixture[str]):
        conn = _mock_conn()
        tables_cur = _mock_cursor([("docs",), ("embeddings",)])
        count_cur_1 = _mock_cursor([(100,)])
        count_cur_2 = _mock_cursor([(200,)])
        conn.execute.side_effect = [tables_cur, count_cur_1, count_cur_2]

        cli.cmd_count(conn, _args(command="count", pattern=None))

        out = capsys.readouterr().out
        assert "docs" in out
        assert "embeddings" in out
        assert "300" in out

    def test_count_json(self, capsys: pytest.CaptureFixture[str]):
        conn = _mock_conn()
        tables_cur = _mock_cursor([("docs",)])
        count_cur = _mock_cursor([(42,)])
        conn.execute.side_effect = [tables_cur, count_cur]

        cli.cmd_count(conn, _args(command="count", pattern=None, json=True))

        data = json.loads(capsys.readouterr().out)
        assert data["total_tables"] == 1
        assert data["total_rows"] == 42

    def test_count_no_match(self, capsys: pytest.CaptureFixture[str]):
        conn = _mock_conn()
        conn.execute.return_value = _mock_cursor([])

        cli.cmd_count(conn, _args(command="count", pattern="nonexistent"))

        out = capsys.readouterr().out
        assert "No matching" in out


# ---------------------------------------------------------------------------
# cmd_query tests
# ---------------------------------------------------------------------------

class TestCmdQuery:
    def test_query_results(self, capsys: pytest.CaptureFixture[str]):
        conn = _mock_conn()
        exists_cur = _mock_cursor([(1,)])
        results_cur = _mock_cursor([(1, "hello")], columns=["id", "text"])
        conn.execute.side_effect = [exists_cur, results_cur]

        cli.cmd_query(conn, _args(
            command="query", table="docs", where="id = 1",
            output_fields=None, limit=10,
        ))

        out = capsys.readouterr().out
        assert "1 row(s)" in out
        assert "hello" in out

    def test_query_json(self, capsys: pytest.CaptureFixture[str]):
        conn = _mock_conn()
        exists_cur = _mock_cursor([(1,)])
        results_cur = _mock_cursor([(1, "test")], columns=["id", "text"])
        conn.execute.side_effect = [exists_cur, results_cur]

        cli.cmd_query(conn, _args(
            command="query", table="docs", where="id = 1",
            output_fields=None, limit=10, json=True,
        ))

        data = json.loads(capsys.readouterr().out)
        assert len(data) == 1
        assert data[0]["id"] == 1

    def test_query_no_results(self, capsys: pytest.CaptureFixture[str]):
        conn = _mock_conn()
        exists_cur = _mock_cursor([(1,)])
        results_cur = _mock_cursor([], columns=["id"])
        conn.execute.side_effect = [exists_cur, results_cur]

        cli.cmd_query(conn, _args(
            command="query", table="docs", where="id = -1",
            output_fields=None, limit=10,
        ))

        out = capsys.readouterr().out
        assert "No results" in out

    def test_query_nonexistent_table(self):
        conn = _mock_conn()
        conn.execute.return_value = _mock_cursor([])
        conn.execute.return_value.fetchone.return_value = None

        with pytest.raises(SystemExit, match="does not exist"):
            cli.cmd_query(conn, _args(
                command="query", table="nonexistent", where="1=1",
                output_fields=None, limit=10,
            ))


# ---------------------------------------------------------------------------
# cmd_export tests
# ---------------------------------------------------------------------------

class TestCmdExport:
    def test_export_to_file(self, capsys: pytest.CaptureFixture[str], tmp_path):
        conn = _mock_conn()
        exists_cur = _mock_cursor([(1,)])
        data_cur = _mock_cursor([(1, "hello"), (2, "world")], columns=["id", "text"])
        conn.execute.side_effect = [exists_cur, data_cur]

        out_file = str(tmp_path / "out.jsonl")
        cli.cmd_export(conn, _args(
            command="export", table="docs", filter=None,
            limit=10_000, output=out_file,
        ))

        out = capsys.readouterr().out
        assert "2 row(s)" in out

        with open(out_file) as f:
            lines = f.readlines()
        assert len(lines) == 2
        row = json.loads(lines[0])
        assert row["id"] == 1

    def test_export_nonexistent_table(self):
        conn = _mock_conn()
        conn.execute.return_value = _mock_cursor([])
        conn.execute.return_value.fetchone.return_value = None

        with pytest.raises(SystemExit, match="does not exist"):
            cli.cmd_export(conn, _args(
                command="export", table="nonexistent", filter=None,
                limit=10_000, output=None,
            ))


# ---------------------------------------------------------------------------
# cmd_rename tests
# ---------------------------------------------------------------------------

class TestCmdRename:
    def test_rename_success(self, capsys: pytest.CaptureFixture[str]):
        conn = _mock_conn()
        exists_old = _mock_cursor([(1,)])
        exists_new = _mock_cursor([])
        exists_new.fetchone.return_value = None
        conn.execute.side_effect = [exists_old, exists_new, None]

        cli.cmd_rename(conn, _args(command="rename", old="old_table", new="new_table"))

        out = capsys.readouterr().out
        assert "Renamed" in out

    def test_rename_source_missing(self):
        conn = _mock_conn()
        conn.execute.return_value = _mock_cursor([])
        conn.execute.return_value.fetchone.return_value = None

        with pytest.raises(SystemExit, match="does not exist"):
            cli.cmd_rename(conn, _args(command="rename", old="missing", new="target"))

    def test_rename_target_exists(self):
        conn = _mock_conn()
        exists_old = _mock_cursor([(1,)])
        exists_new = _mock_cursor([(1,)])
        conn.execute.side_effect = [exists_old, exists_new]

        with pytest.raises(SystemExit, match="already exists"):
            cli.cmd_rename(conn, _args(command="rename", old="source", new="existing"))


# ---------------------------------------------------------------------------
# cmd_vacuum tests
# ---------------------------------------------------------------------------

class TestCmdVacuum:
    def test_vacuum_success(self, capsys: pytest.CaptureFixture[str]):
        conn = _mock_conn()
        exists_cur = _mock_cursor([(1,)])
        conn.execute.side_effect = [exists_cur, None]

        cli.cmd_vacuum(conn, _args(command="vacuum", table="docs"))

        out = capsys.readouterr().out
        assert "Vacuumed" in out

    def test_vacuum_nonexistent(self):
        conn = _mock_conn()
        conn.execute.return_value = _mock_cursor([])
        conn.execute.return_value.fetchone.return_value = None

        with pytest.raises(SystemExit, match="does not exist"):
            cli.cmd_vacuum(conn, _args(command="vacuum", table="nonexistent"))


# ---------------------------------------------------------------------------
# cmd_health tests
# ---------------------------------------------------------------------------

class TestCmdHealth:
    def test_health_connected(self, capsys: pytest.CaptureFixture[str]):
        conn = _mock_conn()
        version_cur = _mock_cursor([("PostgreSQL 16.2",)])
        ext_cur = _mock_cursor([("vector", "0.7.0")])
        tables_cur = _mock_cursor([("docs",)])
        count_cur = _mock_cursor([(100,)])
        conn.execute.side_effect = [version_cur, ext_cur, tables_cur, count_cur]

        cli.cmd_health(conn, _args(command="health"))

        out = capsys.readouterr().out
        assert "connected" in out
        assert "PostgreSQL 16.2" in out
        assert "0.7.0" in out

    def test_health_no_extension(self, capsys: pytest.CaptureFixture[str]):
        conn = _mock_conn()
        version_cur = _mock_cursor([("PostgreSQL 16.2",)])
        ext_cur = _mock_cursor([])
        ext_cur.fetchone.return_value = None
        tables_cur = _mock_cursor([])
        conn.execute.side_effect = [version_cur, ext_cur, tables_cur]

        cli.cmd_health(conn, _args(command="health"))

        out = capsys.readouterr().out
        assert "not installed" in out

    def test_health_json(self, capsys: pytest.CaptureFixture[str]):
        conn = _mock_conn()
        version_cur = _mock_cursor([("PostgreSQL 16.2",)])
        ext_cur = _mock_cursor([("vector", "0.7.0")])
        tables_cur = _mock_cursor([("docs",)])
        count_cur = _mock_cursor([(50,)])
        conn.execute.side_effect = [version_cur, ext_cur, tables_cur, count_cur]

        cli.cmd_health(conn, _args(command="health", json=True))

        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "connected"
        assert data["pgvector_version"] == "0.7.0"
        assert data["total_rows"] == 50


# ---------------------------------------------------------------------------
# cmd_indexes tests
# ---------------------------------------------------------------------------

class TestCmdIndexes:
    def test_indexes_list(self, capsys: pytest.CaptureFixture[str]):
        conn = _mock_conn()
        exists_cur = _mock_cursor([(1,)])
        idx_cur = _mock_cursor([
            ("idx_embed_hnsw", "CREATE INDEX idx_embed_hnsw ON docs USING hnsw ...", 1_048_576),
        ])
        conn.execute.side_effect = [exists_cur, idx_cur]

        cli.cmd_indexes(conn, _args(command="indexes", table="docs"))

        out = capsys.readouterr().out
        assert "idx_embed_hnsw" in out
        assert "1.0 MB" in out

    def test_indexes_empty(self, capsys: pytest.CaptureFixture[str]):
        conn = _mock_conn()
        exists_cur = _mock_cursor([(1,)])
        idx_cur = _mock_cursor([])
        conn.execute.side_effect = [exists_cur, idx_cur]

        cli.cmd_indexes(conn, _args(command="indexes", table="docs"))

        out = capsys.readouterr().out
        assert "No indexes" in out

    def test_indexes_json(self, capsys: pytest.CaptureFixture[str]):
        conn = _mock_conn()
        exists_cur = _mock_cursor([(1,)])
        idx_cur = _mock_cursor([("idx_1", "CREATE INDEX ...", 512)])
        conn.execute.side_effect = [exists_cur, idx_cur]

        cli.cmd_indexes(conn, _args(command="indexes", table="docs", json=True))

        data = json.loads(capsys.readouterr().out)
        assert data["table"] == "docs"
        assert len(data["indexes"]) == 1
        assert data["indexes"][0]["size_bytes"] == 512

    def test_indexes_nonexistent_table(self):
        conn = _mock_conn()
        conn.execute.return_value = _mock_cursor([])
        conn.execute.return_value.fetchone.return_value = None

        with pytest.raises(SystemExit, match="does not exist"):
            cli.cmd_indexes(conn, _args(command="indexes", table="nonexistent"))


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

class TestParser:
    def test_list_default(self):
        args = cli._build_parser().parse_args(["list"])
        assert args.command == "list"
        assert args.counts is False

    def test_list_counts(self):
        args = cli._build_parser().parse_args(["list", "--counts"])
        assert args.counts is True

    def test_describe_args(self):
        args = cli._build_parser().parse_args(["describe", "my_table"])
        assert args.command == "describe"
        assert args.name == "my_table"

    def test_drop_args(self):
        args = cli._build_parser().parse_args(["drop", "test_.*", "--yes", "--dry-run"])
        assert args.pattern == "test_.*"
        assert args.yes is True
        assert args.dry_run is True

    def test_query_args(self):
        args = cli._build_parser().parse_args(["query", "docs", "id > 5", "--limit", "50"])
        assert args.table == "docs"
        assert args.where == "id > 5"
        assert args.limit == 50

    def test_export_args(self):
        args = cli._build_parser().parse_args(["export", "docs", "--filter", "id > 0", "-o", "out.jsonl"])
        assert args.table == "docs"
        assert args.filter == "id > 0"
        assert args.output == "out.jsonl"

    def test_vacuum_args(self):
        args = cli._build_parser().parse_args(["vacuum", "docs"])
        assert args.table == "docs"

    def test_health_no_args(self):
        args = cli._build_parser().parse_args(["health"])
        assert args.command == "health"

    def test_indexes_args(self):
        args = cli._build_parser().parse_args(["indexes", "docs"])
        assert args.table == "docs"

    def test_json_flag(self):
        args = cli._build_parser().parse_args(["--json", "list"])
        assert args.json is True

    def test_rename_args(self):
        args = cli._build_parser().parse_args(["rename", "old_t", "new_t"])
        assert args.old == "old_t"
        assert args.new == "new_t"

    def test_count_optional_pattern(self):
        args = cli._build_parser().parse_args(["count"])
        assert args.pattern is None

    def test_count_with_pattern(self):
        args = cli._build_parser().parse_args(["count", "embed_.*"])
        assert args.pattern == "embed_.*"

    def test_prog_parameter(self):
        parser = cli._build_parser(prog="vs pgvector")
        assert parser.prog == "vs pgvector"
