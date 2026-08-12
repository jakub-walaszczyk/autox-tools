"""Unit tests for the MaaS CLI tool.

All tests mock the OpenAI client -- no real MaaS endpoint required.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from openai import APIConnectionError, APIStatusError

from autox_tools.maas import _client, cli
from autox_tools.maas._client import MaasSettings

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# base_url intentionally omits the "/v1" suffix to exercise its auto-append.
_SETTINGS = MaasSettings(base_url="https://maas.example.com", api_key="test-key", verify_tls=True)


def _make_model(
    model_id: str = "publishers/ns/models/qwen3-8b",
    created: int = 1_700_000_000,
    **extra: object,
) -> SimpleNamespace:
    """Build a fake OpenAI Model object as returned by MaaS listing.

    MaaS ids are typically fully-qualified; the short name is derived from the
    final path segment, so the default id yields the name ``qwen3-8b``.
    """
    return SimpleNamespace(id=model_id, created=created, **extra)


def _completion(content: str = "4") -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _embedding(dimensions: int = 768) -> SimpleNamespace:
    return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1] * dimensions)])


def _list_response(models: list) -> SimpleNamespace:
    return SimpleNamespace(data=models)


def _args(**kwargs: object) -> argparse.Namespace:
    defaults: dict[str, object] = {
        "json": False,
        "command": "models",
        "metadata": False,
        "type": "auto",
        "prompt": "What is 2+2?",
        "input": "Sample text.",
        "model_id": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# _client.py tests
# ---------------------------------------------------------------------------

class TestApiEndpoint:
    def test_appends_v1_suffix(self):
        assert _client.api_endpoint("https://maas.example.com") == "https://maas.example.com/v1"

    def test_strips_trailing_slash(self):
        assert _client.api_endpoint("https://maas.example.com/") == "https://maas.example.com/v1"

    def test_idempotent(self):
        url = "https://maas.example.com/v1"
        assert _client.api_endpoint(url) == url


class TestResolveSettings:
    def test_from_config(self):
        cfg = SimpleNamespace(base_url="https://c.example.com/v1", api_key="ck", verify_tls=False)
        settings = _client.resolve_settings(cfg)
        assert settings.base_url == "https://c.example.com/v1"
        assert settings.api_key == "ck"
        assert settings.verify_tls is False

    def test_from_env(self):
        env = {"MAAS_BASE_URL": "https://e.example.com/v1", "MAAS_API_KEY": "ek"}
        with (
            patch.dict(os.environ, env, clear=True),
            patch("autox_tools.maas._client.load_dotenv"),
            patch("autox_tools.maas._client.find_dotenv", return_value=""),
        ):
            settings = _client.resolve_settings(None)
        assert settings.base_url == "https://e.example.com/v1"
        assert settings.api_key == "ek"
        assert settings.verify_tls is True

    def test_env_verify_tls_false(self):
        env = {"MAAS_BASE_URL": "https://e.example.com/v1", "MAAS_VERIFY_TLS": "false"}
        with (
            patch.dict(os.environ, env, clear=True),
            patch("autox_tools.maas._client.load_dotenv"),
            patch("autox_tools.maas._client.find_dotenv", return_value=""),
        ):
            settings = _client.resolve_settings(None)
        assert settings.verify_tls is False

    def test_missing_base_url_exits(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("autox_tools.maas._client.load_dotenv"),
            patch("autox_tools.maas._client.find_dotenv", return_value=""),
            pytest.raises(SystemExit, match="MAAS_BASE_URL"),
        ):
            _client.resolve_settings(None)


class TestClientBuild:
    def test_connect_targets_v1_endpoint(self):
        with patch("autox_tools.maas._client.OpenAI") as mock_openai:
            _client.connect(_SETTINGS)
            _, kwargs = mock_openai.call_args
            assert kwargs["base_url"] == "https://maas.example.com/v1"
            assert kwargs["api_key"] == "test-key"
            assert kwargs["http_client"] is None

    def test_empty_api_key_uses_placeholder(self):
        settings = MaasSettings(base_url="https://maas.example.com/v1", api_key="")
        with patch("autox_tools.maas._client.OpenAI") as mock_openai:
            _client.connect(settings)
            _, kwargs = mock_openai.call_args
            assert kwargs["api_key"] == "EMPTY"

    def test_verify_false_builds_insecure_http_client(self):
        settings = MaasSettings(base_url="https://maas.example.com/v1", api_key="k", verify_tls=False)
        with (
            patch("autox_tools.maas._client.OpenAI") as mock_openai,
            patch("autox_tools.maas._client.httpx.Client") as mock_httpx,
        ):
            _client.connect(settings)
            mock_httpx.assert_called_once_with(verify=False)
            _, kwargs = mock_openai.call_args
            assert kwargs["base_url"] == "https://maas.example.com/v1"
            assert kwargs["http_client"] is mock_httpx.return_value


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_model_id(self):
        assert cli._model_id(_make_model("publishers/ns/models/qwen3-8b")) == "publishers/ns/models/qwen3-8b"

    def test_model_name_is_final_segment(self):
        assert cli._model_name(_make_model("publishers/ns/models/qwen3-8b")) == "qwen3-8b"

    def test_model_name_no_slash(self):
        assert cli._model_name(_make_model("bare-model")) == "bare-model"

    def test_extra_fields_excludes_known(self):
        model = _make_model("publishers/ns/models/foo", object="model", root="qwen")
        extra = cli._extra_fields(model)
        assert extra == {"root": "qwen"}

    def test_format_ts(self):
        assert cli._format_ts(1_700_000_000) == "2023-11-14 22:13"

    def test_format_ts_none(self):
        assert cli._format_ts(None) == "—"

    def test_compact_metadata(self):
        assert cli._compact_metadata(None) == "—"
        assert cli._compact_metadata({"b": 2, "a": 1}) == "a=1, b=2"

    def test_find_model_by_full_id_and_name(self):
        models = [_make_model("publishers/ns/models/foo"), _make_model("publishers/ns/models/bar")]
        assert cli._find_model(models, "publishers/ns/models/foo") is models[0]  # full id
        assert cli._find_model(models, "bar") is models[1]                       # short name
        assert cli._find_model(models, "missing") is None


# ---------------------------------------------------------------------------
# cmd_models tests
# ---------------------------------------------------------------------------

class TestCmdModels:
    def test_list(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.models.list.return_value = _list_response([
            _make_model("publishers/ns/models/qwen3-8b"),
            _make_model("publishers/ns/models/granite-embed"),
        ])
        cli.cmd_models(client, _SETTINGS, _args(command="models"))
        out = capsys.readouterr().out
        assert "publishers/ns/models/qwen3-8b" in out  # full id column
        assert "granite-embed" in out                  # short name column
        assert "2 model(s)" in out

    def test_empty(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.models.list.return_value = _list_response([])
        cli.cmd_models(client, _SETTINGS, _args(command="models"))
        assert "No models found." in capsys.readouterr().out

    def test_json(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.models.list.return_value = _list_response([_make_model("publishers/ns/models/foo")])
        cli.cmd_models(client, _SETTINGS, _args(command="models", json=True))
        data = json.loads(capsys.readouterr().out)
        assert data["total"] == 1
        model = data["models"][0]
        assert model["id"] == "publishers/ns/models/foo"
        assert model["name"] == "foo"
        assert "owned_by" not in model
        assert "endpoint" not in model

    def test_metadata_column(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        model = _make_model("publishers/ns/models/foo", root="qwen")
        client.models.list.return_value = _list_response([model])
        cli.cmd_models(client, _SETTINGS, _args(command="models", metadata=True))
        out = capsys.readouterr().out
        assert "Metadata" in out
        assert "root=qwen" in out


# ---------------------------------------------------------------------------
# cmd_info tests
# ---------------------------------------------------------------------------

class TestCmdInfo:
    def test_info_by_name(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.models.list.return_value = _list_response([_make_model("publishers/ns/models/foo")])
        cli.cmd_info(client, _SETTINGS, _args(command="info", model_id="foo"))
        out = capsys.readouterr().out
        assert "publishers/ns/models/foo" in out
        assert "https://maas.example.com/v1" in out

    def test_info_by_full_id(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.models.list.return_value = _list_response([_make_model("publishers/ns/models/foo")])
        cli.cmd_info(client, _SETTINGS, _args(command="info", model_id="publishers/ns/models/foo"))
        assert "foo" in capsys.readouterr().out

    def test_info_not_found_exits(self):
        client = MagicMock()
        client.models.list.return_value = _list_response([_make_model("publishers/ns/models/foo")])
        with pytest.raises(SystemExit, match="not found"):
            cli.cmd_info(client, _SETTINGS, _args(command="info", model_id="missing"))

    def test_info_json(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        model = _make_model("publishers/ns/models/foo", root="qwen")
        client.models.list.return_value = _list_response([model])
        cli.cmd_info(client, _SETTINGS, _args(command="info", model_id="foo", json=True))
        data = json.loads(capsys.readouterr().out)
        assert data["id"] == "publishers/ns/models/foo"
        assert data["name"] == "foo"
        assert data["endpoint"] == "https://maas.example.com/v1"
        assert data["extra"] == {"root": "qwen"}
        assert "owned_by" not in data


# ---------------------------------------------------------------------------
# cmd_check tests
# ---------------------------------------------------------------------------

class TestCmdCheck:
    def test_auto_detects_llm(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.models.list.return_value = _list_response([_make_model("publishers/ns/models/qwen")])
        client.chat.completions.create.return_value = _completion("4")
        cli.cmd_check(client, _SETTINGS, _args(command="check", model_id="qwen"))
        out = capsys.readouterr().out
        assert "PASS" in out
        assert "llm" in out
        assert "4" in out
        # The listed id is passed straight through as the request's model param.
        assert client.chat.completions.create.call_args.kwargs["model"] == "publishers/ns/models/qwen"

    def test_auto_falls_back_to_embedding(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.models.list.return_value = _list_response([_make_model("publishers/ns/models/embed")])
        client.chat.completions.create.side_effect = RuntimeError("not a chat model")
        client.embeddings.create.return_value = _embedding(768)
        cli.cmd_check(client, _SETTINGS, _args(command="check", model_id="embed"))
        out = capsys.readouterr().out
        assert "PASS" in out
        assert "embedding" in out
        assert "768" in out

    def test_auto_both_fail(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.models.list.return_value = _list_response([_make_model("publishers/ns/models/dead")])
        client.chat.completions.create.side_effect = RuntimeError("chat down")
        client.embeddings.create.side_effect = RuntimeError("embed down")
        cli.cmd_check(client, _SETTINGS, _args(command="check", model_id="dead"))
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "chat down" in out
        assert "embed down" in out

    def test_explicit_llm_does_not_probe_embedding(self):
        client = MagicMock()
        client.models.list.return_value = _list_response([_make_model("publishers/ns/models/x")])
        client.chat.completions.create.side_effect = RuntimeError("boom")
        cli.cmd_check(client, _SETTINGS, _args(command="check", model_id="x", type="llm"))
        client.embeddings.create.assert_not_called()

    def test_not_found_exits(self):
        client = MagicMock()
        client.models.list.return_value = _list_response([_make_model("publishers/ns/models/foo")])
        with pytest.raises(SystemExit, match="not found"):
            cli.cmd_check(client, _SETTINGS, _args(command="check", model_id="missing"))

    def test_all_models(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.models.list.return_value = _list_response([
            _make_model("publishers/ns/models/qwen"),
            _make_model("publishers/ns/models/embed"),
        ])
        client.chat.completions.create.return_value = _completion("4")
        cli.cmd_check(client, _SETTINGS, _args(command="check", model_id=None))
        out = capsys.readouterr().out
        assert "qwen" in out
        assert "embed" in out
        assert "2 passed" in out
        assert "2 total" in out

    def test_json_single(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.models.list.return_value = _list_response([_make_model("publishers/ns/models/qwen")])
        client.chat.completions.create.return_value = _completion("4")
        cli.cmd_check(client, _SETTINGS, _args(command="check", model_id="qwen", json=True))
        data = json.loads(capsys.readouterr().out)
        assert data["model_id"] == "publishers/ns/models/qwen"
        assert data["status"] == "pass"
        assert data["detected_type"] == "llm"
        assert data["response"] == "4"

    def test_json_all(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.models.list.return_value = _list_response([_make_model("publishers/ns/models/embed")])
        client.chat.completions.create.side_effect = RuntimeError("no chat")
        client.embeddings.create.return_value = _embedding(1536)
        cli.cmd_check(client, _SETTINGS, _args(command="check", model_id=None, json=True))
        data = json.loads(capsys.readouterr().out)
        assert data["total"] == 1
        assert data["results"][0]["detected_type"] == "embedding"
        assert data["results"][0]["dimensions"] == 1536


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

class TestParser:
    def test_models_default(self):
        args = cli._build_parser().parse_args(["models"])
        assert args.command == "models"
        assert args.metadata is False

    def test_models_metadata(self):
        args = cli._build_parser().parse_args(["models", "-m"])
        assert args.metadata is True

    def test_info(self):
        args = cli._build_parser().parse_args(["info", "my-model"])
        assert args.command == "info"
        assert args.model_id == "my-model"

    def test_check_defaults(self):
        args = cli._build_parser().parse_args(["check"])
        assert args.model_id is None
        assert args.type == "auto"
        assert "2+2" in args.prompt

    def test_check_with_model_and_type(self):
        args = cli._build_parser().parse_args(["check", "my-model", "--type", "llm"])
        assert args.model_id == "my-model"
        assert args.type == "llm"

    def test_json_flag(self):
        args = cli._build_parser().parse_args(["--json", "models"])
        assert args.json is True


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestShortBody:
    def test_html_page_is_masked(self):
        assert cli._short_body("<html><body>oops</body></html>") == "(HTML error page)"

    def test_doctype_page_is_masked(self):
        assert cli._short_body("<!DOCTYPE html><html>oops</html>") == "(HTML error page)"

    def test_plain_body_collapsed_and_clipped(self):
        assert cli._short_body("  too   many\nspaces  ") == "too many spaces"
        assert cli._short_body("x" * 300).endswith("…")


class TestAbortOnApiError:
    """The command dispatch distills SDK errors into concise, non-zero exits."""

    @staticmethod
    def _status_error(status_code: int, body: str) -> APIStatusError:
        request = httpx.Request("GET", "https://maas.example.com/v1/models")
        response = httpx.Response(status_code, request=request, text=body)
        return APIStatusError("boom", response=response, body=None)

    def test_503_router_page_reports_unavailable(self):
        exc = self._status_error(503, "<html>Application is not available</html>")
        with pytest.raises(SystemExit) as excinfo:
            cli._abort_on_api_error(exc, _SETTINGS)
        msg = str(excinfo.value)
        assert "HTTP 503" in msg
        assert "unavailable" in msg
        assert "<html>" not in msg  # raw router page must not leak

    def test_auth_error_points_at_api_key(self):
        exc = self._status_error(401, '{"error":"unauthorized"}')
        with pytest.raises(SystemExit) as excinfo:
            cli._abort_on_api_error(exc, _SETTINGS)
        assert "api_key" in str(excinfo.value)

    def test_other_status_includes_status_and_body(self):
        exc = self._status_error(422, '{"error":"bad input"}')
        with pytest.raises(SystemExit) as excinfo:
            cli._abort_on_api_error(exc, _SETTINGS)
        msg = str(excinfo.value)
        assert "HTTP 422" in msg
        assert "bad input" in msg

    def test_connection_error_reports_endpoint(self):
        request = httpx.Request("GET", "https://maas.example.com/v1/models")
        exc = APIConnectionError(request=request)
        with pytest.raises(SystemExit) as excinfo:
            cli._abort_on_api_error(exc, _SETTINGS)
        msg = str(excinfo.value)
        assert "Cannot reach MaaS" in msg
        assert "maas.example.com" in msg

    def test_main_translates_listing_failure(self, monkeypatch: pytest.MonkeyPatch):
        """A listing failure in a command surfaces as a clean exit, not a traceback."""
        exc = self._status_error(503, "<html>Application is not available</html>")
        client = MagicMock()
        client.models.list.side_effect = exc

        monkeypatch.setattr(sys, "argv", ["maas", "-t", "maas", "models"])
        monkeypatch.setattr("autox_tools.config._loader.resolve", lambda *_a, **_k: None)
        monkeypatch.setattr(cli._client, "resolve_settings", lambda _cfg: _SETTINGS)
        monkeypatch.setattr(cli._client, "connect", lambda _s: client)

        with pytest.raises(SystemExit) as excinfo:
            cli.main()
        assert "HTTP 503" in str(excinfo.value)
