"""Offline HTTP contract and CLI tests for Quay discovery."""

from __future__ import annotations

import argparse
import json
from unittest.mock import patch

import httpx
import pytest

from autox_tools.config import cli as config_cli
from autox_tools.config._loader import load_config, resolve
from autox_tools.config._models import QuayConfig
from autox_tools.quay import cli
from autox_tools.quay._client import ImageNotFoundError, QuayClient, QuayError, resolve_settings
from autox_tools.quay._filters import TagFilters
from autox_tools.quay._references import parse_reference, registry_host

DIGEST = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64


def tag(name, digest=DIGEST, **extra):
    return {"name": name, "manifest_digest": digest, "start_ts": 1700000000, **extra}


@pytest.fixture
def client_factory():
    clients = []

    def make(handler, token=""):
        client = QuayClient(QuayConfig(token=token), transport=httpx.MockTransport(handler))
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.close()


def test_filtered_pagination_and_stable_ties(client_factory):
    requests = []
    pages = [
        [tag("sha256-signature"), tag("release")],
        [tag("pr-first"), tag("pr-second"), tag("pr-first", OTHER)],
        [tag("pr-third")],
    ]

    def handler(request):
        requests.append(request)
        assert request.url.params["onlyActiveTags"] == "true"
        page = int(request.url.params["page"])
        return httpx.Response(200, json={"tags": pages[page - 1], "has_additional": page < 3})

    result = client_factory(handler).tags("org/nested/repo", TagFilters(prefix="pr-"), limit=2)
    assert [item["tag"] for item in result["items"]] == ["pr-first", "pr-second"]
    assert result["items"][0]["digest"] == DIGEST
    assert result["items"][0]["updated_at"] == "2023-11-14T22:13:20+00:00"
    assert result["truncated"] is True
    assert len(requests) == 3
    assert requests[0].url.path == "/api/v1/repository/org/nested/repo/tag/"


def test_limit_after_filters_without_false_truncation(client_factory):
    pages = [[tag("pr-one")], [tag("other"), tag("pr-expired", end_ts=1)]]

    def handler(request):
        page = int(request.url.params["page"])
        return httpx.Response(200, json={"tags": pages[page - 1], "has_additional": page == 1})

    result = client_factory(handler).tags("org/repo", TagFilters(prefix="pr-"), 1)
    assert result["returned"] == 1
    assert result["truncated"] is False


def test_all_tags_and_server_order(client_factory):
    def handler(request):
        page = int(request.url.params["page"])
        return httpx.Response(200, json={"tags": [tag(f"tag-{page}", start_ts=10 - page)],
                                        "has_additional": page < 3})

    result = client_factory(handler).tags("org/repo", TagFilters(), limit=None)
    assert [item["tag"] for item in result["items"]] == ["tag-1", "tag-2", "tag-3"]
    assert result["truncated"] is False


@pytest.mark.parametrize("name", ["sha256-abc", "image.sig", "image.att", "image.sbom", "image.git"])
def test_artifacts(name):
    assert not TagFilters().accepts(name)
    assert TagFilters(include_artifacts=True).accepts(name)


@pytest.mark.parametrize("name,accepted", [
    ("odh-pr-2fd3680ab4d60016081e8b390d6e46e9256e030b", True),
    ("odh-pr-2FD3680", True), ("odh-pr-12fd3680", False),
    ("odh-pr-x2fd3680", False), ("odh-pr-2fd3680z", False),
    ("release-2fd3680", False), ("ODH-pr-2fd3680", False),
])
def test_combined_filters(name, accepted):
    assert TagFilters(prefix="odh-", match="*-pr-*", commit="2fd3680").accepts(name) is accepted


@pytest.mark.parametrize("value,namespace,repository", [
    ("repo", "org", "org/repo"),
    ("org/team/repo", "other", "org/team/repo"),
    ("quay.io/org/team/repo", "", "org/team/repo"),
])
def test_repository_parsing(value, namespace, repository):
    assert parse_reference(value, "quay.io", namespace).repository == repository


