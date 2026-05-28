"""Unit tests for the automl CLI tool."""

from __future__ import annotations

from autox_tools.automl import cli


class TestParser:
    def test_info_args(self):
        parser = cli._build_parser()
        args = parser.parse_args(["info"])
        assert args.command == "info"

    def test_json_flag(self):
        parser = cli._build_parser()
        args = parser.parse_args(["--json", "info"])
        assert args.json is True


class TestCmdInfo:
    def test_human_output(self, capsys):
        parser = cli._build_parser()
        args = parser.parse_args(["info"])
        cli.cmd_info(args)

        out = capsys.readouterr().out
        assert "AutoML CLI" in out
        assert "available" in out

    def test_json_output(self, capsys):
        import json

        parser = cli._build_parser()
        args = parser.parse_args(["--json", "info"])
        cli.cmd_info(args)

        result = json.loads(capsys.readouterr().out)
        assert result["status"] == "available"
