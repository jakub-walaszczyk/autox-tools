"""Unit tests for the pipelines CLI tool.

All tests mock KFP and Kubernetes clients -- no cluster access required.
"""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from autox_tools.pipelines import _filters, cli

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
# _normalize_component_name / _match_pod_to_task
# ---------------------------------------------------------------------------

class TestNormalizeComponentName:
    def test_strips_comp_prefix(self):
        assert cli._normalize_component_name("comp-documents-discovery") == "documents-discovery"

    def test_lowercases_and_replaces_underscores(self):
        assert cli._normalize_component_name("Search_Space_Preparation") == "search-space-preparation"

    def test_noop_for_already_normalized(self):
        assert cli._normalize_component_name("documents-discovery") == "documents-discovery"

    def test_strips_whitespace(self):
        assert cli._normalize_component_name("  comp-foo  ") == "foo"


class TestMatchPodToTask:
    def test_exact_label_match(self):
        result = cli._match_pod_to_task(
            "some-pod", {"component_name": "indexing"}, {}, {"indexing", "eval"},
        )
        assert result == "indexing"

    def test_v2_label_with_comp_prefix(self):
        result = cli._match_pod_to_task(
            "some-pod",
            {"pipelines.kubeflow.org/v2_component_name": "comp-documents-discovery"},
            {},
            {"documents-discovery", "evaluation"},
        )
        assert result == "documents-discovery"

    def test_v2_annotation_with_comp_prefix(self):
        result = cli._match_pod_to_task(
            "some-pod", {},
            {"pipelines.kubeflow.org/v2_component_name": "comp-search-space-preparation"},
            {"search-space-preparation"},
        )
        assert result == "search-space-preparation"

    def test_substring_in_pod_name(self):
        result = cli._match_pod_to_task(
            "run-abc-rag-templates-optimization-xyz", {}, {},
            {"rag-templates-optimization"},
        )
        assert result == "rag-templates-optimization"

    def test_no_match_returns_none(self):
        result = cli._match_pod_to_task(
            "pipeline-zc4fk-system-container-impl-123", {}, {},
            {"documents-discovery"},
        )
        assert result is None

    def test_underscore_task_name_matches_hyphenated_label(self):
        result = cli._match_pod_to_task(
            "some-pod",
            {"pipelines.kubeflow.org/v2_component_name": "comp-test-data-loader"},
            {},
            {"test_data_loader"},
        )
        assert result == "test_data_loader"


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
            args = argparse.Namespace(run_id="test-id", tail=100, all=False, json=False)
            cli.cmd_logs(client, args, k8s_api=MagicMock())

        out = capsys.readouterr().out
        assert "No failed tasks" in out
        assert "rag-templates-optimization" in out
        assert "evaluation" in out
        assert "--all" in out

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
            args = argparse.Namespace(run_id="test-id", tail=100, all=True, json=False)
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
            args = argparse.Namespace(run_id="test-id", tail=100, all=False, json=False)
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
            args = argparse.Namespace(run_id="test-id", tail=100, all=True, json=False)
            cli.cmd_logs(client, args, k8s_api=k8s_api)

        out = capsys.readouterr().out
        assert "=== rag-templates-optimization (Succeeded) ===" in out
        assert "pod: run-my-pipeline-abc12-rag-templates-opt-4k7x2" in out
        assert "optimization logs here" in out

    def test_v2_component_label_with_comp_prefix(self, capsys):
        """KFP v2 pods with comp- prefixed labels should match task names."""
        tasks = [_make_task("documents-discovery", "Failed", error="exit 1")]
        run = _make_run(state="Failed", task_details=tasks)

        pod = MagicMock()
        pod.metadata.name = "pipeline-zc4fk-system-container-impl-1249432282"
        pod.metadata.labels = {
            "pipelines.kubeflow.org/v2_component_name": "comp-documents-discovery",
        }
        pod.metadata.annotations = {}
        pod.status.phase = "Failed"
        pod.status.container_statuses = []
        main_c = MagicMock()
        main_c.name = "main"
        pod.spec.containers = [main_c]

        k8s_api = MagicMock()
        pods_response = MagicMock()
        pods_response.items = [pod]
        k8s_api.list_namespaced_pod.return_value = pods_response
        k8s_api.read_namespaced_pod_log.return_value = "discovery error logs"

        client = MagicMock()
        client.get_run.return_value = run

        with patch.dict(os.environ, {"RHOAI_PROJECT_NAME": "ns"}, clear=False):
            args = argparse.Namespace(run_id="test-id", tail=100, all=False, json=False)
            cli.cmd_logs(client, args, k8s_api=k8s_api)

        out = capsys.readouterr().out
        assert "=== documents-discovery (Failed) ===" in out
        assert "discovery error logs" in out

    def test_v2_annotation_matching(self, capsys):
        """KFP v2 pods with v2_component_name annotation should match."""
        tasks = [_make_task("search-space-preparation", "Failed")]
        run = _make_run(state="Failed", task_details=tasks)

        pod = MagicMock()
        pod.metadata.name = "pipeline-abc-system-container-impl-999"
        pod.metadata.labels = {}
        pod.metadata.annotations = {
            "pipelines.kubeflow.org/v2_component_name": "comp-search-space-preparation",
        }
        pod.status.phase = "Failed"
        pod.status.container_statuses = []
        main_c = MagicMock()
        main_c.name = "main"
        pod.spec.containers = [main_c]

        k8s_api = MagicMock()
        pods_response = MagicMock()
        pods_response.items = [pod]
        k8s_api.list_namespaced_pod.return_value = pods_response
        k8s_api.read_namespaced_pod_log.return_value = "search logs"

        client = MagicMock()
        client.get_run.return_value = run

        with patch.dict(os.environ, {"RHOAI_PROJECT_NAME": "ns"}, clear=False):
            args = argparse.Namespace(run_id="test-id", tail=100, all=False, json=False)
            cli.cmd_logs(client, args, k8s_api=k8s_api)

        out = capsys.readouterr().out
        assert "=== search-space-preparation (Failed) ===" in out
        assert "search logs" in out

    def test_fallback_dumps_impl_pods_when_no_match(self, capsys):
        """When no task-to-pod mapping works, fall back to impl pod logs."""
        tasks = [_make_task("documents-discovery", "Failed")]
        run = _make_run(state="Failed", task_details=tasks)

        driver_pod = MagicMock()
        driver_pod.metadata.name = "pipeline-zc4fk-system-container-driver-123"
        driver_pod.metadata.labels = {}
        driver_pod.metadata.annotations = {}
        driver_pod.status.phase = "Succeeded"
        driver_pod.status.container_statuses = []
        d_main = MagicMock()
        d_main.name = "main"
        driver_pod.spec.containers = [d_main]

        impl_pod = MagicMock()
        impl_pod.metadata.name = "pipeline-zc4fk-system-container-impl-456"
        impl_pod.metadata.labels = {}
        impl_pod.metadata.annotations = {}
        impl_pod.status.phase = "Failed"
        impl_pod.status.container_statuses = []
        cs = MagicMock()
        cs.state.terminated.exit_code = 1
        impl_pod.status.container_statuses = [cs]
        i_main = MagicMock()
        i_main.name = "main"
        impl_pod.spec.containers = [i_main]

        k8s_api = MagicMock()
        pods_response = MagicMock()
        pods_response.items = [driver_pod, impl_pod]
        k8s_api.list_namespaced_pod.return_value = pods_response
        k8s_api.read_namespaced_pod_log.return_value = "fallback error output"

        client = MagicMock()
        client.get_run.return_value = run

        with patch.dict(os.environ, {"RHOAI_PROJECT_NAME": "ns"}, clear=False):
            args = argparse.Namespace(run_id="test-id", tail=100, all=False, json=False)
            cli.cmd_logs(client, args, k8s_api=k8s_api)

        out = capsys.readouterr().out
        assert "fallback error output" in out
        assert "impl-456" in out