def test_image_parsing():
    assert parse_reference("localhost:8080/org/repo:pr", "localhost:8080", image=True).tag == "pr"
    ref = parse_reference(f"quay.io/org/team/repo@{DIGEST}", "quay.io", image=True)
    assert ref.digest == DIGEST and ref.tag is None


@pytest.mark.parametrize("value,image", [
    ("repo", False), ("other.io/org/repo", False), ("quay.io/repo", False),
    ("org/repo", True), ("org/repo:tag", False), ("org/repo@sha256:abc", True),
    ("org/../repo", False), ("org//repo", False), ("https://quay.io/org/repo", False),
    ("org/repo:bad?tag", True), ("org/repo:tag@" + DIGEST, True),
])
def test_invalid_references(value, image):
    with pytest.raises(ValueError):
        parse_reference(value, "quay.io", image=image)


@pytest.mark.parametrize("url", ["quay.io", "https://user:pass@quay.io", "https://quay.io/api/v1",
                                 "https://quay.io?token=secret", "https://quay.io#x", "https://quay.io:bad"])
def test_invalid_registry_origin(url):
    with pytest.raises(ValueError):
        registry_host(url)


@pytest.mark.parametrize("token", ["", "my-api-token"])
def test_auth_and_timeout(client_factory, token):
    def handler(request):
        assert request.headers.get("authorization") == (f"Bearer {token}" if token else None)
        assert request.extensions["timeout"]["read"] == 30
        return httpx.Response(200, json={})

    client_factory(handler, token).get("find/repositories")


def test_redirect_not_followed(client_factory):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://other.io/api"})

    with pytest.raises(QuayError, match="302"):
        client_factory(handler, "secret").get("find/repositories")
    assert len(requests) == 1


