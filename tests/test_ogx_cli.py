"""Unit tests for the OGX CLI tool.

All tests mock ogx_client -- no real OGX server required.
"""

from __future__ import annotations

import argparse
import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from autox_tools.ogx import cli


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_model(
    model_id: str = "test-model",
    model_type: str = "llm",
    provider_id: str = "test-provider",
    created: int = 1_700_000_000,
    owned_by: str = "test",
) -> SimpleNamespace:
    """Build a fake Model object."""
    return SimpleNamespace(
        id=model_id,
        model_type=model_type,
        provider_id=provider_id,
        provider_resource_id=f"{model_id}-resource",
        created=created,
        owned_by=owned_by,
        metadata={},
    )


def _make_provider(
    provider_id: str = "milvus-prod",
    provider_type: str = "milvus",
    api: str = "vector_io",
) -> SimpleNamespace:
    """Build a fake ProviderInfo object."""
    return SimpleNamespace(
        provider_id=provider_id,
        provider_type=provider_type,
        api=api,
        config={},
        health={"status": "OK"},
    )


def _make_vector_store(
    vs_id: str = "vs_123",
    name: str = "test-store",
    status: str = "completed",
    file_total: int = 10,
    usage_bytes: int = 1_048_576,
    created_at: int = 1_700_000_000,
) -> SimpleNamespace:
    """Build a fake VectorStore object."""
    return SimpleNamespace(
        id=vs_id,
        name=name,
        status=status,
        file_counts=SimpleNamespace(
            completed=file_total, in_progress=0, failed=0, cancelled=0, total=file_total,
        ),
        usage_bytes=usage_bytes,
        created_at=created_at,
        expires_at=None,
        last_active_at=created_at + 3600,
        metadata={},
    )