# ---------------------------------------------------------------------------
# cmd_artifacts (no S3 configured)
# ---------------------------------------------------------------------------

def _artifacts_ns(**overrides: Any) -> argparse.Namespace:
    """Build a Namespace for cmd_artifacts with defaults for all flags."""
    defaults: dict[str, Any] = {
        "run_id": "test-id", "component": None, "pattern": None,
        "artifact": None, "download": None, "print_content": False,
        "json": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestCmdArtifactsNoS3:
    def test_warns_without_artifacts_s3_credentials(self, capsys):
        run = _make_run(state="Succeeded")
        client = MagicMock()
        client.get_run.return_value = run

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ARTIFACTS_AWS_S3_ENDPOINT", None)
            cli.cmd_artifacts(client, _artifacts_ns())

        out = capsys.readouterr().out
        assert "Artifacts S3 credentials not configured" in out
        assert "ARTIFACTS_AWS_S3_ENDPOINT" in out

    def test_json_without_artifacts_s3(self, capsys):
        run = _make_run(state="Succeeded")
        client = MagicMock()
        client.get_run.return_value = run

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ARTIFACTS_AWS_S3_ENDPOINT", None)
            cli.cmd_artifacts(client, _artifacts_ns(json=True))

        import json
        result = json.loads(capsys.readouterr().out)
        assert "artifact_root" in result
        assert result["note"] is not None

    def test_missing_bucket_without_s3_uri(self):
        """Exit with clear error when artifact_root has no s3:// and ARTIFACTS_S3_BUCKET is unset."""
        run = _make_run(state="Succeeded")
        client = MagicMock()
        client.get_run.return_value = run

        env = {
            "ARTIFACTS_AWS_S3_ENDPOINT": "https://s3.amazonaws.com/",
            "ARTIFACTS_AWS_ACCESS_KEY_ID": "key",
            "ARTIFACTS_AWS_SECRET_ACCESS_KEY": "secret",
        }
        with patch.dict(os.environ, env, clear=True), \
             patch("autox_tools.pipelines._artifacts_s3.load_dotenv"), \
             patch("autox_tools.pipelines._artifacts_s3.find_dotenv", return_value=""), \
             patch("autox_tools.pipelines._artifacts_s3.boto3"), \
             pytest.raises(SystemExit, match="ARTIFACTS_S3_BUCKET is required"):
            cli.cmd_artifacts(client, _artifacts_ns())

    def test_artifact_flag_without_pattern_exits(self):
        """--artifact without --pattern should exit with usage hint."""
        run = _make_run(state="Succeeded")
        client = MagicMock()
        client.get_run.return_value = run

        env = {
            "ARTIFACTS_AWS_S3_ENDPOINT": "https://s3.example.com/",
            "ARTIFACTS_AWS_ACCESS_KEY_ID": "key",
            "ARTIFACTS_AWS_SECRET_ACCESS_KEY": "secret",
            "ARTIFACTS_S3_BUCKET": "bucket",
        }
        with patch.dict(os.environ, env, clear=True), \
             patch("autox_tools.pipelines._artifacts_s3.load_dotenv"), \
             patch("autox_tools.pipelines._artifacts_s3.find_dotenv", return_value=""), \
             patch("autox_tools.pipelines._artifacts_s3.boto3"), \
             pytest.raises(SystemExit, match="--artifact requires --pattern"):
            cli.cmd_artifacts(client, _artifacts_ns(artifact="eval.json"))


# ---------------------------------------------------------------------------
# _artifacts_s3.py tests
# ---------------------------------------------------------------------------

class TestArtifactsS3Connect:
    def test_missing_env_vars_exits(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("autox_tools.pipelines._artifacts_s3.load_dotenv"), \
             patch("autox_tools.pipelines._artifacts_s3.find_dotenv", return_value=""), \
             pytest.raises(SystemExit, match="Missing required environment variables"):
            from autox_tools.pipelines._artifacts_s3 import connect
            connect()

    def test_connect_builds_client(self):
        env = {
            "ARTIFACTS_AWS_S3_ENDPOINT": "https://artifacts-minio.example.com",
            "ARTIFACTS_AWS_ACCESS_KEY_ID": "art-key",
            "ARTIFACTS_AWS_SECRET_ACCESS_KEY": "art-secret",
        }
        with patch.dict(os.environ, env, clear=True), \
             patch("autox_tools.pipelines._artifacts_s3.load_dotenv"), \
             patch("autox_tools.pipelines._artifacts_s3.find_dotenv", return_value=""), \
             patch("autox_tools.pipelines._artifacts_s3.boto3") as mock_boto3:
            from autox_tools.pipelines._artifacts_s3 import connect
            connect()
            _, kwargs = mock_boto3.client.call_args
            assert kwargs["endpoint_url"] == "https://artifacts-minio.example.com"
            assert kwargs["aws_access_key_id"] == "art-key"
            assert kwargs["aws_secret_access_key"] == "art-secret"
            assert kwargs["region_name"] == "us-east-1"
            assert kwargs["verify"] is True
            assert kwargs["config"].s3["addressing_style"] == "path"

    def test_connect_tls_disabled(self):
        env = {
            "ARTIFACTS_AWS_S3_ENDPOINT": "https://artifacts-minio.example.com",
            "ARTIFACTS_AWS_ACCESS_KEY_ID": "art-key",
            "ARTIFACTS_AWS_SECRET_ACCESS_KEY": "art-secret",
            "ARTIFACTS_S3_VERIFY_TLS": "false",
        }
        with patch.dict(os.environ, env, clear=True), \
             patch("autox_tools.pipelines._artifacts_s3.load_dotenv"), \
             patch("autox_tools.pipelines._artifacts_s3.find_dotenv", return_value=""), \
             patch("autox_tools.pipelines._artifacts_s3.boto3") as mock_boto3:
            from autox_tools.pipelines._artifacts_s3 import connect
            connect()
            _, kwargs = mock_boto3.client.call_args
            assert kwargs["verify"] is False

    def test_connect_custom_region(self):
        env = {
            "ARTIFACTS_AWS_S3_ENDPOINT": "https://artifacts-minio.example.com",
            "ARTIFACTS_AWS_ACCESS_KEY_ID": "art-key",
            "ARTIFACTS_AWS_SECRET_ACCESS_KEY": "art-secret",
            "ARTIFACTS_AWS_DEFAULT_REGION": "eu-west-1",
        }
        with patch.dict(os.environ, env, clear=True), \
             patch("autox_tools.pipelines._artifacts_s3.load_dotenv"), \
             patch("autox_tools.pipelines._artifacts_s3.find_dotenv", return_value=""), \
             patch("autox_tools.pipelines._artifacts_s3.boto3") as mock_boto3:
            from autox_tools.pipelines._artifacts_s3 import connect
            connect()
            _, kwargs = mock_boto3.client.call_args
            assert kwargs["region_name"] == "eu-west-1"


# ---------------------------------------------------------------------------
# Pattern discovery and filtering
# ---------------------------------------------------------------------------

class TestCategorizeObject:
    @pytest.mark.parametrize("key,expected", [
        ("prefix/evaluation_results.json", "evaluation"),
        ("prefix/indexing_notebook.ipynb", "indexing_notebooks"),
        ("prefix/inference_notebook.ipynb", "inference_notebooks"),
        ("prefix/leaderboard.html", "leaderboard"),
        ("prefix/results/leaderboard_v2.json", "leaderboard"),
        ("prefix/rag_patterns/P1/pattern.json", "rag_patterns"),
        ("prefix/rag_patterns/P1/some_file.txt", "rag_patterns"),
        ("prefix/other/random.csv", "other"),
    ])
    def test_categorization(self, key, expected):
        assert cli._categorize_object(key) == expected

    @pytest.mark.parametrize("key", [
        "prefix/rag_patterns/P1/evaluation_results.json",
        "prefix/rag_patterns/P1/inference_notebook.ipynb",
        "prefix/rag_patterns/P1/indexing_notebook.ipynb",
        "prefix/rag_patterns/P1/leaderboard.html",
    ])
    def test_rag_patterns_takes_priority(self, key):
        """Files inside rag_patterns/ are always categorized as rag_patterns."""
        assert cli._categorize_object(key) == "rag_patterns"


class TestFindRagPatternsPrefix:
    def test_finds_prefix_from_keys(self):
        objects = [
            {"Key": "pipe/run-1/comp/task-id/rag_patterns/P1/file.json", "Size": 100},
        ]
        s3 = MagicMock()
        with patch("autox_tools.s3.cli._paginate_objects", return_value={"Contents": objects}):
            result = cli._find_rag_patterns_prefix(s3, "bucket", "pipe/run-1/")
        assert result == "pipe/run-1/comp/task-id/rag_patterns/"

    def test_returns_none_when_not_found(self):
        objects = [{"Key": "pipe/run-1/other/file.csv", "Size": 50}]
        s3 = MagicMock()
        with patch("autox_tools.s3.cli._paginate_objects", return_value={"Contents": objects}):
            result = cli._find_rag_patterns_prefix(s3, "bucket", "pipe/run-1/")
        assert result is None

    def test_returns_none_on_empty(self):
        s3 = MagicMock()
        with patch("autox_tools.s3.cli._paginate_objects", return_value={"Contents": []}):
            result = cli._find_rag_patterns_prefix(s3, "bucket", "pipe/run-1/")
        assert result is None


class TestDiscoverPatterns:
    def test_extracts_pattern_names(self):
        s3 = MagicMock()
        common_prefixes = [
            {"Prefix": "base/rag_patterns/Pattern_1/"},
            {"Prefix": "base/rag_patterns/Pattern_2/"},
            {"Prefix": "base/rag_patterns/Default/"},
        ]
        with patch("autox_tools.s3.cli._paginate_objects",
                    return_value={"CommonPrefixes": common_prefixes}):
            result = cli._discover_patterns(s3, "bucket", "base/rag_patterns/")
        assert result == ["Default", "Pattern_1", "Pattern_2"]

    def test_empty_when_no_prefixes(self):
        s3 = MagicMock()
        with patch("autox_tools.s3.cli._paginate_objects",
                    return_value={"CommonPrefixes": []}):
            result = cli._discover_patterns(s3, "bucket", "base/rag_patterns/")
        assert result == []


class TestMatchPatternName:
    def test_exact_match(self):
        assert cli._match_pattern_name("Pattern_1", ["Pattern_1", "Pattern_2"]) == ["Pattern_1"]

    def test_case_insensitive(self):
        assert cli._match_pattern_name("pattern_1", ["Pattern_1", "Pattern_2"]) == ["Pattern_1"]

    def test_substring(self):
        result = cli._match_pattern_name("Pattern", ["Pattern_1", "Pattern_2", "Default"])
        assert set(result) == {"Pattern_1", "Pattern_2"}

    def test_no_match(self):
        assert cli._match_pattern_name("missing", ["Pattern_1", "Pattern_2"]) == []

    def test_exact_preferred(self):
        assert cli._match_pattern_name("Default", ["Default", "DefaultV2"]) == ["Default"]


_ARTIFACTS_ENV = {
    "ARTIFACTS_AWS_S3_ENDPOINT": "https://s3.example.com/",
    "ARTIFACTS_AWS_ACCESS_KEY_ID": "key",
    "ARTIFACTS_AWS_SECRET_ACCESS_KEY": "secret",
}


def _mock_s3_run():
    """Create a mock KFP run with s3:// artifact root."""
    run_obj = SimpleNamespace(
        run_id="run-1",
        state="Succeeded",
        created_at=None,
        finished_at=None,
        error=None,
        display_name="test-run",
        name="test-run",
        pipeline_spec={"pipeline_name": "my-pipeline"},
        runtime_config=SimpleNamespace(
            pipeline_root="s3://test-bucket/pipeline/run-1/",
            parameters={},
        ),
    )
    return SimpleNamespace(run=run_obj, run_details=SimpleNamespace(task_details=[]))


class TestCmdArtifactsSummary:
    def test_summary_shows_category_counts(self, capsys):
        """Default mode should show category counts, not individual file listings."""
        objects = [
            {"Key": "pipeline/run-1/eval/evaluation_results.json", "Size": 1000},
            {"Key": "pipeline/run-1/comp/id/rag_patterns/P1/pattern.json", "Size": 500},
            {"Key": "pipeline/run-1/comp/id/rag_patterns/P1/other.csv", "Size": 200},
            {"Key": "pipeline/run-1/leaderboard.html", "Size": 300},
            {"Key": "pipeline/run-1/random.txt", "Size": 50},
        ]
        kfp_client = MagicMock()
        kfp_client.get_run.return_value = _mock_s3_run()

        with patch.dict(os.environ, _ARTIFACTS_ENV, clear=False), \
             patch("autox_tools.pipelines._artifacts_s3.load_dotenv"), \
             patch("autox_tools.pipelines._artifacts_s3.find_dotenv", return_value=""), \
             patch("autox_tools.pipelines._artifacts_s3.boto3"), \
             patch("autox_tools.s3.cli._paginate_objects",
                    return_value={"Contents": objects, "CommonPrefixes": []}), \
             patch("autox_tools.pipelines.cli._find_rag_patterns_prefix",
                    return_value="pipeline/run-1/comp/id/rag_patterns/"), \
             patch("autox_tools.pipelines.cli._discover_patterns",
                    return_value=["P1"]):
            cli.cmd_artifacts(kfp_client, _artifacts_ns())

        out = capsys.readouterr().out
        assert "Evaluation Results" in out
        assert "RAG Patterns" in out
        assert "5 artifact(s)" in out
        assert "P1" in out
        assert "--pattern" in out

    def test_summary_json(self, capsys):
        objects = [
            {"Key": "pipeline/run-1/eval/evaluation_results.json", "Size": 1000},
        ]
        kfp_client = MagicMock()
        kfp_client.get_run.return_value = _mock_s3_run()

        with patch.dict(os.environ, _ARTIFACTS_ENV, clear=False), \
             patch("autox_tools.pipelines._artifacts_s3.load_dotenv"), \
             patch("autox_tools.pipelines._artifacts_s3.find_dotenv", return_value=""), \
             patch("autox_tools.pipelines._artifacts_s3.boto3"), \
             patch("autox_tools.s3.cli._paginate_objects",
                    return_value={"Contents": objects, "CommonPrefixes": []}), \
             patch("autox_tools.pipelines.cli._find_rag_patterns_prefix",
                    return_value=None), \
             patch("autox_tools.pipelines.cli._discover_patterns",
                    return_value=[]):
            cli.cmd_artifacts(kfp_client, _artifacts_ns(json=True))

        import json
        result = json.loads(capsys.readouterr().out)
        assert result["total_artifacts"] == 1
        assert result["categories"]["evaluation"]["count"] == 1
        assert result["patterns"] == []


class TestCmdArtifactsPattern:
    def _setup_pattern_mocks(self, capsys):
        """Return mocks and patches for pattern-mode tests."""
        kfp_client = MagicMock()
        kfp_client.get_run.return_value = _mock_s3_run()
        return kfp_client

    def test_pattern_single_lists_files(self, capsys):
        kfp_client = self._setup_pattern_mocks(capsys)
        pattern_objects = [
            {"Key": "base/rag_patterns/P1/pattern.json", "Size": 500},
            {"Key": "base/rag_patterns/P1/evaluation_results.json", "Size": 1000},
            {"Key": "base/rag_patterns/P1/notebook.ipynb", "Size": 2000},
        ]

        with patch.dict(os.environ, _ARTIFACTS_ENV, clear=False), \
             patch("autox_tools.pipelines._artifacts_s3.load_dotenv"), \
             patch("autox_tools.pipelines._artifacts_s3.find_dotenv", return_value=""), \
             patch("autox_tools.pipelines._artifacts_s3.boto3"), \
             patch("autox_tools.pipelines.cli._find_rag_patterns_prefix",
                    return_value="base/rag_patterns/"), \
             patch("autox_tools.pipelines.cli._discover_patterns",
                    return_value=["P1", "P2"]), \
             patch("autox_tools.s3.cli._paginate_objects",
                    return_value={"Contents": pattern_objects, "CommonPrefixes": []}):
            cli.cmd_artifacts(kfp_client, _artifacts_ns(pattern="P1"))

        out = capsys.readouterr().out
        assert "Pattern: P1" in out
        assert "pattern.json" in out
        assert "evaluation_results.json" in out
        assert "3 artifact(s)" in out

    def test_pattern_all_shows_summaries(self, capsys):
        kfp_client = self._setup_pattern_mocks(capsys)

        def mock_paginate(client, bucket, prefix, **kw):
            if "P1/" in prefix:
                return {"Contents": [{"Key": f"{prefix}f1", "Size": 100}], "CommonPrefixes": []}
            if "P2/" in prefix:
                return {"Contents": [
                    {"Key": f"{prefix}f1", "Size": 200},
                    {"Key": f"{prefix}f2", "Size": 300},
                ], "CommonPrefixes": []}
            return {"Contents": [], "CommonPrefixes": []}

        with patch.dict(os.environ, _ARTIFACTS_ENV, clear=False), \
             patch("autox_tools.pipelines._artifacts_s3.load_dotenv"), \
             patch("autox_tools.pipelines._artifacts_s3.find_dotenv", return_value=""), \
             patch("autox_tools.pipelines._artifacts_s3.boto3"), \
             patch("autox_tools.pipelines.cli._find_rag_patterns_prefix",
                    return_value="base/rag_patterns/"), \
             patch("autox_tools.pipelines.cli._discover_patterns",
                    return_value=["P1", "P2"]), \
             patch("autox_tools.s3.cli._paginate_objects", side_effect=mock_paginate):
            cli.cmd_artifacts(kfp_client, _artifacts_ns(pattern="all"))

        out = capsys.readouterr().out
        assert "P1" in out
        assert "P2" in out
        assert "2 pattern(s)" in out

    def test_pattern_not_found_exits(self, capsys):
        kfp_client = self._setup_pattern_mocks(capsys)

        with patch.dict(os.environ, _ARTIFACTS_ENV, clear=False), \
             patch("autox_tools.pipelines._artifacts_s3.load_dotenv"), \
             patch("autox_tools.pipelines._artifacts_s3.find_dotenv", return_value=""), \
             patch("autox_tools.pipelines._artifacts_s3.boto3"), \
             patch("autox_tools.pipelines.cli._find_rag_patterns_prefix",
                    return_value="base/rag_patterns/"), \
             patch("autox_tools.pipelines.cli._discover_patterns",
                    return_value=["P1", "P2"]), \
             pytest.raises(SystemExit):
            cli.cmd_artifacts(kfp_client, _artifacts_ns(pattern="nonexistent"))

        out = capsys.readouterr().out
        assert "P1" in out
        assert "P2" in out

    def test_artifact_downloads_single_file(self, capsys, tmp_path):
        kfp_client = self._setup_pattern_mocks(capsys)
        pattern_objects = [
            {"Key": "base/rag_patterns/P1/pattern.json", "Size": 500},
            {"Key": "base/rag_patterns/P1/evaluation_results.json", "Size": 1000},
        ]

        mock_s3 = MagicMock()

        with patch.dict(os.environ, _ARTIFACTS_ENV, clear=False), \
             patch("autox_tools.pipelines._artifacts_s3.load_dotenv"), \
             patch("autox_tools.pipelines._artifacts_s3.find_dotenv", return_value=""), \
             patch("autox_tools.pipelines._artifacts_s3.boto3") as mock_boto3, \
             patch("autox_tools.pipelines.cli._find_rag_patterns_prefix",
                    return_value="base/rag_patterns/"), \
             patch("autox_tools.pipelines.cli._discover_patterns",
                    return_value=["P1"]), \
             patch("autox_tools.s3.cli._paginate_objects",
                    return_value={"Contents": pattern_objects, "CommonPrefixes": []}):
            mock_boto3.client.return_value = mock_s3
            cli.cmd_artifacts(
                kfp_client,
                _artifacts_ns(pattern="P1", artifact="evaluation_results", download=str(tmp_path)),
            )

        out = capsys.readouterr().out
        assert "evaluation_results.json" in out
        assert "Downloaded" in out
        mock_s3.download_file.assert_called_once()

    def test_artifact_not_found_shows_available(self, capsys):
        kfp_client = self._setup_pattern_mocks(capsys)
        pattern_objects = [
            {"Key": "base/rag_patterns/P1/pattern.json", "Size": 500},
        ]

        with patch.dict(os.environ, _ARTIFACTS_ENV, clear=False), \
             patch("autox_tools.pipelines._artifacts_s3.load_dotenv"), \
             patch("autox_tools.pipelines._artifacts_s3.find_dotenv", return_value=""), \
             patch("autox_tools.pipelines._artifacts_s3.boto3"), \
             patch("autox_tools.pipelines.cli._find_rag_patterns_prefix",
                    return_value="base/rag_patterns/"), \
             patch("autox_tools.pipelines.cli._discover_patterns",
                    return_value=["P1"]), \
             patch("autox_tools.s3.cli._paginate_objects",
                    return_value={"Contents": pattern_objects, "CommonPrefixes": []}), \
             pytest.raises(SystemExit):
            cli.cmd_artifacts(
                kfp_client,
                _artifacts_ns(pattern="P1", artifact="nonexistent"),
            )

        out = capsys.readouterr().out
        assert "pattern.json" in out

    def test_artifact_without_download_shows_metadata_only(self, capsys):
        """--artifact without --download should show metadata, not download."""
        kfp_client = self._setup_pattern_mocks(capsys)
        pattern_objects = [
            {"Key": "base/rag_patterns/P1/pattern.json", "Size": 500},
        ]

        mock_s3 = MagicMock()

        with patch.dict(os.environ, _ARTIFACTS_ENV, clear=False), \
             patch("autox_tools.pipelines._artifacts_s3.load_dotenv"), \
             patch("autox_tools.pipelines._artifacts_s3.find_dotenv", return_value=""), \
             patch("autox_tools.pipelines._artifacts_s3.boto3") as mock_boto3, \
             patch("autox_tools.pipelines.cli._find_rag_patterns_prefix",
                    return_value="base/rag_patterns/"), \
             patch("autox_tools.pipelines.cli._discover_patterns",
                    return_value=["P1"]), \
             patch("autox_tools.s3.cli._paginate_objects",
                    return_value={"Contents": pattern_objects, "CommonPrefixes": []}):
            mock_boto3.client.return_value = mock_s3
            cli.cmd_artifacts(
                kfp_client,
                _artifacts_ns(pattern="P1", artifact="pattern.json"),
            )

        out = capsys.readouterr().out
        assert "pattern.json" in out
        assert "S3 key" in out
        assert "Downloaded" not in out
        mock_s3.download_file.assert_not_called()

    def test_artifact_print_outputs_to_stdout(self, capsys):
        """--print should fetch the S3 object and write its content to stdout."""
        kfp_client = self._setup_pattern_mocks(capsys)
        pattern_objects = [
            {"Key": "base/rag_patterns/P1/pattern.json", "Size": 42},
        ]

        body_mock = MagicMock()
        body_mock.read.return_value = b'{"key": "value"}'
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": body_mock}

        with patch.dict(os.environ, _ARTIFACTS_ENV, clear=False), \
             patch("autox_tools.pipelines._artifacts_s3.load_dotenv"), \
             patch("autox_tools.pipelines._artifacts_s3.find_dotenv", return_value=""), \
             patch("autox_tools.pipelines._artifacts_s3.boto3") as mock_boto3, \
             patch("autox_tools.pipelines.cli._find_rag_patterns_prefix",
                    return_value="base/rag_patterns/"), \
             patch("autox_tools.pipelines.cli._discover_patterns",
                    return_value=["P1"]), \
             patch("autox_tools.s3.cli._paginate_objects",
                    return_value={"Contents": pattern_objects, "CommonPrefixes": []}):
            mock_boto3.client.return_value = mock_s3
            cli.cmd_artifacts(
                kfp_client,
                _artifacts_ns(pattern="P1", artifact="pattern.json", print_content=True),
            )

        mock_s3.get_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="base/rag_patterns/P1/pattern.json",
        )

    def test_no_rag_patterns_folder_exits(self, capsys):
        kfp_client = self._setup_pattern_mocks(capsys)

        with patch.dict(os.environ, _ARTIFACTS_ENV, clear=False), \
             patch("autox_tools.pipelines._artifacts_s3.load_dotenv"), \
             patch("autox_tools.pipelines._artifacts_s3.find_dotenv", return_value=""), \
             patch("autox_tools.pipelines._artifacts_s3.boto3"), \
             patch("autox_tools.pipelines.cli._find_rag_patterns_prefix",
                    return_value=None), \
             pytest.raises(SystemExit):
            cli.cmd_artifacts(kfp_client, _artifacts_ns(pattern="P1"))


# ---------------------------------------------------------------------------
# _discover_components and --component mode
# ---------------------------------------------------------------------------

class TestRefinePrefixForRun:
    def test_appends_run_id_when_objects_exist(self):
        s3 = MagicMock()
        with patch("autox_tools.s3.cli._paginate_objects",
                    return_value={"Contents": [{"Key": "pipe/run-1/comp/f", "Size": 1}]}):
            result = cli._refine_prefix_for_run(s3, "bucket", "pipe/", "run-1")
        assert result == "pipe/run-1/"

    def test_keeps_original_when_no_objects(self):
        s3 = MagicMock()
        with patch("autox_tools.s3.cli._paginate_objects",
                    return_value={"Contents": []}):
            result = cli._refine_prefix_for_run(s3, "bucket", "pipe/", "run-1")
        assert result == "pipe/"

    def test_noop_when_run_id_already_in_prefix(self):
        s3 = MagicMock()
        result = cli._refine_prefix_for_run(s3, "bucket", "pipe/run-1/", "run-1")
        assert result == "pipe/run-1/"


class TestDiscoverComponents:
    def test_extracts_component_names(self):
        s3 = MagicMock()
        common_prefixes = [
            {"Prefix": "pipeline/run-1/rag-templates-optimization/"},
            {"Prefix": "pipeline/run-1/search-space-optimization/"},
            {"Prefix": "pipeline/run-1/data-preprocessing/"},
        ]
        with patch("autox_tools.s3.cli._paginate_objects",
                    return_value={"CommonPrefixes": common_prefixes}):
            result = cli._discover_components(s3, "bucket", "pipeline/run-1/")
        assert result == ["data-preprocessing", "rag-templates-optimization", "search-space-optimization"]

    def test_empty_when_no_prefixes(self):
        s3 = MagicMock()
        with patch("autox_tools.s3.cli._paginate_objects",
                    return_value={"CommonPrefixes": []}):
            result = cli._discover_components(s3, "bucket", "pipeline/run-1/")
        assert result == []


class TestCmdArtifactsComponent:
    def _setup_mocks(self):
        kfp_client = MagicMock()
        kfp_client.get_run.return_value = _mock_s3_run()
        return kfp_client

    def test_component_lists_files(self, capsys):
        kfp_client = self._setup_mocks()
        comp_objects = [
            {"Key": "pipeline/run-1/search-space/id1/file1.json", "Size": 100},
            {"Key": "pipeline/run-1/search-space/id1/file2.csv", "Size": 200},
        ]

        with patch.dict(os.environ, _ARTIFACTS_ENV, clear=False), \
             patch("autox_tools.pipelines._artifacts_s3.load_dotenv"), \
             patch("autox_tools.pipelines._artifacts_s3.find_dotenv", return_value=""), \
             patch("autox_tools.pipelines._artifacts_s3.boto3"), \
             patch("autox_tools.pipelines.cli._discover_components",
                    return_value=["rag-templates-optimization", "search-space"]), \
             patch("autox_tools.s3.cli._paginate_objects",
                    return_value={"Contents": comp_objects, "CommonPrefixes": []}):
            cli.cmd_artifacts(kfp_client, _artifacts_ns(component="search-space"))

        out = capsys.readouterr().out
        assert "Component: search-space" in out
        assert "file1.json" in out
        assert "file2.csv" in out
        assert "2 artifact(s)" in out

    def test_component_all_shows_summaries(self, capsys):
        kfp_client = self._setup_mocks()

        def mock_paginate(client, bucket, prefix, **kw):
            if "rag-templates" in prefix:
                return {"Contents": [{"Key": f"{prefix}f1", "Size": 500}], "CommonPrefixes": []}
            if "search-space" in prefix:
                return {"Contents": [
                    {"Key": f"{prefix}f1", "Size": 100},
                    {"Key": f"{prefix}f2", "Size": 200},
                ], "CommonPrefixes": []}
            return {"Contents": [], "CommonPrefixes": []}

        with patch.dict(os.environ, _ARTIFACTS_ENV, clear=False), \
             patch("autox_tools.pipelines._artifacts_s3.load_dotenv"), \
             patch("autox_tools.pipelines._artifacts_s3.find_dotenv", return_value=""), \
             patch("autox_tools.pipelines._artifacts_s3.boto3"), \
             patch("autox_tools.pipelines.cli._discover_components",
                    return_value=["rag-templates-optimization", "search-space"]), \
             patch("autox_tools.s3.cli._paginate_objects", side_effect=mock_paginate):
            cli.cmd_artifacts(kfp_client, _artifacts_ns(component="all"))

        out = capsys.readouterr().out
        assert "rag-templates-optimization" in out
        assert "search-space" in out
        assert "2 component(s)" in out

    def test_component_not_found_exits(self, capsys):
        kfp_client = self._setup_mocks()

        with patch.dict(os.environ, _ARTIFACTS_ENV, clear=False), \
             patch("autox_tools.pipelines._artifacts_s3.load_dotenv"), \
             patch("autox_tools.pipelines._artifacts_s3.find_dotenv", return_value=""), \
             patch("autox_tools.pipelines._artifacts_s3.boto3"), \
             patch("autox_tools.pipelines.cli._discover_components",
                    return_value=["rag-templates-optimization", "search-space"]), \
             pytest.raises(SystemExit):
            cli.cmd_artifacts(kfp_client, _artifacts_ns(component="nonexistent"))

        out = capsys.readouterr().out
        assert "rag-templates-optimization" in out
        assert "search-space" in out

    def test_component_with_download(self, capsys, tmp_path):
        kfp_client = self._setup_mocks()
        comp_objects = [
            {"Key": "pipeline/run-1/search-space/id1/file1.json", "Size": 100},
        ]
        mock_s3 = MagicMock()

        with patch.dict(os.environ, _ARTIFACTS_ENV, clear=False), \
             patch("autox_tools.pipelines._artifacts_s3.load_dotenv"), \
             patch("autox_tools.pipelines._artifacts_s3.find_dotenv", return_value=""), \
             patch("autox_tools.pipelines._artifacts_s3.boto3") as mock_boto3, \
             patch("autox_tools.pipelines.cli._discover_components",
                    return_value=["search-space"]), \
             patch("autox_tools.s3.cli._paginate_objects",
                    return_value={"Contents": comp_objects, "CommonPrefixes": []}):
            mock_boto3.client.return_value = mock_s3
            cli.cmd_artifacts(
                kfp_client,
                _artifacts_ns(component="search-space", download=str(tmp_path)),
            )

        out = capsys.readouterr().out
        assert "Downloaded" in out
        mock_s3.download_file.assert_called_once()

    def test_component_with_pattern_exits(self):
        kfp_client = self._setup_mocks()

        with patch.dict(os.environ, _ARTIFACTS_ENV, clear=False), \
             patch("autox_tools.pipelines._artifacts_s3.load_dotenv"), \
             patch("autox_tools.pipelines._artifacts_s3.find_dotenv", return_value=""), \
             patch("autox_tools.pipelines._artifacts_s3.boto3"), \
             pytest.raises(SystemExit, match="--component cannot be combined"):
            cli.cmd_artifacts(
                kfp_client,
                _artifacts_ns(component="search-space", pattern="P1"),
            )

    def test_component_json(self, capsys):
        kfp_client = self._setup_mocks()
        comp_objects = [
            {"Key": "pipeline/run-1/search-space/id1/file1.json", "Size": 100},
        ]

        with patch.dict(os.environ, _ARTIFACTS_ENV, clear=False), \
             patch("autox_tools.pipelines._artifacts_s3.load_dotenv"), \
             patch("autox_tools.pipelines._artifacts_s3.find_dotenv", return_value=""), \
             patch("autox_tools.pipelines._artifacts_s3.boto3"), \
             patch("autox_tools.pipelines.cli._discover_components",
                    return_value=["search-space"]), \
             patch("autox_tools.s3.cli._paginate_objects",
                    return_value={"Contents": comp_objects, "CommonPrefixes": []}):
            cli.cmd_artifacts(kfp_client, _artifacts_ns(component="search-space", json=True))

        import json
        result = json.loads(capsys.readouterr().out)
        assert result["component"] == "search-space"
        assert result["total"] == 1


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
        assert args.pattern is None
        assert args.artifact is None

    def test_artifacts_component_args(self):
        parser = cli._build_parser()
        args = parser.parse_args(["artifacts", "run-42", "--component", "search-space"])
        assert args.component == "search-space"
        assert args.pattern is None

    def test_artifacts_pattern_args(self):
        parser = cli._build_parser()
        args = parser.parse_args(["artifacts", "run-42", "--pattern", "P1", "--artifact", "eval.json"])
        assert args.pattern == "P1"
        assert args.artifact == "eval.json"
        assert args.print_content is False

    def test_artifacts_print_flag(self):
        parser = cli._build_parser()
        args = parser.parse_args([
            "artifacts", "run-42", "--pattern", "P1",
            "--artifact", "eval.json", "--print",
        ])
        assert args.print_content is True

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