@pytest.mark.parametrize("failure", [429, 500, 503, "connection", "timeout"])
@pytest.mark.parametrize("recover", [True, False])
def test_retries(client_factory, failure, recover):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 3 and recover:
            return httpx.Response(200, json={"ok": True})
        if failure == "connection":
            raise httpx.ConnectError("failed", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("failed", request=request)
        return httpx.Response(failure)

    client = client_factory(handler)
    with patch("autox_tools.quay._client.time.sleep") as sleep:
        if recover:
            assert client.get("find/repositories") == {"ok": True}
        else:
            with pytest.raises(QuayError):
                client.get("find/repositories")
    assert len(calls) == 3
    assert sleep.call_count == 2


@pytest.mark.parametrize("status", [401, 403, 404, 400])
def test_permanent_errors_not_retried(client_factory, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="untrusted response containing secrets")

    with pytest.raises(QuayError) as error:
        client_factory(handler).get("find/repositories")
    assert error.value.status == status
    assert "secrets" not in str(error.value)
    assert len(calls) == 1


def test_exact_lookup_manifest_and_moved_tag(client_factory):
    requests = []
    manifest_digests = iter([DIGEST, OTHER])

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/tag/"):
            assert request.url.params["filter_tag_name"] == "eq:odh-pr"
            return httpx.Response(200, json={"tags": [tag("odh-pr-extra"), tag("odh-pr", next(manifest_digests))]})
        return httpx.Response(200, json={
            "is_manifest_list": True,
            "manifest_data": json.dumps({"mediaType": "application/vnd.oci.image.index.v1+json", "manifests": [
                {"digest": DIGEST, "platform": {"os": "linux", "architecture": "amd64"}},
                {"digest": OTHER},
            ]}),
        })

    client = client_factory(handler)
    reference = parse_reference("org/repo:odh-pr", "quay.io", image=True)
    first = client.latest(reference)
    second = client.latest(reference)
    assert first["digest"] == DIGEST and second["digest"] == OTHER
    assert requests[1].url.path.endswith(DIGEST) and requests[3].url.path.endswith(OTHER)
    assert first["size"] is None
    assert first["platforms"][0]["architecture"] == "amd64"
    assert first["platforms"][1]["os"] is None
    assert first["media_type"] == "application/vnd.oci.image.index.v1+json"


def test_latest_digest_optional_metadata(client_factory):
    def handler(request):
        assert "/manifest/" in request.url.path
        return httpx.Response(200, json={})

    result = client_factory(handler).latest(parse_reference(f"org/repo@{DIGEST}", "quay.io", image=True))
    assert result["digest_ref"] == f"quay.io/org/repo@{DIGEST}"
    for field in ("tag", "tag_ref", "updated_at", "size", "media_type", "platforms"):
        assert result[field] is None


def test_latest_artifact(client_factory):
    def handler(request):
        return httpx.Response(200, json={"tags": [tag("sha256-test.sig")]})

    result = client_factory(handler).latest(parse_reference("org/repo:sha256-test.sig", "quay.io", image=True))
    assert result["tag"] == "sha256-test.sig"


@pytest.mark.parametrize("response", [httpx.Response(200, json={"tags": []}), httpx.Response(404)])
def test_latest_missing(client_factory, response):
    with pytest.raises(ImageNotFoundError):
        client_factory(lambda request: response).latest(parse_reference("org/repo:missing", "quay.io", image=True))


def test_latest_history_paginated_exact_tag_and_reversions(client_factory):
    requests = []
    # Reassigning the same digest later is a separate history entry. Timestamp
    # ties retain the server order, and repeated page boundary rows are skipped.
    current = tag("rolling", start_ts=30)
    previous = tag("rolling", OTHER, start_ts=20, end_ts=30)
    reverted = tag("rolling", start_ts=20, end_ts=20)
    pages = [[tag("rolling-extra")], [current, previous], [previous, reverted]]

    def handler(request):
        requests.append(request)
        if "/manifest/" in request.url.path:
            return httpx.Response(200, json={"manifest_data": {"mediaType": "image/type"}})
        assert request.url.params["onlyActiveTags"] == "false"
        assert request.url.params["filter_tag_name"] == "eq:rolling"
        page = int(request.url.params["page"])
        return httpx.Response(200, json={"tags": pages[page - 1], "has_additional": page < 3})

    reference = parse_reference("org/repo:rolling", "quay.io", image=True)
    result = client_factory(handler).latest(reference, 3)
    assert [item["digest"] for item in result["items"]] == [DIGEST, OTHER, DIGEST]
    assert [item["is_active"] for item in result["items"]] == [True, False, False]
    assert result["items"][1]["ended_at"] == "1970-01-01T00:00:30+00:00"
    assert all(item["media_type"] == "image/type" for item in result["items"])
    assert result["returned"] == 3 and result["truncated"] is False
    assert len(requests) == 6


def test_latest_history_truncation_and_manifest_fetch_limit(client_factory):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"tags": [
            tag("rolling", start_ts=30), tag("rolling", OTHER, start_ts=20, end_ts=30),
            tag("rolling", start_ts=10, end_ts=20),
        ]})

    result = client_factory(handler).latest(parse_reference("org/repo:rolling", "quay.io", image=True), 2)
    assert result["returned"] == 2 and result["truncated"] is True
    assert len(requests) == 3  # One history page and only the two requested manifests.


def test_latest_history_fewer_than_requested(client_factory):
    client = client_factory(lambda r: httpx.Response(200, json={"tags": [tag("rolling")]}))
    result = client.latest(parse_reference("org/repo:rolling", "quay.io", image=True), 5)
    assert result["returned"] == 1 and result["truncated"] is False
    assert result["items"][0]["manifest_available"] is True


def test_latest_current_excludes_old_assignments(client_factory):
    def handler(request):
        if "/manifest/" in request.url.path:
            assert request.url.path.endswith(DIGEST)
            return httpx.Response(200, json={})
        assert request.url.params["onlyActiveTags"] == "true"
        return httpx.Response(200, json={"tags": [
            tag("rolling-extra"), tag("rolling", OTHER, end_ts=1), tag("rolling"),
        ]})

    result = client_factory(handler).latest(parse_reference("org/repo:rolling", "quay.io", image=True))
    assert result["digest"] == DIGEST and result["is_active"]


