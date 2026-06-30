"""Unit tests for the S3 CLI tool.

All tests mock boto3 -- no real S3 endpoint required.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from autox_tools.s3 import cli

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_s3_object(key: str, size: int = 1024, age_days: int = 0) -> dict:
    """Build a fake S3 object metadata dict."""
    return {
        "Key": key,
        "Size": size,
        "LastModified": datetime.now(UTC) - timedelta(days=age_days),
    }


def _mock_list_response(objects: list[dict], *, prefixes: list[str] | None = None, truncated: bool = False) -> dict:
    """Build a fake list_objects_v2 response."""
    resp: dict = {
        "Contents": objects,
        "IsTruncated": truncated,
    }
    if prefixes:
        resp["CommonPrefixes"] = [{"Prefix": p} for p in prefixes]
    if truncated:
        resp["NextContinuationToken"] = "token-123"
    return resp


# ---------------------------------------------------------------------------
# _client.py tests
# ---------------------------------------------------------------------------

class TestClientConnect:
    def test_missing_env_vars_exits(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("autox_tools.s3._client.load_dotenv"), \
             patch("autox_tools.s3._client.find_dotenv", return_value=""), \
             pytest.raises(SystemExit, match="Missing required environment variables"):
            from autox_tools.s3._client import connect
            connect()

    def test_connect_builds_client(self):
        env = {
            "AWS_S3_ENDPOINT": "https://minio.example.com",
            "AWS_ACCESS_KEY_ID": "key",
            "AWS_SECRET_ACCESS_KEY": "secret",
        }
        with patch.dict(os.environ, env, clear=True), \
             patch("autox_tools.s3._client.load_dotenv"), \
             patch("autox_tools.s3._client.find_dotenv", return_value=""), \
             patch("autox_tools.s3._client.boto3") as mock_boto3:
            from autox_tools.s3._client import connect
            connect()
            _, kwargs = mock_boto3.client.call_args
            assert kwargs["endpoint_url"] == "https://minio.example.com"
            assert kwargs["aws_access_key_id"] == "key"
            assert kwargs["aws_secret_access_key"] == "secret"
            assert kwargs["region_name"] == "us-east-1"
            assert kwargs["verify"] is True
            assert kwargs["config"].s3["addressing_style"] == "path"

    def test_connect_tls_disabled(self):
        env = {
            "AWS_S3_ENDPOINT": "https://minio.example.com",
            "AWS_ACCESS_KEY_ID": "key",
            "AWS_SECRET_ACCESS_KEY": "secret",
            "S3_VERIFY_TLS": "false",
        }
        with patch.dict(os.environ, env, clear=True), \
             patch("autox_tools.s3._client.load_dotenv"), \
             patch("autox_tools.s3._client.find_dotenv", return_value=""), \
             patch("autox_tools.s3._client.boto3") as mock_boto3:
            from autox_tools.s3._client import connect
            connect()
            _, kwargs = mock_boto3.client.call_args
            assert kwargs["verify"] is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestHumanSize:
    @pytest.mark.parametrize("nbytes,expected", [
        (0, "0 B"),
        (512, "512 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1048576, "1.0 MB"),
        (1073741824, "1.0 GB"),
    ])
    def test_formatting(self, nbytes, expected):
        assert cli._human_size(nbytes) == expected


# ---------------------------------------------------------------------------
# cmd_list
# ---------------------------------------------------------------------------

class TestCmdList:
    def test_non_recursive_shows_prefixes_and_files(self, capsys):
        client = MagicMock()
        objects = [_make_s3_object("data/file1.json", 2048)]
        client.list_objects_v2.return_value = _mock_list_response(
            objects, prefixes=["data/subdir/"], truncated=False,
        )
        args = argparse.Namespace(
            bucket="my-bucket", prefix="data/", recursive=False, limit=1000, json=False,
        )
        cli.cmd_list(client, args)
        out = capsys.readouterr().out
        assert "subdir/" in out
        assert "file1.json" in out
        assert "2.0 KB" in out

    def test_recursive_lists_all(self, capsys):
        client = MagicMock()
        objects = [
            _make_s3_object("data/a.json", 100),
            _make_s3_object("data/sub/b.json", 200),
        ]
        client.list_objects_v2.return_value = _mock_list_response(objects)
        args = argparse.Namespace(
            bucket="my-bucket", prefix="data/", recursive=True, limit=1000, json=False,
        )
        cli.cmd_list(client, args)
        out = capsys.readouterr().out
        assert "a.json" in out
        assert "sub/b.json" in out
        assert "2 object(s)" in out

    def test_json_output(self, capsys):
        client = MagicMock()
        objects = [_make_s3_object("data/file.json", 512)]
        client.list_objects_v2.return_value = _mock_list_response(objects)
        args = argparse.Namespace(
            bucket="b", prefix="data/", recursive=True, limit=1000, json=True,
        )
        cli.cmd_list(client, args)
        import json
        result = json.loads(capsys.readouterr().out)
        assert len(result) == 1
        assert result[0]["key"] == "data/file.json"
        assert result[0]["size_bytes"] == 512

    def test_empty_bucket(self, capsys):
        client = MagicMock()
        client.list_objects_v2.return_value = _mock_list_response([])
        args = argparse.Namespace(
            bucket="b", prefix="", recursive=True, limit=1000, json=False,
        )
        cli.cmd_list(client, args)
        assert "No objects found" in capsys.readouterr().out

    def test_pagination(self):
        client = MagicMock()
        page1 = _mock_list_response([_make_s3_object("a.txt")], truncated=True)
        page2 = _mock_list_response([_make_s3_object("b.txt")], truncated=False)
        client.list_objects_v2.side_effect = [page1, page2]

        result = cli._paginate_objects(client, "bucket", "")
        assert len(result["Contents"]) == 2
        assert client.list_objects_v2.call_count == 2


# ---------------------------------------------------------------------------
# cmd_tree
# ---------------------------------------------------------------------------

class TestCmdTree:
    def test_basic_tree(self, capsys):
        client = MagicMock()
        objects = [
            _make_s3_object("exp/a/1.json", 100),
            _make_s3_object("exp/a/2.json", 200),
            _make_s3_object("exp/b/3.json", 300),
        ]
        client.list_objects_v2.return_value = _mock_list_response(objects)
        args = argparse.Namespace(bucket="bucket", prefix="exp/", depth=3)
        cli.cmd_tree(client, args)
        out = capsys.readouterr().out
        assert "bucket/exp/" in out
        assert "1.json" in out
        assert "3.json" in out

    def test_depth_truncation(self, capsys):
        client = MagicMock()
        objects = [_make_s3_object("exp/deep/nested/file.json", 100)]
        client.list_objects_v2.return_value = _mock_list_response(objects)
        args = argparse.Namespace(bucket="bucket", prefix="exp/", depth=1)
        cli.cmd_tree(client, args)
        out = capsys.readouterr().out
        assert "... (1 more)" in out

    def test_empty(self, capsys):
        client = MagicMock()
        client.list_objects_v2.return_value = _mock_list_response([])
        args = argparse.Namespace(bucket="bucket", prefix="", depth=3)
        cli.cmd_tree(client, args)
        assert "No objects found" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# cmd_download
# ---------------------------------------------------------------------------

class TestCmdDownload:
    def test_downloads_files(self, capsys):
        client = MagicMock()
        objects = [
            _make_s3_object("exp/a.json", 1024),
            _make_s3_object("exp/sub/b.json", 2048),
        ]
        client.list_objects_v2.return_value = _mock_list_response(objects)

        with tempfile.TemporaryDirectory() as tmpdir:
            args = argparse.Namespace(
                bucket="bucket", prefix="exp/", output=tmpdir, pattern=None,
            )
            cli.cmd_download(client, args)

            assert client.download_file.call_count == 2
            out = capsys.readouterr().out
            assert "Downloaded 2 file(s)" in out

    def test_glob_filter(self, capsys):
        client = MagicMock()
        objects = [
            _make_s3_object("exp/a.json", 1024),
            _make_s3_object("exp/b.csv", 2048),
            _make_s3_object("exp/c.json", 512),
        ]
        client.list_objects_v2.return_value = _mock_list_response(objects)

        with tempfile.TemporaryDirectory() as tmpdir:
            args = argparse.Namespace(
                bucket="bucket", prefix="exp/", output=tmpdir, pattern="*.json",
            )
            cli.cmd_download(client, args)
            assert client.download_file.call_count == 2

    def test_skips_directory_markers(self, capsys):
        client = MagicMock()
        objects = [
            _make_s3_object("exp/", 0),
            _make_s3_object("exp/file.txt", 100),
        ]
        client.list_objects_v2.return_value = _mock_list_response(objects)

        with tempfile.TemporaryDirectory() as tmpdir:
            args = argparse.Namespace(
                bucket="bucket", prefix="exp/", output=tmpdir, pattern=None,
            )
            cli.cmd_download(client, args)
            assert client.download_file.call_count == 1

    def test_no_matching_objects(self, capsys):
        client = MagicMock()
        client.list_objects_v2.return_value = _mock_list_response([])
        args = argparse.Namespace(
            bucket="bucket", prefix="exp/", output=".", pattern=None,
        )
        cli.cmd_download(client, args)
        assert "No matching objects" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# cmd_cleanup
# ---------------------------------------------------------------------------

class TestCmdCleanup:
    def test_age_filter(self, capsys):
        client = MagicMock()
        objects = [
            _make_s3_object("exp/old.json", 100, age_days=60),
            _make_s3_object("exp/new.json", 100, age_days=5),
        ]
        client.list_objects_v2.return_value = _mock_list_response(objects)
        args = argparse.Namespace(
            bucket="bucket", prefix="exp/", older_than=30,
            pattern=None, dry_run=True, yes=False,
        )
        cli.cmd_cleanup(client, args)
        out = capsys.readouterr().out
        assert "1 object(s) matching" in out
        assert "old.json" in out
        assert "new.json" not in out

    def test_pattern_filter(self, capsys):
        client = MagicMock()
        objects = [
            _make_s3_object("exp/a.tmp", 100),
            _make_s3_object("exp/b.json", 200),
        ]
        client.list_objects_v2.return_value = _mock_list_response(objects)
        args = argparse.Namespace(
            bucket="bucket", prefix="exp/", older_than=None,
            pattern="*.tmp", dry_run=True, yes=False,
        )
        cli.cmd_cleanup(client, args)
        out = capsys.readouterr().out
        assert "1 object(s) matching" in out
        assert "a.tmp" in out

    def test_batch_delete(self, capsys):
        client = MagicMock()
        objects = [_make_s3_object(f"exp/{i}.txt", 10) for i in range(5)]
        client.list_objects_v2.return_value = _mock_list_response(objects)
        args = argparse.Namespace(
            bucket="bucket", prefix="exp/", older_than=None,
            pattern=None, dry_run=False, yes=True,
        )
        cli.cmd_cleanup(client, args)
        client.delete_objects.assert_called_once()
        delete_arg = client.delete_objects.call_args[1]
        assert len(delete_arg["Delete"]["Objects"]) == 5

    def test_no_matches(self, capsys):
        client = MagicMock()
        client.list_objects_v2.return_value = _mock_list_response([])
        args = argparse.Namespace(
            bucket="bucket", prefix="exp/", older_than=None,
            pattern=None, dry_run=False, yes=True,
        )
        cli.cmd_cleanup(client, args)
        assert "No objects match" in capsys.readouterr().out

    def test_abort_on_no_confirm(self, capsys):
        client = MagicMock()
        objects = [_make_s3_object("exp/file.txt", 100)]
        client.list_objects_v2.return_value = _mock_list_response(objects)
        args = argparse.Namespace(
            bucket="bucket", prefix="exp/", older_than=None,
            pattern=None, dry_run=False, yes=False,
        )
        with patch("builtins.input", return_value="n"):
            cli.cmd_cleanup(client, args)
        client.delete_objects.assert_not_called()
        assert "Aborted" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# cmd_upload
# ---------------------------------------------------------------------------

class TestCmdUpload:
    def test_single_file(self, capsys):
        client = MagicMock()
        client.head_bucket.return_value = {}

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            f.write(b'{"test": true}')
            f.flush()
            try:
                args = argparse.Namespace(
                    local_path=f.name, bucket="bucket", prefix="uploads", recursive=False,
                )
                cli.cmd_upload(client, args)
                client.upload_file.assert_called_once()
                out = capsys.readouterr().out
                assert "Uploaded 1 file(s)" in out
            finally:
                os.unlink(f.name)

    def test_directory_without_recursive_flag(self):
        client = MagicMock()
        client.head_bucket.return_value = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            args = argparse.Namespace(
                local_path=tmpdir, bucket="bucket", prefix="uploads", recursive=False,
            )
            with pytest.raises(SystemExit, match="--recursive"):
                cli.cmd_upload(client, args)

    def test_directory_recursive(self, capsys):
        client = MagicMock()
        client.head_bucket.return_value = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "sub"))
            for name in ["a.txt", "sub/b.txt"]:
                with open(os.path.join(tmpdir, name), "w") as f:
                    f.write("data")

            args = argparse.Namespace(
                local_path=tmpdir, bucket="bucket", prefix="uploads", recursive=True,
            )
            cli.cmd_upload(client, args)
            assert client.upload_file.call_count == 2
            out = capsys.readouterr().out
            assert "Uploaded 2 file(s)" in out

    def test_nonexistent_path(self):
        client = MagicMock()
        client.head_bucket.return_value = {}
        args = argparse.Namespace(
            local_path="/nonexistent/path", bucket="bucket", prefix="x", recursive=False,
        )
        with pytest.raises(SystemExit, match="does not exist"):
            cli.cmd_upload(client, args)


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------

class TestParser:
    def test_list_args(self):
        parser = cli._build_parser()
        args = parser.parse_args(["--json", "list", "-b", "my-bucket", "prefix/", "--recursive", "--limit", "500"])
        assert args.json is True
        assert args.command == "list"
        assert args.bucket == "my-bucket"
        assert args.prefix == "prefix/"
        assert args.recursive is True
        assert args.limit == 500

    def test_tree_args(self):
        parser = cli._build_parser()
        args = parser.parse_args(["tree", "-b", "bucket", "pfx/", "--depth", "5"])
        assert args.command == "tree"
        assert args.prefix == "pfx/"
        assert args.depth == 5

    def test_download_args(self):
        parser = cli._build_parser()
        args = parser.parse_args(["download", "-b", "bucket", "pfx/", "-o", "/tmp/out", "--pattern", "*.json"])
        assert args.command == "download"
        assert args.bucket == "bucket"
        assert args.prefix == "pfx/"
        assert args.output == "/tmp/out"
        assert args.pattern == "*.json"

    def test_cleanup_args(self):
        parser = cli._build_parser()
        args = parser.parse_args(["cleanup", "-b", "bucket", "pfx/", "--older-than", "30", "--dry-run", "--yes"])
        assert args.command == "cleanup"
        assert args.bucket == "bucket"
        assert args.prefix == "pfx/"
        assert args.older_than == 30
        assert args.dry_run is True
        assert args.yes is True

    def test_upload_args(self):
        parser = cli._build_parser()
        args = parser.parse_args(["upload", "./dir", "pfx/", "-b", "bucket", "--recursive"])
        assert args.command == "upload"
        assert args.local_path == "./dir"
        assert args.bucket == "bucket"
        assert args.prefix == "pfx/"
        assert args.recursive is True

    def test_list_no_bucket(self):
        parser = cli._build_parser()
        args = parser.parse_args(["list"])
        assert args.command == "list"
        assert args.bucket is None
        assert args.prefix == ""


class TestResolveBucket:
    def test_cli_arg_wins(self):
        from autox_tools.config._models import S3Config
        args = argparse.Namespace(bucket="cli-bucket")
        cfg = S3Config(endpoint="x", access_key_id="x", secret_access_key="x", bucket="cfg-bucket")
        assert cli._resolve_bucket(args, cfg) == "cli-bucket"

    def test_falls_back_to_config(self):
        from autox_tools.config._models import S3Config
        args = argparse.Namespace(bucket=None)
        cfg = S3Config(endpoint="x", access_key_id="x", secret_access_key="x", bucket="cfg-bucket")
        assert cli._resolve_bucket(args, cfg) == "cfg-bucket"

    def test_exits_when_no_bucket(self):
        from autox_tools.config._models import S3Config
        args = argparse.Namespace(bucket=None)
        cfg = S3Config(endpoint="x", access_key_id="x", secret_access_key="x")
        with pytest.raises(SystemExit):
            cli._resolve_bucket(args, cfg)

    def test_exits_when_no_config(self):
        args = argparse.Namespace(bucket=None)
        with pytest.raises(SystemExit):
            cli._resolve_bucket(args, None)
