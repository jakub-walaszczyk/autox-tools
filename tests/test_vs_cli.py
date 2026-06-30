"""Unit tests for the unified vs CLI dispatcher."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from autox_tools.vs import cli


class TestDispatcher:
    def test_unknown_backend_exits(self):
        with (
            patch.object(sys, "argv", ["vs", "nonexistent"]),
            pytest.raises(SystemExit, match="Unknown backend"),
        ):
            cli.main()

    def test_no_args_exits(self):
        with (
            patch.object(sys, "argv", ["vs"]),
            pytest.raises(SystemExit),
        ):
            cli.main()

    def test_help_flag(self, capsys: pytest.CaptureFixture[str]):
        with patch.object(sys, "argv", ["vs", "--help"]):
            cli.main()
        out = capsys.readouterr().out
        assert "milvus" in out
        assert "pgvector" in out

    def test_routes_to_milvus(self):
        with (
            patch.object(sys, "argv", ["vs", "milvus", "--help"]),
            patch("autox_tools.vs.milvus.cli.main") as mock_main,
        ):
            mock_main.side_effect = SystemExit(0)
            with pytest.raises(SystemExit):
                cli.main()
            mock_main.assert_called_once_with(prog="vs milvus")

    def test_routes_to_pgvector(self):
        with (
            patch.object(sys, "argv", ["vs", "pgvector", "--help"]),
            patch("autox_tools.vs.pgvector.cli.main") as mock_main,
        ):
            mock_main.side_effect = SystemExit(0)
            with pytest.raises(SystemExit):
                cli.main()
            mock_main.assert_called_once_with(prog="vs pgvector")

    def test_argv_rewritten_for_backend(self):
        captured_argv = []

        def capture_main(prog="pgvector"):
            captured_argv.extend(sys.argv)

        with (
            patch.object(sys, "argv", ["vs", "pgvector", "health", "--json"]),
            patch("autox_tools.vs.pgvector.cli.main", side_effect=capture_main),
        ):
            cli.main()
        assert captured_argv == ["vs pgvector", "health", "--json"]