def test_latest_history_manifest_removed(client_factory):
    def handler(request):
        if request.url.path.endswith(OTHER):
            return httpx.Response(404)
        return httpx.Response(200, json={"tags": [tag("rolling"), tag("rolling", OTHER, end_ts=1)]})

    result = client_factory(handler).latest(parse_reference("org/repo:rolling", "quay.io", image=True), 2)
    current, previous = result["items"]
    assert current["manifest_available"] is True
    assert previous["manifest_available"] is False
    assert previous["digest_ref"].endswith(OTHER)
    assert previous["platforms"] is None


def test_latest_history_auth_errors_are_fatal(client_factory):
    def handler(request):
        if "/manifest/" in request.url.path:
            return httpx.Response(403)
        return httpx.Response(200, json={"tags": [tag("rolling", end_ts=1)]})

    with pytest.raises(QuayError, match="denied access"):
        client_factory(handler).latest(parse_reference("org/repo:rolling", "quay.io", image=True), 2)


def test_latest_history_repeated_page_fails(client_factory):
    client = client_factory(lambda r: httpx.Response(200, json={"tags": [tag("rolling")], "has_additional": True}))
    with pytest.raises(QuayError, match="pagination stopped advancing"):
        client.latest(parse_reference("org/repo:rolling", "quay.io", image=True), 2)


def test_latest_deleted_tag_history(client_factory):
    client = client_factory(lambda r: httpx.Response(200, json={"tags": [tag("rolling", end_ts=1)]}))
    reference = parse_reference("org/repo:rolling", "quay.io", image=True)
    with pytest.raises(ImageNotFoundError, match="No active tag"):
        client.latest(reference)
    assert client.latest(reference, 2)["items"][0]["is_active"] is False


def repository(namespace, name):
    return {"kind": "repository", "namespace": {"name": namespace}, "name": name}


def test_search_org_pagination_limit(client_factory):
    pages = [[repository("other", "match")], [repository("org", "nested/match")], [repository("org", "match-2")]]

    def handler(request):
        assert request.url.params["query"] == "org/match"
        page = int(request.url.params["page"])
        return httpx.Response(200, json={"results": pages[page - 1], "has_additional": page < 3})

    result = client_factory(handler).search("match", "org", limit=1)
    assert result["items"][0]["repository"] == "org/nested/match"
    assert result["truncated"] and result["search_may_be_capped"]


def test_search_repeated_page_cap(client_factory):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"results": [repository("org", "repo")], "has_additional": True})

    result = client_factory(handler).search("repo", None, limit=None)
    assert len(calls) == 2
    assert result["returned"] == 1 and result["truncated"]


def test_search_exposed_results_not_complete_discovery(client_factory):
    result = client_factory(lambda r: httpx.Response(200, json={"results": []})).search("repo", None)
    assert result == {"items": [], "returned": 0, "truncated": False, "search_may_be_capped": True}


def run_cli(monkeypatch, client, arguments):
    monkeypatch.setattr(cli, "resolve", lambda *args: QuayConfig())
    monkeypatch.setattr(cli, "QuayClient", lambda settings: client)
    monkeypatch.setattr("sys.argv", ["quay", *arguments])
    cli.main()


@pytest.mark.parametrize("arguments,expected", [
    (["latest", "org/repo:current", "--output", "ref"], "quay.io/org/repo:current\n"),
    (["latest", "org/repo:current", "--output", "digest"], f"quay.io/org/repo@{DIGEST}\n"),
    (["latest", f"org/repo@{DIGEST}", "--output", "digest"], f"quay.io/org/repo@{DIGEST}\n"),
])
def test_cli_reference_output(client_factory, monkeypatch, capsys, arguments, expected):
    client = client_factory(lambda r: httpx.Response(200, json={"tags": [tag("current")]}))
    run_cli(monkeypatch, client, arguments)
    output = capsys.readouterr()
    assert output.out == expected and output.err == ""


