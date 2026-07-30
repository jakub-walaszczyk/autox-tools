"""Unit tests for the secrets CLI tool.

All tests mock the Kubernetes client -- no real cluster required.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from datetime import UTC
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from autox_tools.secrets import cli
from autox_tools.secrets._client import _derive_k8s_api_url

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_secret(
    name: str = "test-secret",
    namespace: str = "test-ns",
    data: dict[str, str] | None = None,
    secret_type: str = "Opaque",
    labels: dict[str, str] | None = None,
    created: str = "2025-05-20T10:00:00+00:00",
    resource_version: str = "12345",
) -> SimpleNamespace:
    """Build a fake V1Secret object with base64-encoded data."""
    encoded_data: dict[str, str] | None = None
    if data is not None:
        encoded_data = {k: base64.b64encode(v.encode()).decode() for k, v in data.items()}
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            namespace=namespace,
            labels=labels,
            creation_timestamp=created,
            resource_version=resource_version,
        ),
        data=encoded_data,
        type=secret_type,
    )


def _make_secret_list(*secrets: SimpleNamespace) -> SimpleNamespace:
    """Build a fake V1SecretList."""
    return SimpleNamespace(items=list(secrets))


def _mock_api() -> MagicMock:
    return MagicMock()


def _ns_args(**overrides: object) -> argparse.Namespace:
    """Build a minimal argparse.Namespace with defaults for the secrets CLI."""
    defaults: dict[str, object] = {
        "json": False,
        "namespace": "test-ns",
        "command": "list",
        "type": None,
        "filter": None,
        "labels": None,
        "name": "test-secret",
        "from_literal": None,
        "from_env_file": None,
        "yes": True,
        "set_values": None,
        "remove_keys": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# _client.py tests
# ---------------------------------------------------------------------------


class TestDeriveK8sApiUrl:
    def test_standard_ocp(self) -> None:
        url = _derive_k8s_api_url("https://ds-pipeline.apps.mycluster.example.com/")
        assert url == "https://api.mycluster.example.com:6443"

    def test_rosa(self) -> None:
        url = _derive_k8s_api_url("https://ds-pipeline.apps.rosa.mycluster.example.com/")
        assert url == "https://api.mycluster.example.com:443"

    def test_invalid_url_returns_none(self) -> None:
        assert _derive_k8s_api_url("https://some-host.example.com") is None

    def test_empty_url_returns_none(self) -> None:
        assert _derive_k8s_api_url("") is None

    @patch.dict(os.environ, {"K8S_API_PORT": "9443"})
    def test_custom_port(self) -> None:
        url = _derive_k8s_api_url("https://ds-pipeline.apps.mycluster.example.com/")
        assert url == "https://api.mycluster.example.com:9443"


class TestClientConnect:
    @patch.dict(os.environ, {}, clear=True)
    @patch("autox_tools._k8s.load_dotenv")
    @patch("autox_tools._k8s.find_dotenv", return_value="")
    def test_missing_token_exits(self, _find: MagicMock, _load: MagicMock) -> None:
        with pytest.raises(SystemExit, match="Missing RHOAI_TOKEN"):
            from autox_tools.secrets._client import connect
            connect()

    @patch.dict(os.environ, {"RHOAI_TOKEN": "sha256~abc", "K8S_API_URL": "https://api.cluster:6443"})
    @patch("autox_tools._k8s.load_dotenv")
    @patch("autox_tools._k8s.find_dotenv", return_value="")
    @patch("autox_tools._k8s.k8s_client")
    def test_connect_with_explicit_url(self, mock_k8s: MagicMock, _find: MagicMock, _load: MagicMock) -> None:
        mock_config = MagicMock()
        mock_k8s.Configuration.return_value = mock_config
        mock_api_client = MagicMock()
        mock_k8s.ApiClient.return_value = mock_api_client

        from autox_tools.secrets._client import connect
        connect()

        assert mock_config.host == "https://api.cluster:6443"
        mock_k8s.CoreV1Api.assert_called_once_with(mock_api_client)

    @patch.dict(os.environ, {
        "RHOAI_TOKEN": "sha256~abc",
        "RHOAI_KFP_URL": "https://ds-pipeline.apps.mycluster.example.com/",
    }, clear=True)
    @patch("autox_tools._k8s.load_dotenv")
    @patch("autox_tools._k8s.find_dotenv", return_value="")
    @patch("autox_tools._k8s.k8s_client")
    def test_connect_derives_from_kfp_url(self, mock_k8s: MagicMock, _find: MagicMock, _load: MagicMock) -> None:
        mock_config = MagicMock()
        mock_k8s.Configuration.return_value = mock_config

        from autox_tools.secrets._client import connect
        connect()

        assert mock_config.host == "https://api.mycluster.example.com:6443"

    @patch.dict(os.environ, {"RHOAI_TOKEN": "sha256~abc"}, clear=True)
    @patch("autox_tools._k8s.load_dotenv")
    @patch("autox_tools._k8s.find_dotenv", return_value="")
    def test_connect_no_url_exits(self, _find: MagicMock, _load: MagicMock) -> None:
        with pytest.raises(SystemExit, match="K8S API URL could not be resolved"):
            from autox_tools.secrets._client import connect
            connect()

    @patch.dict(os.environ, {
        "RHOAI_TOKEN": "sha256~abc",
        "K8S_API_URL": "https://api.cluster:6443",
        "KFP_VERIFY_SSL": "false",
    })
    @patch("autox_tools._k8s.load_dotenv")
    @patch("autox_tools._k8s.find_dotenv", return_value="")
    @patch("autox_tools._k8s.k8s_client")
    @patch("autox_tools._k8s.urllib3")
    def test_connect_ssl_disabled(
        self, mock_urllib3: MagicMock, mock_k8s: MagicMock, _find: MagicMock, _load: MagicMock,
    ) -> None:
        mock_config = MagicMock()
        mock_k8s.Configuration.return_value = mock_config

        from autox_tools.secrets._client import connect
        connect()

        assert mock_config.verify_ssl is False
        mock_urllib3.disable_warnings.assert_called_once()


# ---------------------------------------------------------------------------
# Namespace resolution
# ---------------------------------------------------------------------------


class TestResolveNamespace:
    def test_cli_flag_takes_priority(self) -> None:
        args = _ns_args(namespace="from-flag")
        with patch.dict(os.environ, {"RHOAI_PROJECT_NAME": "from-env"}):
            assert cli._resolve_namespace(args) == "from-flag"

    def test_env_var_fallback(self) -> None:
        args = _ns_args(namespace=None)
        with patch.dict(os.environ, {"RHOAI_PROJECT_NAME": "from-env"}):
            assert cli._resolve_namespace(args) == "from-env"

    def test_missing_namespace_exits(self) -> None:
        args = _ns_args(namespace=None)
        with patch.dict(os.environ, {}, clear=True), pytest.raises(SystemExit, match="Namespace is required"):
            cli._resolve_namespace(args)


# ---------------------------------------------------------------------------
# Decode helper
# ---------------------------------------------------------------------------


class TestDecodeSecretData:
    def test_decodes_values(self) -> None:
        data = {
            "user": base64.b64encode(b"admin").decode(),
            "pass": base64.b64encode(b"s3cret").decode(),
        }
        result = cli._decode_secret_data(data)
        assert result == {"user": "admin", "pass": "s3cret"}

    def test_handles_none(self) -> None:
        assert cli._decode_secret_data(None) == {}

    def test_handles_empty_dict(self) -> None:
        assert cli._decode_secret_data({}) == {}


# ---------------------------------------------------------------------------
# list command
# ---------------------------------------------------------------------------


class TestCmdList:
    def test_lists_opaque_secrets(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = _mock_api()
        api.list_namespaced_secret.return_value = _make_secret_list(
            _make_secret("db-creds", data={"user": "admin", "pass": "pw"}),
            _make_secret("api-key", data={"key": "abc123"}),
        )
        cli.cmd_list(api, _ns_args(), "test-ns")
        out = capsys.readouterr().out
        assert "db-creds" in out
        assert "api-key" in out
        assert "2 secret(s)" in out

    def test_lists_all_types_by_default(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = _mock_api()
        api.list_namespaced_secret.return_value = _make_secret_list(
            _make_secret("opaque-one", data={"k": "v"}),
            _make_secret("sa-token", secret_type="kubernetes.io/service-account-token", data={"token": "x"}),
            _make_secret("tls-cert", secret_type="kubernetes.io/tls", data={"tls.crt": "x"}),
        )
        cli.cmd_list(api, _ns_args(), "test-ns")
        out = capsys.readouterr().out
        assert "opaque-one" in out
        assert "sa-token" in out
        assert "tls-cert" in out
        # Type column surfaces each secret's type.
        assert "kubernetes.io/tls" in out
        assert "kubernetes.io/service-account-token" in out
        assert "3 secret(s)" in out

    def test_type_filter_short_form(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = _mock_api()
        api.list_namespaced_secret.return_value = _make_secret_list(
            _make_secret("opaque-one", data={"k": "v"}),
            _make_secret("sa-token", secret_type="kubernetes.io/service-account-token", data={"token": "x"}),
            _make_secret("tls-cert", secret_type="kubernetes.io/tls", data={"tls.crt": "x"}),
        )
        cli.cmd_list(api, _ns_args(type="tls"), "test-ns")
        out = capsys.readouterr().out
        assert "tls-cert" in out
        assert "opaque-one" not in out
        assert "sa-token" not in out
        assert "1 secret(s)" in out

    def test_type_filter_full_form(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = _mock_api()
        api.list_namespaced_secret.return_value = _make_secret_list(
            _make_secret("opaque-one", data={"k": "v"}),
            _make_secret("tls-cert", secret_type="kubernetes.io/tls", data={"tls.crt": "x"}),
        )
        cli.cmd_list(api, _ns_args(type="Opaque"), "test-ns")
        out = capsys.readouterr().out
        assert "opaque-one" in out
        assert "tls-cert" not in out
        assert "1 secret(s)" in out

    def test_type_filter_no_match(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = _mock_api()
        api.list_namespaced_secret.return_value = _make_secret_list(
            _make_secret("opaque-one", data={"k": "v"}),
        )
        cli.cmd_list(api, _ns_args(type="tls"), "test-ns")
        out = capsys.readouterr().out
        assert "No secrets of type 'tls' found" in out

    def test_name_filter(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = _mock_api()
        api.list_namespaced_secret.return_value = _make_secret_list(
            _make_secret("db-creds", data={"k": "v"}),
            _make_secret("cache-creds", data={"k": "v"}),
            _make_secret("api-key", data={"k": "v"}),
        )
        cli.cmd_list(api, _ns_args(filter="creds"), "test-ns")
        out = capsys.readouterr().out
        assert "db-creds" in out
        assert "cache-creds" in out
        assert "api-key" not in out

    def test_label_selector_passed(self) -> None:
        api = _mock_api()
        api.list_namespaced_secret.return_value = _make_secret_list()
        cli.cmd_list(api, _ns_args(labels="app=myapp"), "test-ns")
        call_kwargs = api.list_namespaced_secret.call_args
        assert call_kwargs.kwargs["label_selector"] == "app=myapp"

    def test_empty_namespace(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = _mock_api()
        api.list_namespaced_secret.return_value = _make_secret_list()
        cli.cmd_list(api, _ns_args(), "test-ns")
        out = capsys.readouterr().out
        assert "No secrets found" in out

    def test_json_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = _mock_api()
        api.list_namespaced_secret.return_value = _make_secret_list(
            _make_secret("db-creds", data={"user": "admin"}, labels={"app": "db"}),
            _make_secret("tls-cert", secret_type="kubernetes.io/tls", data={"tls.crt": "x"}),
        )
        cli.cmd_list(api, _ns_args(json=True), "test-ns")
        result = json.loads(capsys.readouterr().out)
        assert result["namespace"] == "test-ns"
        assert result["total"] == 2
        assert result["secrets"][0]["name"] == "db-creds"
        assert result["secrets"][0]["type"] == "Opaque"
        assert result["secrets"][0]["keys"] == ["user"]
        assert result["secrets"][0]["labels"] == {"app": "db"}
        assert result["secrets"][1]["type"] == "kubernetes.io/tls"

    def test_k8s_error_403(self) -> None:
        api = _mock_api()
        api.list_namespaced_secret.side_effect = Exception("403 Forbidden")
        with pytest.raises(SystemExit, match="403 Forbidden"):
            cli.cmd_list(api, _ns_args(), "test-ns")


# ---------------------------------------------------------------------------
# reveal command
# ---------------------------------------------------------------------------


class TestCmdReveal:
    def test_decodes_and_displays(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = _mock_api()
        api.read_namespaced_secret.return_value = _make_secret(
            "db-creds", data={"DB_HOST": "pg.svc", "DB_PASS": "s3cret"},
        )
        cli.cmd_reveal(api, _ns_args(name="db-creds"), "test-ns")
        out = capsys.readouterr().out
        assert "db-creds" in out
        assert "pg.svc" in out
        assert "s3cret" in out

    def test_reveals_non_opaque(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = _mock_api()
        api.read_namespaced_secret.return_value = _make_secret(
            "tls-cert", secret_type="kubernetes.io/tls", data={"tls.crt": "PEMDATA"},
        )
        cli.cmd_reveal(api, _ns_args(name="tls-cert"), "test-ns")
        out = capsys.readouterr().out
        assert "tls-cert" in out
        assert "kubernetes.io/tls" in out
        assert "PEMDATA" in out

    def test_not_found_exits(self) -> None:
        api = _mock_api()
        api.read_namespaced_secret.side_effect = Exception("404 Not Found")
        with pytest.raises(SystemExit, match="not found"):
            cli.cmd_reveal(api, _ns_args(name="missing"), "test-ns")

    def test_empty_data(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = _mock_api()
        api.read_namespaced_secret.return_value = _make_secret("empty", data=None)
        cli.cmd_reveal(api, _ns_args(name="empty"), "test-ns")
        out = capsys.readouterr().out
        assert "(no data)" in out

    def test_json_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = _mock_api()
        api.read_namespaced_secret.return_value = _make_secret(
            "db-creds", data={"user": "admin"}, labels={"env": "prod"},
        )
        cli.cmd_reveal(api, _ns_args(name="db-creds", json=True), "test-ns")
        result = json.loads(capsys.readouterr().out)
        assert result["name"] == "db-creds"
        assert result["type"] == "Opaque"
        assert result["data"]["user"] == "admin"
        assert result["labels"] == {"env": "prod"}

    def test_labels_displayed(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = _mock_api()
        api.read_namespaced_secret.return_value = _make_secret(
            "creds", data={"k": "v"}, labels={"app": "web", "tier": "frontend"},
        )
        cli.cmd_reveal(api, _ns_args(name="creds"), "test-ns")
        out = capsys.readouterr().out
        assert "app=web" in out
        assert "tier=frontend" in out


# ---------------------------------------------------------------------------
# create command
# ---------------------------------------------------------------------------


class TestCmdCreate:
    def test_from_literal(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = _mock_api()
        args = _ns_args(
            command="create", name="new-secret",
            from_literal=["user=admin", "pass=s3cret"],
            from_env_file=None, labels=None,
        )
        cli.cmd_create(api, args, "test-ns")
        out = capsys.readouterr().out
        assert "created" in out
        api.create_namespaced_secret.assert_called_once()

        body = api.create_namespaced_secret.call_args.kwargs["body"]
        decoded = {k: base64.b64decode(v).decode() for k, v in body.data.items()}
        assert decoded == {"user": "admin", "pass": "s3cret"}

    def test_from_env_file(self, tmp_path: object, capsys: pytest.CaptureFixture[str]) -> None:
        import pathlib
        env_file = pathlib.Path(str(tmp_path)) / "test.env"
        env_file.write_text("DB_HOST=localhost\nDB_PORT=5432\n# comment\n\nDB_NAME=mydb\n")

        api = _mock_api()
        args = _ns_args(
            command="create", name="env-secret",
            from_literal=None, from_env_file=str(env_file), labels=None,
        )
        cli.cmd_create(api, args, "test-ns")

        body = api.create_namespaced_secret.call_args.kwargs["body"]
        decoded = {k: base64.b64decode(v).decode() for k, v in body.data.items()}
        assert decoded == {"DB_HOST": "localhost", "DB_PORT": "5432", "DB_NAME": "mydb"}

    def test_literal_overrides_env_file(self, tmp_path: object, capsys: pytest.CaptureFixture[str]) -> None:
        import pathlib
        env_file = pathlib.Path(str(tmp_path)) / "test.env"
        env_file.write_text("KEY=from-file\n")

        api = _mock_api()
        args = _ns_args(
            command="create", name="merged",
            from_literal=["KEY=from-literal"],
            from_env_file=str(env_file), labels=None,
        )
        cli.cmd_create(api, args, "test-ns")

        body = api.create_namespaced_secret.call_args.kwargs["body"]
        decoded_val = base64.b64decode(body.data["KEY"]).decode()
        assert decoded_val == "from-literal"

    def test_no_input_exits(self) -> None:
        api = _mock_api()
        args = _ns_args(command="create", from_literal=None, from_env_file=None)
        with pytest.raises(SystemExit, match="--from-literal or --from-env-file"):
            cli.cmd_create(api, args, "test-ns")

    def test_invalid_literal_format_exits(self) -> None:
        api = _mock_api()
        args = _ns_args(command="create", from_literal=["noequals"], from_env_file=None)
        with pytest.raises(SystemExit, match="Invalid literal format"):
            cli.cmd_create(api, args, "test-ns")

    def test_invalid_key_name_exits(self) -> None:
        api = _mock_api()
        args = _ns_args(command="create", from_literal=["bad key=val"], from_env_file=None)
        with pytest.raises(SystemExit, match="Invalid secret key name"):
            cli.cmd_create(api, args, "test-ns")

    def test_conflict_exits(self) -> None:
        api = _mock_api()
        api.create_namespaced_secret.side_effect = Exception("409 Conflict AlreadyExists")
        args = _ns_args(command="create", from_literal=["k=v"], from_env_file=None)
        with pytest.raises(SystemExit, match=r"already exists.*edit"):
            cli.cmd_create(api, args, "test-ns")

    def test_confirmation_abort(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = _mock_api()
        args = _ns_args(command="create", from_literal=["k=v"], from_env_file=None, yes=False)
        with patch("builtins.input", return_value="n"):
            cli.cmd_create(api, args, "test-ns")
        out = capsys.readouterr().out
        assert "Aborted" in out
        api.create_namespaced_secret.assert_not_called()

    def test_yes_skips_prompt(self) -> None:
        api = _mock_api()
        args = _ns_args(command="create", from_literal=["k=v"], from_env_file=None, yes=True)
        cli.cmd_create(api, args, "test-ns")
        api.create_namespaced_secret.assert_called_once()

    def test_labels_applied(self) -> None:
        api = _mock_api()
        args = _ns_args(
            command="create", from_literal=["k=v"],
            from_env_file=None, labels="app=web,env=prod",
        )
        cli.cmd_create(api, args, "test-ns")
        body = api.create_namespaced_secret.call_args.kwargs["body"]
        assert body.metadata.labels == {"app": "web", "env": "prod"}

    def test_json_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = _mock_api()
        args = _ns_args(
            command="create", name="new-secret",
            from_literal=["user=admin", "pass=s3cret"],
            from_env_file=None, labels=None, json=True,
        )
        cli.cmd_create(api, args, "test-ns")
        result = json.loads(capsys.readouterr().out)
        assert result["name"] == "new-secret"
        assert result["created"] is True
        assert sorted(result["keys"]) == ["pass", "user"]


# ---------------------------------------------------------------------------
# delete command
# ---------------------------------------------------------------------------


class TestCmdDelete:
    def test_deletes_secret(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = _mock_api()
        api.read_namespaced_secret.return_value = _make_secret("target", data={"k": "v"})
        cli.cmd_delete(api, _ns_args(command="delete", name="target"), "test-ns")
        out = capsys.readouterr().out
        assert "deleted" in out
        api.delete_namespaced_secret.assert_called_once_with(name="target", namespace="test-ns", _request_timeout=30)

    def test_not_found_exits(self) -> None:
        api = _mock_api()
        api.read_namespaced_secret.side_effect = Exception("404 Not Found")
        with pytest.raises(SystemExit, match="not found"):
            cli.cmd_delete(api, _ns_args(command="delete", name="missing"), "test-ns")

    def test_non_opaque_exits(self) -> None:
        api = _mock_api()
        api.read_namespaced_secret.return_value = _make_secret(
            "tls", secret_type="kubernetes.io/tls", data={"tls.crt": "x"},
        )
        with pytest.raises(SystemExit, match="not Opaque"):
            cli.cmd_delete(api, _ns_args(command="delete", name="tls"), "test-ns")

    def test_confirmation_abort(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = _mock_api()
        api.read_namespaced_secret.return_value = _make_secret("target", data={"k": "v"})
        with patch("builtins.input", return_value="n"):
            cli.cmd_delete(api, _ns_args(command="delete", name="target", yes=False), "test-ns")
        out = capsys.readouterr().out
        assert "Aborted" in out
        api.delete_namespaced_secret.assert_not_called()

    def test_yes_skips_prompt(self) -> None:
        api = _mock_api()
        api.read_namespaced_secret.return_value = _make_secret("target", data={"k": "v"})
        cli.cmd_delete(api, _ns_args(command="delete", name="target", yes=True), "test-ns")
        api.delete_namespaced_secret.assert_called_once()

    def test_json_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = _mock_api()
        api.read_namespaced_secret.return_value = _make_secret("target", data={"k": "v"})
        cli.cmd_delete(api, _ns_args(command="delete", name="target", json=True), "test-ns")
        result = json.loads(capsys.readouterr().out)
        assert result["name"] == "target"
        assert result["namespace"] == "test-ns"
        assert result["deleted"] is True

    def test_k8s_error_403(self) -> None:
        api = _mock_api()
        api.read_namespaced_secret.return_value = _make_secret("target", data={"k": "v"})
        api.delete_namespaced_secret.side_effect = Exception("403 Forbidden")
        with pytest.raises(SystemExit, match="403 Forbidden"):
            cli.cmd_delete(api, _ns_args(command="delete", name="target"), "test-ns")


# ---------------------------------------------------------------------------
# edit command
# ---------------------------------------------------------------------------


class TestCmdEdit:
    def _setup_api(self, data: dict[str, str] | None = None) -> MagicMock:
        api = _mock_api()
        api.read_namespaced_secret.return_value = _make_secret(
            "target", data=data or {"existing_key": "old_value"},
        )
        return api

    def test_set_adds_key(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = self._setup_api()
        args = _ns_args(command="edit", name="target", set_values=["new_key=new_val"], remove_keys=None)
        cli.cmd_edit(api, args, "test-ns")
        out = capsys.readouterr().out
        assert "Added" in out
        assert "new_key" in out

        body = api.replace_namespaced_secret.call_args.kwargs["body"]
        decoded = base64.b64decode(body.data["new_key"]).decode()
        assert decoded == "new_val"

    def test_set_updates_key(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = self._setup_api()
        args = _ns_args(command="edit", name="target", set_values=["existing_key=updated"], remove_keys=None)
        cli.cmd_edit(api, args, "test-ns")
        out = capsys.readouterr().out
        assert "Updated" in out

        body = api.replace_namespaced_secret.call_args.kwargs["body"]
        decoded = base64.b64decode(body.data["existing_key"]).decode()
        assert decoded == "updated"

    def test_remove_key(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = self._setup_api({"key_a": "val_a", "key_b": "val_b"})
        args = _ns_args(command="edit", name="target", set_values=None, remove_keys=["key_a"])
        cli.cmd_edit(api, args, "test-ns")
        out = capsys.readouterr().out
        assert "Removed" in out

        body = api.replace_namespaced_secret.call_args.kwargs["body"]
        assert "key_a" not in body.data
        assert "key_b" in body.data

    def test_combined_set_and_remove(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = self._setup_api({"old_key": "old_val"})
        args = _ns_args(
            command="edit", name="target",
            set_values=["new_key=new_val"], remove_keys=["old_key"],
        )
        cli.cmd_edit(api, args, "test-ns")
        out = capsys.readouterr().out
        assert "Added" in out
        assert "Removed" in out

    def test_no_changes_exits(self) -> None:
        api = _mock_api()
        args = _ns_args(command="edit", name="target", set_values=None, remove_keys=None)
        with pytest.raises(SystemExit, match="--set or --remove"):
            cli.cmd_edit(api, args, "test-ns")

    def test_not_found_exits(self) -> None:
        api = _mock_api()
        api.read_namespaced_secret.side_effect = Exception("404 Not Found")
        args = _ns_args(command="edit", name="missing", set_values=["k=v"], remove_keys=None)
        with pytest.raises(SystemExit, match=r"not found.*create"):
            cli.cmd_edit(api, args, "test-ns")

    def test_non_opaque_exits(self) -> None:
        api = _mock_api()
        api.read_namespaced_secret.return_value = _make_secret(
            "tls", secret_type="kubernetes.io/tls", data={"tls.crt": "x"},
        )
        args = _ns_args(command="edit", name="tls", set_values=["k=v"], remove_keys=None)
        with pytest.raises(SystemExit, match="not Opaque"):
            cli.cmd_edit(api, args, "test-ns")

    def test_confirmation_abort(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = self._setup_api()
        args = _ns_args(command="edit", name="target", set_values=["k=v"], remove_keys=None, yes=False)
        with patch("builtins.input", return_value="n"):
            cli.cmd_edit(api, args, "test-ns")
        out = capsys.readouterr().out
        assert "Aborted" in out
        api.replace_namespaced_secret.assert_not_called()

    def test_yes_skips_prompt(self) -> None:
        api = self._setup_api()
        args = _ns_args(command="edit", name="target", set_values=["k=v"], remove_keys=None, yes=True)
        cli.cmd_edit(api, args, "test-ns")
        api.replace_namespaced_secret.assert_called_once()

    def test_remove_nonexistent_key_warns(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = self._setup_api()
        args = _ns_args(command="edit", name="target", set_values=["k=v"], remove_keys=["ghost"])
        cli.cmd_edit(api, args, "test-ns")
        out = capsys.readouterr().out
        assert "Warning" in out
        assert "ghost" in out

    def test_json_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        api = self._setup_api({"old_key": "old_val"})
        args = _ns_args(
            command="edit", name="target", json=True,
            set_values=["new_key=new_val", "old_key=updated"], remove_keys=None,
        )
        cli.cmd_edit(api, args, "test-ns")
        result = json.loads(capsys.readouterr().out)
        assert result["added"] == ["new_key"]
        assert result["updated"] == ["old_key"]
        assert sorted(result["keys"]) == ["new_key", "old_key"]

    def test_optimistic_concurrency(self) -> None:
        """Verify that replace uses the existing resourceVersion."""
        api = self._setup_api()
        args = _ns_args(command="edit", name="target", set_values=["k=v"], remove_keys=None)
        cli.cmd_edit(api, args, "test-ns")

        body = api.replace_namespaced_secret.call_args.kwargs["body"]
        assert body.metadata.resource_version == "12345"


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestParser:
    def _parse(self, *argv: str) -> argparse.Namespace:
        parser = cli._build_parser()
        return parser.parse_args(argv)

    def test_list_defaults(self) -> None:
        args = self._parse("list")
        assert args.command == "list"
        assert args.type is None
        assert args.filter is None
        assert args.labels is None
        assert args.json is False

    def test_list_type_flag(self) -> None:
        args = self._parse("list", "--type", "kubernetes.io/tls")
        assert args.command == "list"
        assert args.type == "kubernetes.io/tls"

    def test_reveal_args(self) -> None:
        args = self._parse("reveal", "my-secret")
        assert args.command == "reveal"
        assert args.name == "my-secret"

    def test_create_from_literal(self) -> None:
        args = self._parse("create", "new", "--from-literal", "k=v", "--from-literal", "k2=v2")
        assert args.command == "create"
        assert args.name == "new"
        assert args.from_literal == ["k=v", "k2=v2"]

    def test_create_from_env_file(self) -> None:
        args = self._parse("create", "new", "--from-env-file", "/tmp/test.env")
        assert args.from_env_file == "/tmp/test.env"

    def test_edit_set_and_remove(self) -> None:
        args = self._parse("edit", "target", "--set", "k=v", "--remove", "old_key")
        assert args.command == "edit"
        assert args.set_values == ["k=v"]
        assert args.remove_keys == ["old_key"]

    def test_namespace_flag(self) -> None:
        args = self._parse("-n", "my-ns", "list")
        assert args.namespace == "my-ns"

    def test_json_flag(self) -> None:
        args = self._parse("--json", "list")
        assert args.json is True

    def test_yes_flag_create(self) -> None:
        args = self._parse("create", "new", "--from-literal", "k=v", "-y")
        assert args.yes is True

    def test_yes_flag_edit(self) -> None:
        args = self._parse("edit", "target", "--set", "k=v", "-y")
        assert args.yes is True

    def test_delete_args(self) -> None:
        args = self._parse("delete", "my-secret")
        assert args.command == "delete"
        assert args.name == "my-secret"
        assert args.yes is False

    def test_delete_yes_flag(self) -> None:
        args = self._parse("delete", "my-secret", "-y")
        assert args.yes is True


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_parse_key_value_valid(self) -> None:
        assert cli._parse_key_value("user=admin") == ("user", "admin")

    def test_parse_key_value_with_equals_in_value(self) -> None:
        assert cli._parse_key_value("conn=host=db;port=5432") == ("conn", "host=db;port=5432")

    def test_parse_key_value_no_equals(self) -> None:
        with pytest.raises(SystemExit, match="Invalid literal format"):
            cli._parse_key_value("noequals")

    def test_parse_key_value_empty_key(self) -> None:
        with pytest.raises(SystemExit, match="Key cannot be empty"):
            cli._parse_key_value("=value")

    def test_parse_key_value_invalid_key(self) -> None:
        with pytest.raises(SystemExit, match="Invalid secret key name"):
            cli._parse_key_value("bad key=val")

    def test_parse_labels(self) -> None:
        result = cli._parse_labels("app=web, env=prod")
        assert result == {"app": "web", "env": "prod"}

    def test_parse_labels_invalid(self) -> None:
        with pytest.raises(SystemExit, match="Invalid label format"):
            cli._parse_labels("noequalssign")

    def test_format_age_days(self) -> None:
        from datetime import datetime, timedelta
        created = datetime.now(UTC) - timedelta(days=3)
        assert cli._format_age(created) == "3d"

    def test_format_age_hours(self) -> None:
        from datetime import datetime, timedelta
        created = datetime.now(UTC) - timedelta(hours=5)
        assert cli._format_age(created) == "5h"

    def test_format_age_none(self) -> None:
        assert cli._format_age(None) == "?"

    def test_load_env_file_missing(self) -> None:
        with pytest.raises(SystemExit, match="File not found"):
            cli._load_env_file("/nonexistent/path")

    def test_load_env_file_invalid_line(self, tmp_path: object) -> None:
        import pathlib
        env_file = pathlib.Path(str(tmp_path)) / "bad.env"
        env_file.write_text("NO_EQUALS_HERE\n")
        with pytest.raises(SystemExit, match="expected KEY=VALUE"):
            cli._load_env_file(str(env_file))
