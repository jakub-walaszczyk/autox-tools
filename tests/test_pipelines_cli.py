"""Unit tests for the pipelines CLI tool.

All tests mock KFP and Kubernetes clients -- no cluster access required.
"""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from autox_tools.pipelines import _filters
from autox_tools.pipelines import cli


# ---------------------------------------------------------------------------
# _filters.py tests
# ---------------------------------------------------------------------------

class TestIsUserTask:
    @pytest.mark.parametrize("name", [
        "indexing",
        "optimization",
        "evaluation",
        "data-preprocessing",
        "train-model",
    ])
    def test_accepts_user_tasks(self, name):
        assert _filters.is_user_task(name) is True

    @pytest.mark.parametrize("name", [
        "indexing-driver",
        "optimization-driver",
        "some-component-driver",
    ])
    def test_rejects_driver_suffix(self, name):
        assert _filters.is_user_task(name) is False

    @pytest.mark.parametrize("name", ["root", "Root", "ROOT", "executor", "Executor"])
    def test_rejects_reserved_names(self, name):
        assert _filters.is_user_task(name) is False

    @pytest.mark.parametrize("name", [
        "for-loop-1",
        "for-loop-items",
        "iteration-item-0",
        "iteration-iterations-3",
    ])
    def test_rejects_loop_prefixes(self, name):
        assert _filters.is_user_task(name) is False

    def test_rejects_uuid_names(self):
        assert _filters.is_user_task("a1b2c3d4-e5f6-7890-abcd-ef1234567890") is False

    def test_rejects_pipeline_name_prefix(self):
        assert _filters.is_user_task("my-pipeline-abc", pipeline_name="my-pipeline") is False

    def test_accepts_similar_but_different_prefix(self):
        assert _filters.is_user_task("other-pipeline-abc", pipeline_name="my-pipeline") is True

    def test_strips_whitespace(self):
        assert _filters.is_user_task("  indexing  ") is True
        assert _filters.is_user_task("  root  ") is False

    def test_no_pipeline_name(self):
        assert _filters.is_user_task("my-pipeline-abc", pipeline_name=None) is True


# ---------------------------------------------------------------------------
# _k8s.py tests
# ---------------------------------------------------------------------------

class TestDeriveK8sApiUrl:
    def setup_method(self):
        self._env_patch = patch.dict(os.environ, {}, clear=False)
        self._env_patch.start()
        for key in ("K8S_API_URL", "K8S_API_PORT"):
            os.environ.pop(key, None)

    def teardown_method(self):
        self._env_patch.stop()

    def test_standard_ocp_route(self):
        from autox_tools.pipelines._k8s import _derive_k8s_api_url
        url = _derive_k8s_api_url("https://ds-pipeline-dspa.apps.ocp-cluster.example.com/")
        assert url == "https://api.ocp-cluster.example.com:6443"

    def test_rosa_route(self):
        from autox_tools.pipelines._k8s import _derive_k8s_api_url
        url = _derive_k8s_api_url("https://ds-pipeline-dspa.apps.rosa.my-cluster.p3.openshiftapps.com/")
        assert url == "https://api.my-cluster.p3.openshiftapps.com:443"

    def test_explicit_override(self):
        os.environ["K8S_API_URL"] = "https://custom-api.example.com:9443"
        from autox_tools.pipelines._k8s import _derive_k8s_api_url
        url = _derive_k8s_api_url("https://anything.apps.cluster.example.com/")
        assert url == "https://custom-api.example.com:9443"

    def test_custom_port_override(self):
        os.environ["K8S_API_PORT"] = "8443"
        from autox_tools.pipelines._k8s import _derive_k8s_api_url
        url = _derive_k8s_api_url("https://route.apps.cluster.example.com/")
        assert url == "https://api.cluster.example.com:8443"

    def test_invalid_url_exits(self):
        from autox_tools.pipelines._k8s import _derive_k8s_api_url
        with pytest.raises(SystemExit, match="Cannot derive K8S API URL"):
            _derive_k8s_api_url("https://some-random-url.example.com/")

    def test_no_trailing_slash(self):
        from autox_tools.pipelines._k8s import _derive_k8s_api_url
        url = _derive_k8s_api_url("https://ds-pipeline.apps.cluster.example.com")
        assert url == "https://api.cluster.example.com:6443"