@pytest.mark.parametrize("command", ["tags", "latest", "search"])
@pytest.mark.parametrize("explicit_json", [False, True])
def test_cli_json_default(client_factory, monkeypatch, capsys, command, explicit_json):
    def handler(request):
        if "/manifest/" in request.url.path:
            return httpx.Response(200, json={"manifest_data": {
                "mediaType": "application/vnd.oci.image.index.v1+json",
                "manifests": [{"digest": OTHER, "platform": {"os": "linux", "architecture": "amd64"}}],
            }})
        return httpx.Response(200, json={"tags": [tag("current")], "results": []})

    client = client_factory(handler)
    arguments = [command, "org/repo:current" if command == "latest" else "org/repo"]
    if explicit_json:
        arguments.append("--json")
    run_cli(monkeypatch, client, arguments)
    output = capsys.readouterr()
    result = json.loads(output.out)
    if command in {"tags", "search"}:
        assert "items" in result
    else:
        assert result["digest"] == DIGEST
        assert result["media_type"] == "application/vnd.oci.image.index.v1+json"
        assert result["platforms"][0]["architecture"] == "amd64"
    assert output.err == ""


@pytest.mark.parametrize("command,code", [("tags", 0), ("search", 0), ("latest", 3)])
def test_cli_empty_results(client_factory, monkeypatch, capsys, command, code):
    client = client_factory(lambda r: httpx.Response(200, json={"tags": [], "results": []}))
    args = [command, "org/repo:missing" if command == "latest" else "org/repo"]
    if code:
        with pytest.raises(SystemExit) as exc:
            run_cli(monkeypatch, client, args)
        assert exc.value.code == code
        assert capsys.readouterr().out == ""
    else:
        run_cli(monkeypatch, client, args)
        assert json.loads(capsys.readouterr().out)["items"] == []


@pytest.mark.parametrize("command", ["tags", "latest", "search"])
def test_cli_table_is_explicit(client_factory, monkeypatch, capsys, command):
    client = client_factory(lambda r: httpx.Response(200, json={"tags": [tag("current")], "results": []}))
    run_cli(monkeypatch, client, [command, "org/repo:current" if command == "latest" else "org/repo",
                                  "--output", "table"])
    assert ("Digest" if command != "search" else "Repository") in capsys.readouterr().out


def test_cli_auth_failure(client_factory, monkeypatch, capsys):
    client = client_factory(lambda r: httpx.Response(401))
    with pytest.raises(SystemExit) as exc:
        run_cli(monkeypatch, client, ["latest", "org/repo:current"])
    assert exc.value.code == 1
    output = capsys.readouterr()
    assert output.out == "" and "token" in output.err


@pytest.mark.parametrize("limit_flag", ["-n", "--limit"])
def test_cli_history_json(client_factory, monkeypatch, capsys, limit_flag):
    client = client_factory(lambda r: httpx.Response(200, json={
        "tags": [tag("rolling"), tag("rolling", OTHER, end_ts=1)],
    }))
    run_cli(monkeypatch, client, ["latest", "org/repo:rolling", limit_flag, "2"])
    result = json.loads(capsys.readouterr().out)
    assert result["returned"] == 2 and not result["truncated"]
    assert [item["digest"] for item in result["items"]] == [DIGEST, OTHER]


def test_cli_history_digest_lines(client_factory, monkeypatch, capsys):
    client = client_factory(lambda r: httpx.Response(200, json={
        "tags": [tag("rolling"), tag("rolling", OTHER, end_ts=1)],
    }))
    run_cli(monkeypatch, client, ["latest", "org/repo:rolling", "-n", "2", "--output", "digest"])
    output = capsys.readouterr()
    assert output.out.splitlines() == [f"quay.io/org/repo@{DIGEST}", f"quay.io/org/repo@{OTHER}"]
    assert output.err == ""


