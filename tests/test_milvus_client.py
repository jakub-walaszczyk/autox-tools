"""Unit tests for Milvus URI and token connections."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from autox_tools.config._models import MilvusConfig
from autox_tools.vs.milvus._client import connect


@pytest.mark.parametrize("token", ["api-key", "root:test-password", ""])
@pytest.mark.parametrize("uri", ["http://localhost:19530", "https://milvus.example.com:443"])
def test_connect_from_config(uri, token):
    cfg = MilvusConfig(uri=uri, token=token, server_pem_path="/certs/ca.pem")
    with patch("autox_tools.vs.milvus._client.MilvusClient") as client:
        assert connect(cfg) is client.return_value

    client.assert_called_once_with(
        uri=uri,
        token=token,
        secure=uri.startswith("https://"),
        server_pem_path="/certs/ca.pem",
    )


@pytest.mark.parametrize("token", ["api-key", "root:test-password", ""])
@pytest.mark.parametrize("uri", ["http://localhost:19530", "https://milvus.example.com"])
def test_connect_from_env(monkeypatch, uri, token):
    monkeypatch.setattr("autox_tools.vs.milvus._client.load_dotenv", lambda *_: None)
    monkeypatch.setattr("autox_tools.vs.milvus._client.find_dotenv", lambda **_: "")
    with (
        patch.dict("os.environ", {"MILVUS_URI": uri, "MILVUS_TOKEN": token}, clear=True),
        patch("autox_tools.vs.milvus._client.MilvusClient") as client,
    ):
        assert connect() is client.return_value

    client.assert_called_once_with(
        uri=uri,
        token=token,
        secure=uri.startswith("https://"),
        server_pem_path="",
    )


def test_connect_from_config_without_auth():
    with patch("autox_tools.vs.milvus._client.MilvusClient") as client:
        connect(MilvusConfig(uri="http://localhost:19530"))

    client.assert_called_once_with(uri="http://localhost:19530", token="", secure=False, server_pem_path="")


def test_connect_from_config_with_explicit_tls():
    with patch("autox_tools.vs.milvus._client.MilvusClient") as client:
        connect(MilvusConfig(uri="tcp://milvus.example.com:19530", secure=True))

    assert client.call_args.kwargs["secure"] is True


def test_connect_from_dotenv_without_token(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "MILVUS_URI=tcp://milvus.example.com:19530\n"
        "MILVUS_SECURE=true\n"
        "MILVUS_SERVER_PEM_PATH=/certs/ca.pem\n"
    )
    with patch.dict("os.environ", {}, clear=True), patch("autox_tools.vs.milvus._client.MilvusClient") as client:
        connect()

    client.assert_called_once_with(
        uri="tcp://milvus.example.com:19530", token="", secure=True, server_pem_path="/certs/ca.pem",
    )


@pytest.mark.parametrize("uri", [None, ""])
def test_connect_requires_uri(tmp_path, monkeypatch, uri):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("autox_tools.vs.milvus._client.find_dotenv", lambda **_: "")
    env = {"MILVUS_TOKEN": "api-key"}
    if uri is not None:
        env["MILVUS_URI"] = uri
    with (
        patch.dict("os.environ", env, clear=True),
        patch("autox_tools.vs.milvus._client.MilvusClient") as client,
        pytest.raises(SystemExit, match="Missing required environment variables: MILVUS_URI"),
    ):
        connect()

    client.assert_not_called()
