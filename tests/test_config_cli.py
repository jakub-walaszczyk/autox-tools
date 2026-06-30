"""Unit tests for the config CLI tool."""

from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from autox_tools.config import cli

_SAMPLE_YAML = textwrap.dedent("""\
    defaults:
      profile: dev

    profiles:
      dev:
        s3: minio-dev
        rhoai: dev-cluster

    s3:
      minio-dev:
        endpoint: https://minio.dev.example.com
        access_key_id: dev-key
        secret_access_key: dev-secret-key-1234567890
        region: us-east-1
        verify_tls: false

    rhoai:
      dev-cluster:
        kfp_url: https://kfp.dev.example.com/
        token: sha256~my-long-secret-token
        project_name: dev-ns
        verify_ssl: false
""")


def _write_config(tmp_dir: str, content: str = _SAMPLE_YAML) -> Path:
    path = Path(tmp_dir) / ".autox.yaml"
    path.write_text(content)
    return path


# ── cmd_list ────────────────────────────────────────────────────────────────


class TestCmdList:
    def test_lists_profiles(self, capsys):
        with tempfile.TemporaryDirectory() as d:
            path = _write_config(d)
            with patch("autox_tools.config.cli.load_config") as mock_load:
                from autox_tools.config._loader import load_config as real_load
                mock_load.return_value = real_load(path)
                import argparse
                cli.cmd_list(argparse.Namespace())
                output = capsys.readouterr().out
                assert "dev" in output
                assert "(default)" in output

    def test_exits_when_no_config(self):
        with patch("autox_tools.config.cli.load_config", return_value=None), \
             pytest.raises(SystemExit):
            import argparse
            cli.cmd_list(argparse.Namespace())


# ── cmd_show ────────────────────────────────────────────────────────────────


class TestCmdShow:
    def test_shows_profile(self, capsys):
        with tempfile.TemporaryDirectory() as d:
            path = _write_config(d)
            with patch("autox_tools.config.cli.load_config") as mock_load:
                from autox_tools.config._loader import load_config as real_load
                mock_load.return_value = real_load(path)
                import argparse
                cli.cmd_show(argparse.Namespace(profile_name="dev"))
                output = capsys.readouterr().out
                assert "Profile: dev" in output
                assert "minio-dev" in output

    def test_masks_secrets(self, capsys):
        with tempfile.TemporaryDirectory() as d:
            path = _write_config(d)
            with patch("autox_tools.config.cli.load_config") as mock_load:
                from autox_tools.config._loader import load_config as real_load
                mock_load.return_value = real_load(path)
                import argparse
                cli.cmd_show(argparse.Namespace(profile_name="dev"))
                output = capsys.readouterr().out
                assert "dev-secret-key-1234567890" not in output
                assert "***" in output

    def test_unknown_profile_exits(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_config(d)
            with patch("autox_tools.config.cli.load_config") as mock_load:
                from autox_tools.config._loader import load_config as real_load
                mock_load.return_value = real_load(path)
                import argparse
                with pytest.raises(SystemExit, match="Unknown profile"):
                    cli.cmd_show(argparse.Namespace(profile_name="nonexistent"))


# ── cmd_validate ────────────────────────────────────────────────────────────


class TestCmdValidate:
    def test_valid_config(self, capsys):
        with tempfile.TemporaryDirectory() as d:
            path = _write_config(d)
            with patch("autox_tools.config.cli.find_config", return_value=path):
                import argparse
                cli.cmd_validate(argparse.Namespace())
                output = capsys.readouterr().out
                assert "Config valid" in output

    def test_broken_reference_fails(self):
        broken = textwrap.dedent("""\
            profiles:
              bad:
                s3: nonexistent
            s3: {}
        """)
        with tempfile.TemporaryDirectory() as d:
            path = _write_config(d, broken)
            with patch("autox_tools.config.cli.find_config", return_value=path), \
                 pytest.raises(SystemExit):
                import argparse
                cli.cmd_validate(argparse.Namespace())

    def test_bad_default_profile_fails(self):
        broken = textwrap.dedent("""\
            defaults:
              profile: ghost
            profiles: {}
        """)
        with tempfile.TemporaryDirectory() as d:
            path = _write_config(d, broken)
            with patch("autox_tools.config.cli.find_config", return_value=path), \
                 pytest.raises(SystemExit):
                import argparse
                cli.cmd_validate(argparse.Namespace())


# ── cmd_init ────────────────────────────────────────────────────────────────


class TestCmdInit:
    def test_creates_file(self, monkeypatch):
        with tempfile.TemporaryDirectory() as d:
            monkeypatch.chdir(d)
            import argparse
            cli.cmd_init(argparse.Namespace(force=False))
            assert (Path(d) / ".autox.yaml").exists()

    def test_refuses_overwrite_without_force(self, monkeypatch):
        with tempfile.TemporaryDirectory() as d:
            monkeypatch.chdir(d)
            (Path(d) / ".autox.yaml").write_text("existing")
            import argparse
            with pytest.raises(SystemExit):
                cli.cmd_init(argparse.Namespace(force=False))


# ── _mask ───────────────────────────────────────────────────────────────────


class TestMask:
    def test_short_value(self):
        assert cli._mask("abc") == "***"

    def test_long_value(self):
        result = cli._mask("abcdefghij")
        assert result.startswith("abc")
        assert result.endswith("hij")
        assert "***" in result

    def test_empty_value(self):
        assert cli._mask("") == "***"