# ---------------------------------------------------------------------------
# _kfp.py tests
# ---------------------------------------------------------------------------

class TestKfpConnect:
    def test_missing_env_vars_exits(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("autox_tools.pipelines._kfp.load_dotenv"), \
             patch("autox_tools.pipelines._kfp.find_dotenv", return_value=""), \
             pytest.raises(SystemExit, match="Missing required environment variables"):
            from autox_tools.pipelines._kfp import connect
            connect()

    def test_connect_enforces_trailing_slash(self):
        env = {
            "RHOAI_KFP_URL": "https://kfp.example.com",
            "RHOAI_TOKEN": "tok",
            "RHOAI_PROJECT_NAME": "ns",
        }
        with patch.dict(os.environ, env, clear=True), \
             patch("autox_tools.pipelines._kfp.load_dotenv"), \
             patch("autox_tools.pipelines._kfp.find_dotenv", return_value=""), \
             patch("kfp.Client") as mock_client:
            from autox_tools.pipelines._kfp import connect
            connect()
            _, kwargs = mock_client.call_args
            assert kwargs["host"].endswith("/")

    def test_connect_ssl_disabled(self):
        env = {
            "RHOAI_KFP_URL": "https://kfp.example.com/",
            "RHOAI_TOKEN": "tok",
            "RHOAI_PROJECT_NAME": "ns",
            "KFP_VERIFY_SSL": "false",
        }
        with patch.dict(os.environ, env, clear=True), \
             patch("autox_tools.pipelines._kfp.load_dotenv"), \
             patch("autox_tools.pipelines._kfp.find_dotenv", return_value=""), \
             patch("kfp.Client") as mock_client:
            from autox_tools.pipelines._kfp import connect
            connect()
            _, kwargs = mock_client.call_args
            assert kwargs["verify_ssl"] is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestFormatDuration:
    @pytest.mark.parametrize("seconds,expected", [
        (0, "0s"),
        (45, "45s"),
        (60, "1m0s"),
        (90, "1m30s"),
        (3661, "1h1m1s"),
        (7200, "2h0m0s"),
    ])
    def test_formatting(self, seconds, expected):
        assert cli._format_duration(seconds) == expected


# ---------------------------------------------------------------------------
# Fixtures for KFP mock objects
# ---------------------------------------------------------------------------

def _make_run(state="Succeeded", error=None, pipeline_name="my-pipeline", run_id="abc-123",
              created_at=None, finished_at=None, task_details=None):
    """Build a mock KFP run response."""
    if created_at is None:
        created_at = datetime(2025, 5, 20, 10, 0, 0, tzinfo=UTC)
    if finished_at is None and state.lower() in {"succeeded", "failed", "error"}:
        finished_at = created_at + timedelta(minutes=30)

    run_obj = SimpleNamespace(
        run_id=run_id,
        state=state,
        created_at=created_at,
        finished_at=finished_at,
        error=error,
        display_name="test-run",
        name="test-run",
        pipeline_spec={"pipeline_name": pipeline_name},
        runtime_config=None,
    )

    run_details = SimpleNamespace(
        task_details=task_details or [],
    )

    return SimpleNamespace(run=run_obj, run_details=run_details)


def _make_task(display_name, state="Succeeded", error=None, pod_name=None):
    """Build a mock KFP task detail."""
    return SimpleNamespace(
        display_name=display_name,
        state=state,
        error=error,
        child_tasks=[],
        pod_name=pod_name,
    )


# ---------------------------------------------------------------------------
# cmd_status
# ---------------------------------------------------------------------------

class TestCmdStatus:
    def test_human_output(self, capsys):
        tasks = [
            _make_task("indexing", "Succeeded"),
            _make_task("evaluation", "Failed", error="OOM killed"),
            _make_task("indexing-driver", "Succeeded"),
        ]
        run = _make_run(state="Failed", error="Pipeline failed", task_details=tasks)
        client = MagicMock()
        client.get_run.return_value = run

        args = argparse.Namespace(run_id="abc-123", json=False)
        cli.cmd_status(client, args)

        out = capsys.readouterr().out
        assert "abc-123" in out
        assert "Failed" in out
        assert "indexing" in out
        assert "evaluation" in out
        assert "OOM killed" in out
        assert "indexing-driver" not in out

    def test_json_output(self, capsys):
        tasks = [_make_task("indexing", "Succeeded")]
        run = _make_run(state="Succeeded", task_details=tasks)
        client = MagicMock()
        client.get_run.return_value = run

        args = argparse.Namespace(run_id="abc-123", json=True)
        cli.cmd_status(client, args)

        import json
        result = json.loads(capsys.readouterr().out)
        assert result["run_id"] == "abc-123"
        assert result["state"] == "Succeeded"
        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["name"] == "indexing"

    def test_filters_scaffolding_tasks(self, capsys):
        tasks = [
            _make_task("root", "Succeeded"),
            _make_task("executor", "Succeeded"),
            _make_task("for-loop-1", "Succeeded"),
            _make_task("real-task", "Succeeded"),
        ]
        run = _make_run(state="Succeeded", task_details=tasks)
        client = MagicMock()
        client.get_run.return_value = run

        args = argparse.Namespace(run_id="test-id", json=True)
        cli.cmd_status(client, args)

        import json
        result = json.loads(capsys.readouterr().out)
        task_names = [t["name"] for t in result["tasks"]]
        assert task_names == ["real-task"]

    def test_truncates_error_in_human_output(self, capsys):
        long_error = "x" * 300
        run = _make_run(state="Failed", error=long_error)
        client = MagicMock()
        client.get_run.return_value = run

        args = argparse.Namespace(run_id="test-id", json=False)
        cli.cmd_status(client, args)

        out = capsys.readouterr().out
        assert "..." in out
        assert "x" * 201 not in out


# ---------------------------------------------------------------------------
# cmd_list
# ---------------------------------------------------------------------------

class TestCmdList:
    def _make_runs_response(self, count=3, state="Succeeded"):
        runs = []
        base_time = datetime(2025, 5, 20, 10, 0, 0, tzinfo=UTC)
        for i in range(count):
            runs.append(SimpleNamespace(
                run_id=f"run-{i}",
                display_name=f"pipeline-run-{i}",
                state=state,
                created_at=base_time - timedelta(hours=i),
                finished_at=base_time - timedelta(hours=i) + timedelta(minutes=30),
            ))
        return SimpleNamespace(runs=runs)

    def test_human_output(self, capsys):
        client = MagicMock()
        client.list_runs.return_value = self._make_runs_response(3)
        args = argparse.Namespace(limit=20, experiment=None, state=None, json=False)
        cli.cmd_list(client, args)
        out = capsys.readouterr().out
        assert "run-0" in out
        assert "run-2" in out
        assert "3 run(s)" in out

    def test_json_output(self, capsys):
        client = MagicMock()
        client.list_runs.return_value = self._make_runs_response(2)
        args = argparse.Namespace(limit=20, experiment=None, state=None, json=True)
        cli.cmd_list(client, args)

        import json
        result = json.loads(capsys.readouterr().out)
        assert len(result) == 2
        assert result[0]["run_id"] == "run-0"

    def test_state_filter(self, capsys):
        client = MagicMock()
        resp = self._make_runs_response(3)
        resp.runs[1].state = "Failed"
        client.list_runs.return_value = resp

        args = argparse.Namespace(limit=20, experiment=None, state="failed", json=True)
        cli.cmd_list(client, args)

        import json
        result = json.loads(capsys.readouterr().out)
        assert len(result) == 1
        assert result[0]["state"] == "Failed"

    def test_experiment_filter(self, capsys):
        client = MagicMock()
        exp = SimpleNamespace(experiment_id="exp-42")
        client.get_experiment.return_value = exp
        client.list_runs.return_value = self._make_runs_response(1)

        args = argparse.Namespace(limit=20, experiment="my-exp", state=None, json=False)
        cli.cmd_list(client, args)

        client.list_runs.assert_called_once_with(page_size=20, experiment_id="exp-42")

    def test_experiment_not_found(self):
        client = MagicMock()
        client.get_experiment.side_effect = Exception("not found")

        args = argparse.Namespace(limit=20, experiment="bogus", state=None, json=False)
        with pytest.raises(SystemExit, match="not found"):
            cli.cmd_list(client, args)

    def test_no_runs(self, capsys):
        client = MagicMock()
        client.list_runs.return_value = SimpleNamespace(runs=[])
        args = argparse.Namespace(limit=20, experiment=None, state=None, json=False)
        cli.cmd_list(client, args)
        assert "No runs found" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# cmd_watch
# ---------------------------------------------------------------------------

class TestCmdWatch:
    def test_exits_on_terminal_state(self):
        run = _make_run(state="Succeeded")
        client = MagicMock()
        client.get_run.return_value = run

        args = argparse.Namespace(run_id="test-id", interval=1, timeout=60)
        with pytest.raises(SystemExit) as exc_info:
            cli.cmd_watch(client, args)
        assert exc_info.value.code == 0

    def test_exits_with_code_1_on_failure(self):
        run = _make_run(state="Failed", error="something broke")
        client = MagicMock()
        client.get_run.return_value = run

        args = argparse.Namespace(run_id="test-id", interval=1, timeout=60)
        with pytest.raises(SystemExit) as exc_info:
            cli.cmd_watch(client, args)
        assert exc_info.value.code == 1

    def test_exits_with_code_2_on_timeout(self):
        run = _make_run(state="Running")
        run.run.finished_at = None
        client = MagicMock()
        client.get_run.return_value = run

        args = argparse.Namespace(run_id="test-id", interval=1, timeout=0)
        with pytest.raises(SystemExit) as exc_info:
            cli.cmd_watch(client, args)
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# _match_task_by_name
# ---------------------------------------------------------------------------

class TestMatchTaskByName:
    def test_exact_match(self):
        names = ["indexing", "rag-templates-optimization", "evaluation"]
        assert cli._match_task_by_name("indexing", names) == ["indexing"]

    def test_exact_match_case_insensitive(self):
        names = ["rag-templates-optimization", "evaluation"]
        assert cli._match_task_by_name("Rag-Templates-Optimization", names) == ["rag-templates-optimization"]

    def test_substring_match(self):
        names = ["rag-templates-optimization", "rag-templates-indexing", "evaluation"]
        result = cli._match_task_by_name("rag-templates", names)
        assert set(result) == {"rag-templates-optimization", "rag-templates-indexing"}

    def test_substring_match_case_insensitive(self):
        names = ["rag-templates-optimization", "evaluation"]
        assert cli._match_task_by_name("OPTIMIZATION", names) == ["rag-templates-optimization"]

    def test_no_match(self):
        names = ["indexing", "evaluation"]
        assert cli._match_task_by_name("nonexistent", names) == []

    def test_exact_preferred_over_substring(self):
        names = ["indexing", "rag-indexing", "indexing-v2"]
        assert cli._match_task_by_name("indexing", names) == ["indexing"]


# ---------------------------------------------------------------------------
# cmd_logs
# ---------------------------------------------------------------------------

class TestCmdLogs:
    def test_no_failures_shows_component_list(self, capsys):
        """Default mode with no failures should list components and suggest --all."""
        tasks = [
            _make_task("rag-templates-optimization", "Succeeded"),
            _make_task("evaluation", "Succeeded"),
        ]
        run = _make_run(state="Succeeded", task_details=tasks)
        client = MagicMock()
        client.get_run.return_value = run

        with patch.dict(os.environ, {"RHOAI_PROJECT_NAME": "ns"}, clear=False):
            args = argparse.Namespace(run_id="test-id", task=None, tail=100, all=False, json=False)
            cli.cmd_logs(client, args, k8s_api=MagicMock())

        out = capsys.readouterr().out
        assert "No failed tasks" in out
        assert "rag-templates-optimization" in out
        assert "evaluation" in out
        assert "--all" in out

    def test_task_not_found_shows_available(self, capsys):
        """--task with no match should list available components."""
        tasks = [
            _make_task("rag-templates-optimization", "Succeeded"),
            _make_task("evaluation", "Succeeded"),
        ]
        run = _make_run(state="Succeeded", task_details=tasks)
        client = MagicMock()
        client.get_run.return_value = run

        with patch.dict(os.environ, {"RHOAI_PROJECT_NAME": "ns"}, clear=False):
            args = argparse.Namespace(run_id="test-id", task="nonexistent", tail=100, all=False, json=False)
            with pytest.raises(SystemExit):
                cli.cmd_logs(client, args, k8s_api=MagicMock())

        out = capsys.readouterr().out
        assert "rag-templates-optimization" in out
        assert "evaluation" in out

    def test_task_substring_match(self, capsys):
        """--task with a substring should match the right component."""
        tasks = [
            _make_task("rag-templates-optimization", "Succeeded"),
            _make_task("evaluation", "Succeeded"),
        ]
        run = _make_run(state="Succeeded", task_details=tasks)

        pod = MagicMock()
        pod.metadata.name = "test-id-rag-templates-optimization-abc"
        pod.status.phase = "Succeeded"
        pod.status.container_statuses = []
        main_container = MagicMock()
        main_container.name = "main"
        pod.spec.containers = [main_container]

        k8s_api = MagicMock()
        pods_response = MagicMock()
        pods_response.items = [pod]
        k8s_api.list_namespaced_pod.return_value = pods_response
        k8s_api.read_namespaced_pod_log.return_value = "some log output"

        client = MagicMock()
        client.get_run.return_value = run

        with patch.dict(os.environ, {"RHOAI_PROJECT_NAME": "ns"}, clear=False):
            args = argparse.Namespace(run_id="test-id", task="optimization", tail=100, all=False, json=False)
            cli.cmd_logs(client, args, k8s_api=k8s_api)

        out = capsys.readouterr().out
        assert "rag-templates-optimization" in out
        assert "some log output" in out

    def test_skips_wait_container(self, capsys):
        """The 'wait' container should be skipped from output."""
        tasks = [_make_task("indexing", "Succeeded")]
        run = _make_run(state="Succeeded", task_details=tasks)

        pod = MagicMock()
        pod.metadata.name = "test-id-indexing-abc"
        pod.status.phase = "Succeeded"
        pod.status.container_statuses = []
        wait_c = MagicMock()
        wait_c.name = "wait"
        main_c = MagicMock()
        main_c.name = "main"
        pod.spec.containers = [wait_c, main_c]

        k8s_api = MagicMock()
        pods_response = MagicMock()
        pods_response.items = [pod]
        k8s_api.list_namespaced_pod.return_value = pods_response
        k8s_api.read_namespaced_pod_log.return_value = "main log output"

        client = MagicMock()
        client.get_run.return_value = run

        with patch.dict(os.environ, {"RHOAI_PROJECT_NAME": "ns"}, clear=False):
            args = argparse.Namespace(run_id="test-id", task=None, tail=100, all=True, json=False)
            cli.cmd_logs(client, args, k8s_api=k8s_api)

        out = capsys.readouterr().out
        assert "main log output" in out
        assert "wait" not in out.lower().split("main")[0]
        k8s_api.read_namespaced_pod_log.assert_called_once()

    def test_output_shows_component_name_and_pod(self, capsys):
        """Output should show component name prominently with pod name below."""
        tasks = [_make_task("rag-templates-optimization", "Failed", error="OOM")]
        run = _make_run(state="Failed", task_details=tasks)

        pod = MagicMock()
        pod.metadata.name = "abc-rag-templates-optimization-xyz-12345"
        pod.status.phase = "Failed"
        pod.status.container_statuses = []
        main_c = MagicMock()
        main_c.name = "main"
        pod.spec.containers = [main_c]

        k8s_api = MagicMock()
        pods_response = MagicMock()
        pods_response.items = [pod]
        k8s_api.list_namespaced_pod.return_value = pods_response
        k8s_api.read_namespaced_pod_log.return_value = "error trace here"

        client = MagicMock()
        client.get_run.return_value = run

        with patch.dict(os.environ, {"RHOAI_PROJECT_NAME": "ns"}, clear=False):
            args = argparse.Namespace(run_id="test-id", task=None, tail=100, all=False, json=False)
            cli.cmd_logs(client, args, k8s_api=k8s_api)

        out = capsys.readouterr().out
        assert "=== rag-templates-optimization (Failed) ===" in out
        assert "pod: abc-rag-templates-optimization-xyz-12345" in out
        assert "error trace here" in out

    def test_pod_name_from_kfp_task_details(self, capsys):
        """KFP task.pod_name should be used for direct pod matching (KFP v2 hash-based names)."""
        tasks = [
            _make_task("rag-templates-optimization", "Succeeded",
                       pod_name="run-my-pipeline-abc12-rag-templates-opt-4k7x2"),
        ]
        run = _make_run(state="Succeeded", task_details=tasks)

        pod = MagicMock()
        pod.metadata.name = "run-my-pipeline-abc12-rag-templates-opt-4k7x2"
        pod.metadata.labels = {}
        pod.metadata.annotations = {}
        pod.status.phase = "Succeeded"
        pod.status.container_statuses = []
        main_c = MagicMock()
        main_c.name = "main"
        pod.spec.containers = [main_c]

        k8s_api = MagicMock()
        pods_response = MagicMock()
        pods_response.items = [pod]
        k8s_api.list_namespaced_pod.return_value = pods_response
        k8s_api.read_namespaced_pod_log.return_value = "optimization logs here"

        client = MagicMock()
        client.get_run.return_value = run

        with patch.dict(os.environ, {"RHOAI_PROJECT_NAME": "ns"}, clear=False):
            args = argparse.Namespace(run_id="test-id", task=None, tail=100, all=True, json=False)
            cli.cmd_logs(client, args, k8s_api=k8s_api)

        out = capsys.readouterr().out
        assert "=== rag-templates-optimization (Succeeded) ===" in out
        assert "pod: run-my-pipeline-abc12-rag-templates-opt-4k7x2" in out
        assert "optimization logs here" in out


# ---------------------------------------------------------------------------
# cmd_artifacts (no S3 configured)
# ---------------------------------------------------------------------------

class TestCmdArtifactsNoS3:
    def test_warns_without_s3_credentials(self, capsys):
        run = _make_run(state="Succeeded")
        client = MagicMock()
        client.get_run.return_value = run

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("AWS_S3_ENDPOINT", None)
            args = argparse.Namespace(run_id="test-id", download=None, json=False)
            cli.cmd_artifacts(client, args)

        out = capsys.readouterr().out
        assert "S3 credentials not configured" in out

    def test_json_without_s3(self, capsys):
        run = _make_run(state="Succeeded")
        client = MagicMock()
        client.get_run.return_value = run

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("AWS_S3_ENDPOINT", None)
            args = argparse.Namespace(run_id="test-id", download=None, json=True)
            cli.cmd_artifacts(client, args)

        import json
        result = json.loads(capsys.readouterr().out)
        assert "artifact_root" in result
        assert result["note"] is not None


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------

class TestParser:
    def test_status_args(self):
        parser = cli._build_parser()
        args = parser.parse_args(["status", "abc-123"])
        assert args.command == "status"
        assert args.run_id == "abc-123"

    def test_list_args(self):
        parser = cli._build_parser()
        args = parser.parse_args(["--json", "list", "--limit", "5", "--experiment", "exp1", "--state", "failed"])
        assert args.json is True
        assert args.command == "list"
        assert args.limit == 5
        assert args.experiment == "exp1"
        assert args.state == "failed"

    def test_watch_args(self):
        parser = cli._build_parser()
        args = parser.parse_args(["watch", "run-42", "--interval", "15", "--timeout", "600"])
        assert args.command == "watch"
        assert args.run_id == "run-42"
        assert args.interval == 15
        assert args.timeout == 600

    def test_logs_args(self):
        parser = cli._build_parser()
        args = parser.parse_args(["logs", "run-42", "--task", "indexing", "--tail", "50", "--all"])
        assert args.command == "logs"
        assert args.run_id == "run-42"
        assert args.task == "indexing"
        assert args.tail == 50
        assert args.all is True

    def test_artifacts_args(self):
        parser = cli._build_parser()
        args = parser.parse_args(["artifacts", "run-42", "--download", "/tmp/out"])
        assert args.command == "artifacts"
        assert args.run_id == "run-42"
        assert args.download == "/tmp/out"

    def test_list_defaults(self):
        parser = cli._build_parser()
        args = parser.parse_args(["list"])
        assert args.limit == 20
        assert args.experiment is None
        assert args.state is None

    def test_watch_defaults(self):
        parser = cli._build_parser()
        args = parser.parse_args(["watch", "run-1"])
        assert args.interval == 10
        assert args.timeout == 3600

    def test_logs_defaults(self):
        parser = cli._build_parser()
        args = parser.parse_args(["logs", "run-1"])
        assert args.task is None
        assert args.tail == 100
        assert args.all is False

    def test_json_flag_position(self):
        parser = cli._build_parser()
        args = parser.parse_args(["--json", "status", "run-1"])
        assert args.json is True
        assert args.command == "status"