def _make_completion_response(content: str = "4") -> SimpleNamespace:
    """Build a fake chat completion response."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
    )


def _make_embedding_response(dimensions: int = 1536) -> SimpleNamespace:
    """Build a fake embeddings response."""
    return SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.1] * dimensions)],
    )


def _args(**kwargs: object) -> argparse.Namespace:
    """Build an argparse.Namespace with defaults for the ogx CLI."""
    defaults: dict[str, object] = {
        "json": False,
        "command": "health",
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# _client.py tests
# ---------------------------------------------------------------------------

class TestClientConnect:
    def test_missing_env_vars_exits(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("autox_tools.ogx._client.load_dotenv"),
            patch("autox_tools.ogx._client.find_dotenv", return_value=""),
            pytest.raises(SystemExit, match="Missing required environment variables"),
        ):
            from autox_tools.ogx._client import connect
            connect()

    def test_connect_builds_client(self):
        env = {"OGX_CLIENT_BASE_URL": "https://ogx.example.com", "OGX_CLIENT_API_KEY": "test-key"}
        with (
            patch.dict(os.environ, env, clear=True),
            patch("autox_tools.ogx._client.load_dotenv"),
            patch("autox_tools.ogx._client.find_dotenv", return_value=""),
            patch("autox_tools.ogx._client.OgxClient") as mock_cls,
        ):
            from autox_tools.ogx._client import connect
            connect()
            mock_cls.assert_called_once_with(base_url="https://ogx.example.com", api_key="test-key")

    def test_connect_without_api_key(self):
        env = {"OGX_CLIENT_BASE_URL": "https://ogx.example.com"}
        with (
            patch.dict(os.environ, env, clear=True),
            patch("autox_tools.ogx._client.load_dotenv"),
            patch("autox_tools.ogx._client.find_dotenv", return_value=""),
            patch("autox_tools.ogx._client.OgxClient") as mock_cls,
        ):
            from autox_tools.ogx._client import connect
            connect()
            _, kwargs = mock_cls.call_args
            assert kwargs["api_key"] is None


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------

class TestHelpers:
    @pytest.mark.parametrize(
        ("nbytes", "expected"),
        [
            (0, "0 B"),
            (512, "512 B"),
            (1024, "1.0 KB"),
            (1_048_576, "1.0 MB"),
            (1_073_741_824, "1.0 GB"),
            (None, "—"),
        ],
    )
    def test_human_size(self, nbytes: int | None, expected: str):
        assert cli._human_size(nbytes) == expected

    def test_format_ts_with_value(self):
        assert cli._format_ts(1_700_000_000) == "2023-11-14 22:13"

    def test_format_ts_none(self):
        assert cli._format_ts(None) == "—"


# ---------------------------------------------------------------------------
# cmd_models tests
# ---------------------------------------------------------------------------

class TestCmdModels:
    def test_list_all_models(self, capsys: pytest.CaptureFixture[str]):
        models = [
            _make_model("gpt-4o", "llm", "openai"),
            _make_model("bge-large", "embedding", "local-hf"),
            _make_model("reranker-v1", "rerank", "local-hf"),
        ]
        client = MagicMock()
        client.models.list.return_value = SimpleNamespace(data=models)

        cli.cmd_models(client, _args(command="models", type="all"))

        out = capsys.readouterr().out
        assert "gpt-4o" in out
        assert "bge-large" in out
        assert "reranker-v1" in out
        assert "3 model(s)" in out

    def test_filter_by_type_llm(self, capsys: pytest.CaptureFixture[str]):
        models = [
            _make_model("gpt-4o", "llm", "openai"),
            _make_model("bge-large", "embedding", "local-hf"),
        ]
        client = MagicMock()
        client.models.list.return_value = SimpleNamespace(data=models)

        cli.cmd_models(client, _args(command="models", type="llm"))

        out = capsys.readouterr().out
        assert "gpt-4o" in out
        assert "bge-large" not in out
        assert "1 model(s)" in out

    def test_filter_by_type_embedding(self, capsys: pytest.CaptureFixture[str]):
        models = [
            _make_model("gpt-4o", "llm", "openai"),
            _make_model("bge-large", "embedding", "local-hf"),
        ]
        client = MagicMock()
        client.models.list.return_value = SimpleNamespace(data=models)

        cli.cmd_models(client, _args(command="models", type="embedding"))

        out = capsys.readouterr().out
        assert "bge-large" in out
        assert "gpt-4o" not in out

    def test_no_models(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.models.list.return_value = SimpleNamespace(data=[])

        cli.cmd_models(client, _args(command="models", type="all"))

        out = capsys.readouterr().out
        assert "No models found." in out

    def test_json_output(self, capsys: pytest.CaptureFixture[str]):
        models = [_make_model("gpt-4o", "llm", "openai")]
        client = MagicMock()
        client.models.list.return_value = SimpleNamespace(data=models)

        cli.cmd_models(client, _args(command="models", type="all", json=True))

        data = json.loads(capsys.readouterr().out)
        assert data["total"] == 1
        assert data["models"][0]["id"] == "gpt-4o"
        assert data["models"][0]["model_type"] == "llm"


# ---------------------------------------------------------------------------
# cmd_providers tests
# ---------------------------------------------------------------------------

class TestCmdProviders:
    def test_list_vector_store_providers(self, capsys: pytest.CaptureFixture[str]):
        providers = [
            _make_provider("milvus-prod", "remote::milvus", "vector_io"),
            _make_provider("inference-main", "remote::vllm", "inference"),
            _make_provider("qdrant-dev", "remote::qdrant", "vector_io"),
        ]
        client = MagicMock()
        client.providers.list.return_value = providers

        cli.cmd_providers(client, _args(command="providers"))

        out = capsys.readouterr().out
        assert "milvus-prod" in out
        assert "qdrant-dev" in out
        assert "inference-main" not in out
        assert "2 provider(s)" in out

    def test_no_providers(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.providers.list.return_value = [
            _make_provider("inference-main", "remote::vllm", "inference"),
        ]

        cli.cmd_providers(client, _args(command="providers"))

        out = capsys.readouterr().out
        assert "No vector store providers found." in out

    def test_json_output(self, capsys: pytest.CaptureFixture[str]):
        providers = [_make_provider("milvus-prod", "remote::milvus", "vector_io")]
        client = MagicMock()
        client.providers.list.return_value = providers

        cli.cmd_providers(client, _args(command="providers", json=True))

        data = json.loads(capsys.readouterr().out)
        assert data["total"] == 1
        assert data["providers"][0]["provider_id"] == "milvus-prod"


# ---------------------------------------------------------------------------
# cmd_stores tests
# ---------------------------------------------------------------------------

class TestCmdStores:
    def test_list_stores(self, capsys: pytest.CaptureFixture[str]):
        stores = [
            _make_vector_store("vs_1", "product-docs", "completed", 42, 134_742_016),
            _make_vector_store("vs_2", "support-kb", "in_progress", 5, 5_242_880),
        ]
        client = MagicMock()
        client.vector_stores.list.return_value = SimpleNamespace(data=stores)

        cli.cmd_stores(client, _args(command="stores"))

        out = capsys.readouterr().out
        assert "product-docs" in out
        assert "support-kb" in out
        assert "128.5 MB" in out
        assert "5.0 MB" in out
        assert "2 vector store(s)" in out

    def test_no_stores(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.vector_stores.list.return_value = SimpleNamespace(data=[])

        cli.cmd_stores(client, _args(command="stores"))

        out = capsys.readouterr().out
        assert "No vector stores found." in out

    def test_json_output(self, capsys: pytest.CaptureFixture[str]):
        stores = [_make_vector_store("vs_1", "product-docs", "completed", 42, 1_048_576)]
        client = MagicMock()
        client.vector_stores.list.return_value = SimpleNamespace(data=stores)

        cli.cmd_stores(client, _args(command="stores", json=True))

        data = json.loads(capsys.readouterr().out)
        assert data["total"] == 1
        assert data["vector_stores"][0]["name"] == "product-docs"
        assert data["vector_stores"][0]["usage_bytes"] == 1_048_576


# ---------------------------------------------------------------------------
# cmd_health tests
# ---------------------------------------------------------------------------

class TestCmdHealth:
    def test_health_ok(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.inspect.health.return_value = SimpleNamespace(status="OK")
        client.inspect.version.return_value = SimpleNamespace(version="1.0.0")

        cli.cmd_health(client, _args(command="health"))

        out = capsys.readouterr().out
        assert "OK" in out
        assert "1.0.0" in out

    def test_health_error_status(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.inspect.health.return_value = SimpleNamespace(status="Error")
        client.inspect.version.return_value = SimpleNamespace(version="1.0.0")

        cli.cmd_health(client, _args(command="health"))

        out = capsys.readouterr().out
        assert "Error" in out

    def test_version_failure_fallback(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.inspect.health.return_value = SimpleNamespace(status="OK")
        client.inspect.version.side_effect = RuntimeError("endpoint not available")

        cli.cmd_health(client, _args(command="health"))

        out = capsys.readouterr().out
        assert "OK" in out
        assert "unknown" in out

    def test_json_output(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.inspect.health.return_value = SimpleNamespace(status="OK")
        client.inspect.version.return_value = SimpleNamespace(version="1.2.3")

        cli.cmd_health(client, _args(command="health", json=True))

        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "OK"
        assert data["version"] == "1.2.3"


# ---------------------------------------------------------------------------
# cmd_check tests
# ---------------------------------------------------------------------------

class TestCmdCheck:
    def test_llm_pass(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.models.retrieve.return_value = _make_model("gpt-4o", "llm", "openai")
        client.chat.completions.create.return_value = _make_completion_response("4")

        cli.cmd_check(client, _args(
            command="check", model_id="gpt-4o",
            prompt="What is 2+2? Reply with just the number.",
            input="ignored",
        ))

        out = capsys.readouterr().out
        assert "PASS" in out
        assert "4" in out
        assert "gpt-4o" in out

    def test_llm_fail(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.models.retrieve.return_value = _make_model("bad-model", "llm", "broken")
        client.chat.completions.create.side_effect = RuntimeError("Connection refused")

        cli.cmd_check(client, _args(
            command="check", model_id="bad-model",
            prompt="test", input="ignored",
        ))

        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "Connection refused" in out

    def test_embedding_pass(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.models.retrieve.return_value = _make_model("bge-large", "embedding", "local-hf")
        client.embeddings.create.return_value = _make_embedding_response(768)

        cli.cmd_check(client, _args(
            command="check", model_id="bge-large",
            prompt="ignored",
            input="Test text.",
        ))

        out = capsys.readouterr().out
        assert "PASS" in out
        assert "768" in out

    def test_embedding_fail(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.models.retrieve.return_value = _make_model("bad-embed", "embedding", "broken")
        client.embeddings.create.side_effect = RuntimeError("Model offline")

        cli.cmd_check(client, _args(
            command="check", model_id="bad-embed",
            prompt="ignored", input="test",
        ))

        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "Model offline" in out

    def test_rerank_skipped(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.models.retrieve.return_value = _make_model("reranker-v1", "rerank", "local")

        cli.cmd_check(client, _args(
            command="check", model_id="reranker-v1",
            prompt="ignored", input="ignored",
        ))

        out = capsys.readouterr().out
        assert "SKIPPED" in out
        assert "not supported for rerank" in out

    def test_model_not_found(self):
        client = MagicMock()
        client.models.retrieve.side_effect = RuntimeError("404 Not Found")

        with pytest.raises(SystemExit, match="not found"):
            cli.cmd_check(client, _args(
                command="check", model_id="nonexistent",
                prompt="test", input="test",
            ))

    def test_custom_prompt(self):
        client = MagicMock()
        client.models.retrieve.return_value = _make_model("gpt-4o", "llm", "openai")
        client.chat.completions.create.return_value = _make_completion_response("hello")

        cli.cmd_check(client, _args(
            command="check", model_id="gpt-4o",
            prompt="Say hello.", input="ignored",
        ))

        call_kwargs = client.chat.completions.create.call_args[1]
        assert call_kwargs["messages"][0]["content"] == "Say hello."

    def test_json_output_pass(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.models.retrieve.return_value = _make_model("gpt-4o", "llm", "openai")
        client.chat.completions.create.return_value = _make_completion_response("4")

        cli.cmd_check(client, _args(
            command="check", model_id="gpt-4o",
            prompt="What is 2+2?", input="ignored", json=True,
        ))

        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "pass"
        assert data["response"] == "4"
        assert data["model_type"] == "llm"

    def test_json_output_fail(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.models.retrieve.return_value = _make_model("bad-model", "llm", "broken")
        client.chat.completions.create.side_effect = RuntimeError("Timeout")

        cli.cmd_check(client, _args(
            command="check", model_id="bad-model",
            prompt="test", input="ignored", json=True,
        ))

        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "fail"
        assert "Timeout" in data["error"]

    def test_json_output_embedding(self, capsys: pytest.CaptureFixture[str]):
        client = MagicMock()
        client.models.retrieve.return_value = _make_model("bge-large", "embedding", "local-hf")
        client.embeddings.create.return_value = _make_embedding_response(1536)

        cli.cmd_check(client, _args(
            command="check", model_id="bge-large",
            prompt="ignored", input="Test.", json=True,
        ))

        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "pass"
        assert data["dimensions"] == 1536

    def test_unknown_type_exits(self):
        client = MagicMock()
        client.models.retrieve.return_value = _make_model("weird", "unknown", "x")

        with pytest.raises(SystemExit, match="Unknown model type"):
            cli.cmd_check(client, _args(
                command="check", model_id="weird",
                prompt="test", input="test",
            ))


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

class TestParser:
    def test_models_default_type(self):
        args = cli._build_parser().parse_args(["models"])
        assert args.command == "models"
        assert args.type == "all"

    def test_models_with_type(self):
        args = cli._build_parser().parse_args(["models", "--type", "llm"])
        assert args.type == "llm"

    def test_check_args(self):
        args = cli._build_parser().parse_args(["check", "my-model", "--prompt", "hello"])
        assert args.command == "check"
        assert args.model_id == "my-model"
        assert args.prompt == "hello"

    def test_check_default_prompt(self):
        args = cli._build_parser().parse_args(["check", "my-model"])
        assert "2+2" in args.prompt

    def test_json_flag(self):
        args = cli._build_parser().parse_args(["--json", "models"])
        assert args.json is True

    def test_health_no_args(self):
        args = cli._build_parser().parse_args(["health"])
        assert args.command == "health"

    def test_stores_no_args(self):
        args = cli._build_parser().parse_args(["stores"])
        assert args.command == "stores"

    def test_providers_no_args(self):
        args = cli._build_parser().parse_args(["providers"])
        assert args.command == "providers"