@pytest.mark.parametrize("arguments", [
    ["tags", "org/repo", "--limit", "0"], ["tags", "org/repo", "--commit", "invalid"],
    ["latest", "other.io/org/repo:current"], ["latest", "org/repo"],
    ["latest", "org/repo:current", "--json", "--output", "digest"],
    ["latest", f"org/repo@{DIGEST}", "--output", "ref"],
    ["latest", "org/repo:current", "--prefix", "current"], ["inspect", "org/repo:current"],
    ["latest", "org/repo:current", "-n", "0"], ["latest", "org/repo:current", "-n", "invalid"],
    ["latest", f"org/repo@{DIGEST}", "-n", "2"],
    ["latest", "org/repo:current", "-n", "2", "--output", "ref"],
])
def test_cli_invalid_arguments_before_network(monkeypatch, arguments):
    monkeypatch.setattr(cli, "resolve", lambda *args: QuayConfig(token="secret"))
    monkeypatch.setattr("sys.argv", ["quay", *arguments])
    with patch.object(cli, "QuayClient") as factory, pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    factory.assert_not_called()


@pytest.mark.parametrize("args", [
    ["--json", "-p", "dev", "-t", "registry", "tags", "org/repo"],
    ["tags", "org/repo", "--json", "-p", "dev", "-t", "registry"],
    ["--json", "-p", "dev", "tags", "org/repo", "-t", "registry"],
    ["--json", "-p", "dev", "-t", "registry", "latest", "org/repo:current", "-n", "2"],
    ["latest", "org/repo:current", "--json", "-p", "dev", "-t", "registry"],
])
def test_global_options_both_positions(args):
    parsed = cli._build_parser().parse_args(args)
    assert parsed.json and parsed.profile == "dev" and parsed.target == "registry"


def test_environment_fallback_and_profile_isolation(monkeypatch):
    monkeypatch.setenv("QUAY_BASE_URL", "https://private.example")
    monkeypatch.setenv("QUAY_NAMESPACE", "org")
    monkeypatch.setenv("QUAY_TOKEN", "env-secret")
    with patch("autox_tools.quay._client.load_dotenv"):
        settings = resolve_settings()
    assert settings == QuayConfig("https://private.example", "org", "env-secret")
    assert resolve_settings(QuayConfig()) == QuayConfig()


def test_config_integration(tmp_path, monkeypatch, capsys):
    path = tmp_path / ".autox.yaml"
    path.write_text("""defaults:
  profile: dev
profiles:
  dev:
    quay: odh
quay:
  odh:
    namespace: opendatahub
    token: ${QUAY_TEST_TOKEN}
  other:
    base_url: https://registry.example
""")
    monkeypatch.setenv("QUAY_TEST_TOKEN", "private-api-token")
    monkeypatch.delenv("AUTOX_PROFILE", raising=False)
    cfg = load_config(path)
    assert cfg.quay["odh"].base_url == "https://quay.io"
    with patch("autox_tools.config._loader.load_config", return_value=cfg):
        assert resolve("quay", argparse.Namespace()).namespace == "opendatahub"
        assert resolve("quay", argparse.Namespace(profile="dev", target="other")).base_url == "https://registry.example"
        with pytest.raises(SystemExit, match="Unknown quay target"):
            resolve("quay", argparse.Namespace(target="missing"))
    with patch.object(config_cli, "load_config", return_value=cfg):
        config_cli.cmd_list(argparse.Namespace())
        config_cli.cmd_show(argparse.Namespace(profile_name="dev"))
    output = capsys.readouterr().out
    assert "quay configs:" in output and "quay -> odh" in output
    assert "private-api-token" not in output and "***" in output
    with patch.object(config_cli, "find_config", return_value=path):
        config_cli.cmd_validate(argparse.Namespace())
    assert "2 quay" in capsys.readouterr().out
    path.write_text("profiles:\n  dev:\n    quay: missing\n")
    with patch.object(config_cli, "find_config", return_value=path), pytest.raises(SystemExit) as exc:
        config_cli.cmd_validate(argparse.Namespace())
    assert exc.value.code == 1


def test_unmapped_profile_fallback():
    from autox_tools.config._models import AutoxConfig, Profile

    cfg = AutoxConfig(default_profile="dev", profiles={"dev": Profile()})
    with patch("autox_tools.config._loader.load_config", return_value=cfg), patch.dict("os.environ", {}, clear=True):
        assert resolve("quay", argparse.Namespace()) is None
