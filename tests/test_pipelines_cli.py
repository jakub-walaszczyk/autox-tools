"""Unit tests for the pipelines CLI tool.

All tests mock KFP and Kubernetes clients -- no cluster access required.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from autox_tools._output import format_duration
from autox_tools.pipelines import _artifacts as artifacts_mod
from autox_tools.pipelines import _filters, cli
from autox_tools.pipelines import _logs as logs_mod
from autox_tools.pipelines import _submit as submit_mod

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
        (60, "1m 0s"),
        (90, "1m 30s"),
        (3661, "1h 1m 1s"),
        (7200, "2h 0m 0s"),
    ])
    def test_formatting(self, seconds, expected):
        assert format_duration(seconds) == expected


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

        client.list_runs.assert_called_once_with(page_size=20, sort_by="created_at desc", experiment_id="exp-42")

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
        assert logs_mod._normalize_component_name("comp-documents-discovery") == "documents-discovery"

    def test_lowercases_and_replaces_underscores(self):
        assert logs_mod._normalize_component_name("Search_Space_Preparation") == "search-space-preparation"

    def test_noop_for_already_normalized(self):
        assert logs_mod._normalize_component_name("documents-discovery") == "documents-discovery"

    def test_strips_whitespace(self):
        assert logs_mod._normalize_component_name("  comp-foo  ") == "foo"


class TestMatchPodToTask:
    def test_exact_label_match(self):
        result = logs_mod._match_pod_to_task(
            "some-pod", {"component_name": "indexing"}, {}, {"indexing", "eval"},
        )
        assert result == "indexing"

    def test_v2_label_with_comp_prefix(self):
        result = logs_mod._match_pod_to_task(
            "some-pod",
            {"pipelines.kubeflow.org/v2_component_name": "comp-documents-discovery"},
            {},
            {"documents-discovery", "evaluation"},
        )
        assert result == "documents-discovery"

    def test_v2_annotation_with_comp_prefix(self):
        result = logs_mod._match_pod_to_task(
            "some-pod", {},
            {"pipelines.kubeflow.org/v2_component_name": "comp-search-space-preparation"},
            {"search-space-preparation"},
        )
        assert result == "search-space-preparation"

    def test_substring_in_pod_name(self):
        result = logs_mod._match_pod_to_task(
            "run-abc-rag-templates-optimization-xyz", {}, {},
            {"rag-templates-optimization"},
        )
        assert result == "rag-templates-optimization"

    def test_no_match_returns_none(self):
        result = logs_mod._match_pod_to_task(
            "pipeline-zc4fk-system-container-impl-123", {}, {},
            {"documents-discovery"},
        )
        assert result is None

    def test_underscore_task_name_matches_hyphenated_label(self):
        result = logs_mod._match_pod_to_task(
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
            logs_mod.cmd_logs(client, args, k8s_api=MagicMock())

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
            logs_mod.cmd_logs(client, args, k8s_api=k8s_api)

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
            logs_mod.cmd_logs(client, args, k8s_api=k8s_api)

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
            logs_mod.cmd_logs(client, args, k8s_api=k8s_api)

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
            logs_mod.cmd_logs(client, args, k8s_api=k8s_api)

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
            logs_mod.cmd_logs(client, args, k8s_api=k8s_api)

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
            logs_mod.cmd_logs(client, args, k8s_api=k8s_api)

        out = capsys.readouterr().out
        assert "fallback error output" in out
        assert "impl-456" in out

    def test_strategy1_resolves_driver_to_impl_pod(self, capsys):
        """When KFP reports a driver pod_name, logs should come from the impl pod."""
        tasks = [
            _make_task(
                "search-space-optimization", "Failed",
                error="exit 1",
                pod_name="pipeline-abc-system-container-driver-111",
            ),
        ]
        run = _make_run(state="Failed", task_details=tasks)

        driver_pod = MagicMock()
        driver_pod.metadata.name = "pipeline-abc-system-container-driver-111"
        driver_pod.metadata.labels = {"pipeline/runid": "test-id"}
        driver_pod.metadata.annotations = {}
        driver_pod.status.phase = "Succeeded"
        driver_pod.status.container_statuses = []
        d_main = MagicMock()
        d_main.name = "main"
        driver_pod.spec.containers = [d_main]

        impl_pod = MagicMock()
        impl_pod.metadata.name = "pipeline-abc-system-container-impl-222"
        impl_pod.metadata.labels = {}
        impl_pod.metadata.annotations = {}
        impl_pod.status.phase = "Failed"
        impl_pod.status.container_statuses = []
        i_main = MagicMock()
        i_main.name = "main"
        impl_pod.spec.containers = [i_main]

        k8s_api = MagicMock()
        pods_response = MagicMock()
        pods_response.items = [driver_pod, impl_pod]
        k8s_api.list_namespaced_pod.return_value = pods_response
        k8s_api.read_namespaced_pod_log.return_value = "actual user error"

        client = MagicMock()
        client.get_run.return_value = run

        with patch.dict(os.environ, {"RHOAI_PROJECT_NAME": "ns"}, clear=False):
            args = argparse.Namespace(run_id="test-id", tail=100, all=False, json=False)
            logs_mod.cmd_logs(client, args, k8s_api=k8s_api)

        out = capsys.readouterr().out
        assert "impl-222" in out
        assert "driver-111" not in out

    def test_strategy2_prefers_impl_over_driver(self, capsys):
        """Strategy 2 should pick the impl pod when both share the same component label."""
        tasks = [_make_task("documents-discovery", "Failed")]
        run = _make_run(state="Failed", task_details=tasks)

        driver_pod = MagicMock()
        driver_pod.metadata.name = "pipeline-zc4fk-system-container-driver-100"
        driver_pod.metadata.labels = {
            "pipelines.kubeflow.org/v2_component_name": "comp-documents-discovery",
            "pipeline/runid": "test-id",
        }
        driver_pod.metadata.annotations = {}
        driver_pod.status.phase = "Succeeded"
        driver_pod.status.container_statuses = []
        d_main = MagicMock()
        d_main.name = "main"
        driver_pod.spec.containers = [d_main]

        impl_pod = MagicMock()
        impl_pod.metadata.name = "pipeline-zc4fk-system-container-impl-200"
        impl_pod.metadata.labels = {
            "pipelines.kubeflow.org/v2_component_name": "comp-documents-discovery",
            "pipeline/runid": "test-id",
        }
        impl_pod.metadata.annotations = {}
        impl_pod.status.phase = "Failed"
        impl_pod.status.container_statuses = []
        i_main = MagicMock()
        i_main.name = "main"
        impl_pod.spec.containers = [i_main]

        k8s_api = MagicMock()
        pods_response = MagicMock()
        # Driver listed first — without the sort fix, it would be matched first.
        pods_response.items = [driver_pod, impl_pod]
        k8s_api.list_namespaced_pod.return_value = pods_response
        k8s_api.read_namespaced_pod_log.return_value = "impl error trace"

        client = MagicMock()
        client.get_run.return_value = run

        with patch.dict(os.environ, {"RHOAI_PROJECT_NAME": "ns"}, clear=False):
            args = argparse.Namespace(run_id="test-id", tail=100, all=False, json=False)
            logs_mod.cmd_logs(client, args, k8s_api=k8s_api)

        out = capsys.readouterr().out
        assert "impl-200" in out
        assert "driver-100" not in out

    def test_list_run_pods_merges_label_and_name_search(self):
        """_list_run_pods should find impl pods via name even when label search only returns drivers."""
        driver_pod = MagicMock()
        driver_pod.metadata.name = "pipeline-test-id-driver-aaa"
        driver_pod.metadata.labels = {"pipeline/runid": "test-id"}

        impl_pod = MagicMock()
        impl_pod.metadata.name = "pipeline-test-id-impl-bbb"
        impl_pod.metadata.labels = {}

        label_response = MagicMock()
        label_response.items = [driver_pod]

        all_response = MagicMock()
        all_response.items = [driver_pod, impl_pod, MagicMock(metadata=MagicMock(name="unrelated-pod"))]

        k8s_api = MagicMock()
        k8s_api.list_namespaced_pod.side_effect = [label_response, all_response]

        pods = logs_mod._list_run_pods(k8s_api, "ns", "test-id")
        pod_names = {p.metadata.name for p in pods}
        assert "pipeline-test-id-driver-aaa" in pod_names
        assert "pipeline-test-id-impl-bbb" in pod_names
        assert "unrelated-pod" not in pod_names


# ---------------------------------------------------------------------------
# cmd_artifacts (no S3 configured)
# ---------------------------------------------------------------------------

def _artifacts_ns(**overrides: Any) -> argparse.Namespace:
    """Build a Namespace for cmd_artifacts with defaults for all flags."""
    defaults: dict[str, Any] = {
        "run_id": "test-id", "component": None,
        "download": None, "json": False,
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
            artifacts_mod.cmd_artifacts(client, _artifacts_ns())

        out = capsys.readouterr().out
        assert "Artifacts S3 credentials not configured" in out
        assert "ARTIFACTS_AWS_S3_ENDPOINT" in out

    def test_json_without_artifacts_s3(self, capsys):
        run = _make_run(state="Succeeded")
        client = MagicMock()
        client.get_run.return_value = run

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ARTIFACTS_AWS_S3_ENDPOINT", None)
            artifacts_mod.cmd_artifacts(client, _artifacts_ns(json=True))

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
             patch("autox_tools._s3_connect.load_dotenv"), \
             patch("autox_tools._s3_connect.find_dotenv", return_value=""), \
             patch("autox_tools._s3_connect.boto3"), \
             pytest.raises(SystemExit, match="ARTIFACTS_S3_BUCKET is required"):
            artifacts_mod.cmd_artifacts(client, _artifacts_ns())



# ---------------------------------------------------------------------------
# _artifacts_s3.py tests
# ---------------------------------------------------------------------------

class TestArtifactsS3Connect:
    def test_missing_env_vars_exits(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("autox_tools._s3_connect.load_dotenv"), \
             patch("autox_tools._s3_connect.find_dotenv", return_value=""), \
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
             patch("autox_tools._s3_connect.load_dotenv"), \
             patch("autox_tools._s3_connect.find_dotenv", return_value=""), \
             patch("autox_tools._s3_connect.boto3") as mock_boto3:
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
             patch("autox_tools._s3_connect.load_dotenv"), \
             patch("autox_tools._s3_connect.find_dotenv", return_value=""), \
             patch("autox_tools._s3_connect.boto3") as mock_boto3:
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
             patch("autox_tools._s3_connect.load_dotenv"), \
             patch("autox_tools._s3_connect.find_dotenv", return_value=""), \
             patch("autox_tools._s3_connect.boto3") as mock_boto3:
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
        assert artifacts_mod._categorize_object(key) == expected

    @pytest.mark.parametrize("key", [
        "prefix/rag_patterns/P1/evaluation_results.json",
        "prefix/rag_patterns/P1/inference_notebook.ipynb",
        "prefix/rag_patterns/P1/indexing_notebook.ipynb",
        "prefix/rag_patterns/P1/leaderboard.html",
    ])
    def test_rag_patterns_takes_priority(self, key):
        """Files inside rag_patterns/ are always categorized as rag_patterns."""
        assert artifacts_mod._categorize_object(key) == "rag_patterns"


class TestMatchName:
    def test_exact_match(self):
        assert artifacts_mod._match_name("Pattern_1", ["Pattern_1", "Pattern_2"]) == ["Pattern_1"]

    def test_case_insensitive(self):
        assert artifacts_mod._match_name("pattern_1", ["Pattern_1", "Pattern_2"]) == ["Pattern_1"]

    def test_substring(self):
        result = artifacts_mod._match_name("Pattern", ["Pattern_1", "Pattern_2", "Default"])
        assert set(result) == {"Pattern_1", "Pattern_2"}

    def test_no_match(self):
        assert artifacts_mod._match_name("missing", ["Pattern_1", "Pattern_2"]) == []

    def test_exact_preferred(self):
        assert artifacts_mod._match_name("Default", ["Default", "DefaultV2"]) == ["Default"]


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
             patch("autox_tools._s3_connect.load_dotenv"), \
             patch("autox_tools._s3_connect.find_dotenv", return_value=""), \
             patch("autox_tools._s3_connect.boto3"), \
             patch("autox_tools.pipelines._artifacts.paginate_objects",
                    return_value={"Contents": objects, "CommonPrefixes": []}):
            artifacts_mod.cmd_artifacts(kfp_client, _artifacts_ns())

        out = capsys.readouterr().out
        assert "Evaluation Results" in out
        assert "RAG Patterns" in out
        assert "5 artifact(s)" in out

    def test_summary_json(self, capsys):
        objects = [
            {"Key": "pipeline/run-1/eval/evaluation_results.json", "Size": 1000},
        ]
        kfp_client = MagicMock()
        kfp_client.get_run.return_value = _mock_s3_run()

        with patch.dict(os.environ, _ARTIFACTS_ENV, clear=False), \
             patch("autox_tools._s3_connect.load_dotenv"), \
             patch("autox_tools._s3_connect.find_dotenv", return_value=""), \
             patch("autox_tools._s3_connect.boto3"), \
             patch("autox_tools.pipelines._artifacts.paginate_objects",
                    return_value={"Contents": objects, "CommonPrefixes": []}):
            artifacts_mod.cmd_artifacts(kfp_client, _artifacts_ns(json=True))

        import json
        result = json.loads(capsys.readouterr().out)
        assert result["total_artifacts"] == 1
        assert result["categories"]["evaluation"]["count"] == 1



# ---------------------------------------------------------------------------
# _discover_components and --component mode
# ---------------------------------------------------------------------------

class TestRefinePrefixForRun:
    def test_appends_run_id_when_objects_exist(self):
        s3 = MagicMock()
        with patch("autox_tools.pipelines._artifacts.paginate_objects",
                    return_value={"Contents": [{"Key": "pipe/run-1/comp/f", "Size": 1}]}):
            result = artifacts_mod._refine_prefix_for_run(s3, "bucket", "pipe/", "run-1")
        assert result == "pipe/run-1/"

    def test_keeps_original_when_no_objects(self):
        s3 = MagicMock()
        with patch("autox_tools.pipelines._artifacts.paginate_objects",
                    return_value={"Contents": []}):
            result = artifacts_mod._refine_prefix_for_run(s3, "bucket", "pipe/", "run-1")
        assert result == "pipe/"

    def test_noop_when_run_id_already_in_prefix(self):
        s3 = MagicMock()
        result = artifacts_mod._refine_prefix_for_run(s3, "bucket", "pipe/run-1/", "run-1")
        assert result == "pipe/run-1/"


class TestDiscoverComponents:
    def test_extracts_component_names(self):
        s3 = MagicMock()
        common_prefixes = [
            {"Prefix": "pipeline/run-1/rag-templates-optimization/"},
            {"Prefix": "pipeline/run-1/search-space-optimization/"},
            {"Prefix": "pipeline/run-1/data-preprocessing/"},
        ]
        with patch("autox_tools.pipelines._artifacts.paginate_objects",
                    return_value={"CommonPrefixes": common_prefixes}):
            result = artifacts_mod._discover_components(s3, "bucket", "pipeline/run-1/")
        assert result == ["data-preprocessing", "rag-templates-optimization", "search-space-optimization"]

    def test_empty_when_no_prefixes(self):
        s3 = MagicMock()
        with patch("autox_tools.pipelines._artifacts.paginate_objects",
                    return_value={"CommonPrefixes": []}):
            result = artifacts_mod._discover_components(s3, "bucket", "pipeline/run-1/")
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
             patch("autox_tools._s3_connect.load_dotenv"), \
             patch("autox_tools._s3_connect.find_dotenv", return_value=""), \
             patch("autox_tools._s3_connect.boto3"), \
             patch("autox_tools.pipelines._artifacts._refine_prefix_for_run", return_value="pipeline/run-1/"), \
             patch("autox_tools.pipelines._artifacts._discover_components",
                    return_value=["rag-templates-optimization", "search-space"]), \
             patch("autox_tools.pipelines._artifacts.paginate_objects",
                    return_value={"Contents": comp_objects, "CommonPrefixes": []}):
            artifacts_mod.cmd_artifacts(kfp_client, _artifacts_ns(component="search-space"))

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
             patch("autox_tools._s3_connect.load_dotenv"), \
             patch("autox_tools._s3_connect.find_dotenv", return_value=""), \
             patch("autox_tools._s3_connect.boto3"), \
             patch("autox_tools.pipelines._artifacts._discover_components",
                    return_value=["rag-templates-optimization", "search-space"]), \
             patch("autox_tools.pipelines._artifacts.paginate_objects", side_effect=mock_paginate):
            artifacts_mod.cmd_artifacts(kfp_client, _artifacts_ns(component="all"))

        out = capsys.readouterr().out
        assert "rag-templates-optimization" in out
        assert "search-space" in out
        assert "2 component(s)" in out

    def test_component_not_found_exits(self, capsys):
        kfp_client = self._setup_mocks()

        with patch.dict(os.environ, _ARTIFACTS_ENV, clear=False), \
             patch("autox_tools._s3_connect.load_dotenv"), \
             patch("autox_tools._s3_connect.find_dotenv", return_value=""), \
             patch("autox_tools._s3_connect.boto3"), \
             patch("autox_tools.pipelines._artifacts._refine_prefix_for_run", return_value="prefix/"), \
             patch("autox_tools.pipelines._artifacts._discover_components",
                    return_value=["rag-templates-optimization", "search-space"]), \
             pytest.raises(SystemExit):
            artifacts_mod.cmd_artifacts(kfp_client, _artifacts_ns(component="nonexistent"))

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
             patch("autox_tools._s3_connect.load_dotenv"), \
             patch("autox_tools._s3_connect.find_dotenv", return_value=""), \
             patch("autox_tools._s3_connect.boto3") as mock_boto3, \
             patch("autox_tools.pipelines._artifacts._discover_components",
                    return_value=["search-space"]), \
             patch("autox_tools.pipelines._artifacts.paginate_objects",
                    return_value={"Contents": comp_objects, "CommonPrefixes": []}):
            mock_boto3.client.return_value = mock_s3
            artifacts_mod.cmd_artifacts(
                kfp_client,
                _artifacts_ns(component="search-space", download=str(tmp_path)),
            )

        out = capsys.readouterr().out
        assert "Downloaded" in out
        mock_s3.download_file.assert_called_once()

    def test_component_json(self, capsys):
        kfp_client = self._setup_mocks()
        comp_objects = [
            {"Key": "pipeline/run-1/search-space/id1/file1.json", "Size": 100},
        ]

        with patch.dict(os.environ, _ARTIFACTS_ENV, clear=False), \
             patch("autox_tools._s3_connect.load_dotenv"), \
             patch("autox_tools._s3_connect.find_dotenv", return_value=""), \
             patch("autox_tools._s3_connect.boto3"), \
             patch("autox_tools.pipelines._artifacts._discover_components",
                    return_value=["search-space"]), \
             patch("autox_tools.pipelines._artifacts.paginate_objects",
                    return_value={"Contents": comp_objects, "CommonPrefixes": []}):
            artifacts_mod.cmd_artifacts(kfp_client, _artifacts_ns(component="search-space", json=True))

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
        args = parser.parse_args(["logs", "run-42", "--tail", "50", "--all"])
        assert args.command == "logs"
        assert args.run_id == "run-42"
        assert args.tail == 50
        assert args.all is True

    def test_artifacts_args(self):
        parser = cli._build_parser()
        args = parser.parse_args(["artifacts", "run-42", "--download", "/tmp/out"])
        assert args.command == "artifacts"
        assert args.run_id == "run-42"
        assert args.download == "/tmp/out"

    def test_artifacts_component_args(self):
        parser = cli._build_parser()
        args = parser.parse_args(["artifacts", "run-42", "--component", "search-space"])
        assert args.component == "search-space"

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
        assert args.tail == 100
        assert args.all is False

    def test_json_flag_position(self):
        parser = cli._build_parser()
        args = parser.parse_args(["--json", "status", "run-1"])
        assert args.json is True
        assert args.command == "status"

    def test_run_args(self):
        parser = cli._build_parser()
        args = parser.parse_args(["run", "config.json", "--watch", "--dry-run", "--run-name", "my-run"])
        assert args.command == "run"
        assert args.config == "config.json"
        assert args.watch is True
        assert args.dry_run is True
        assert args.run_name == "my-run"

    def test_run_override_args(self):
        parser = cli._build_parser()
        args = parser.parse_args(["run", "c.json", "--override", "k1=v1", "--override", "k2=v2"])
        assert args.override == ["k1=v1", "k2=v2"]

    def test_run_defaults(self):
        parser = cli._build_parser()
        args = parser.parse_args(["run", "c.json"])
        assert args.watch is False
        assert args.dry_run is False
        assert args.override is None
        assert args.run_name is None


# ---------------------------------------------------------------------------
# _load_run_config
# ---------------------------------------------------------------------------

class TestLoadRunConfig:
    def test_loads_valid_config(self, tmp_path):
        pipeline = tmp_path / "pipeline.yaml"
        pipeline.write_text("apiVersion: v1")
        config = tmp_path / "config.json"
        config.write_text(json.dumps({
            "pipeline_package": "pipeline.yaml",
            "experiment": "test-exp",
            "parameters": {"key": "value"},
        }))

        result = submit_mod._load_run_config(str(config))
        assert result["pipeline_package"] == str(pipeline)
        assert result["experiment"] == "test-exp"
        assert result["parameters"] == {"key": "value"}

    def test_resolves_relative_pipeline_path(self, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        pipeline = sub / "my-pipeline.yaml"
        pipeline.write_text("apiVersion: v1")
        config = sub / "config.json"
        config.write_text(json.dumps({"pipeline_package": "my-pipeline.yaml"}))

        result = submit_mod._load_run_config(str(config))
        assert result["pipeline_package"] == str(pipeline)

    def test_absolute_pipeline_path(self, tmp_path):
        pipeline = tmp_path / "pipeline.yaml"
        pipeline.write_text("apiVersion: v1")
        config = tmp_path / "config.json"
        config.write_text(json.dumps({"pipeline_package": str(pipeline)}))

        result = submit_mod._load_run_config(str(config))
        assert result["pipeline_package"] == str(pipeline)

    def test_missing_config_file_exits(self):
        with pytest.raises(SystemExit, match="Config file not found"):
            submit_mod._load_run_config("/nonexistent/config.json")

    def test_invalid_json_exits(self, tmp_path):
        config = tmp_path / "bad.json"
        config.write_text("{not valid json")
        with pytest.raises(SystemExit, match="Invalid JSON"):
            submit_mod._load_run_config(str(config))

    def test_non_object_json_exits(self, tmp_path):
        config = tmp_path / "array.json"
        config.write_text("[1, 2, 3]")
        with pytest.raises(SystemExit, match="must be a JSON object"):
            submit_mod._load_run_config(str(config))

    def test_missing_required_key_exits(self, tmp_path):
        config = tmp_path / "config.json"
        config.write_text(json.dumps({"experiment": "test"}))
        with pytest.raises(SystemExit, match="pipeline_package"):
            submit_mod._load_run_config(str(config))

    def test_pipeline_file_not_found_exits(self, tmp_path):
        config = tmp_path / "config.json"
        config.write_text(json.dumps({"pipeline_package": "nonexistent.yaml"}))
        with pytest.raises(SystemExit, match="Pipeline package not found"):
            submit_mod._load_run_config(str(config))

    def test_invalid_parameters_type_exits(self, tmp_path):
        pipeline = tmp_path / "pipeline.yaml"
        pipeline.write_text("apiVersion: v1")
        config = tmp_path / "config.json"
        config.write_text(json.dumps({
            "pipeline_package": "pipeline.yaml",
            "parameters": "not-a-dict",
        }))
        with pytest.raises(SystemExit, match="must be an object"):
            submit_mod._load_run_config(str(config))


# ---------------------------------------------------------------------------
# _apply_overrides
# ---------------------------------------------------------------------------

class TestApplyOverrides:
    def test_override_adds_parameter(self):
        config: dict[str, Any] = {"parameters": {"a": "1"}}
        submit_mod._apply_overrides(config, ["b=2"], None)
        assert config["parameters"] == {"a": "1", "b": "2"}

    def test_override_replaces_parameter(self):
        config: dict[str, Any] = {"parameters": {"model": "old"}}
        submit_mod._apply_overrides(config, ["model=new"], None)
        assert config["parameters"]["model"] == "new"

    def test_override_creates_parameters_dict(self):
        config: dict[str, Any] = {}
        submit_mod._apply_overrides(config, ["k=v"], None)
        assert config["parameters"] == {"k": "v"}

    def test_run_name_override(self):
        config: dict[str, Any] = {"run_name": "original"}
        submit_mod._apply_overrides(config, None, "overridden")
        assert config["run_name"] == "overridden"

    def test_invalid_override_format_exits(self):
        config: dict[str, Any] = {}
        with pytest.raises(SystemExit, match="Invalid --override format"):
            submit_mod._apply_overrides(config, ["no-equals-sign"], None)

    def test_override_preserves_value_with_equals(self):
        config: dict[str, Any] = {"parameters": {}}
        submit_mod._apply_overrides(config, ["path=s3://bucket/key=value"], None)
        assert config["parameters"]["path"] == "s3://bucket/key=value"

    def test_no_overrides_noop(self):
        config: dict[str, Any] = {"parameters": {"k": "v"}}
        submit_mod._apply_overrides(config, None, None)
        assert config == {"parameters": {"k": "v"}}


# ---------------------------------------------------------------------------
# cmd_run
# ---------------------------------------------------------------------------

class TestCmdRun:
    def _make_config(self, tmp_path, extra=None):
        pipeline = tmp_path / "pipeline.yaml"
        pipeline.write_text("apiVersion: v1")
        cfg = {
            "pipeline_package": str(pipeline),
            "experiment": "test-exp",
            "run_name": "test-run",
            "parameters": {"model": "granite-3b"},
        }
        if extra:
            cfg.update(extra)
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(cfg))
        return str(config_path)

    def test_dry_run_human_output(self, tmp_path, capsys):
        config_path = self._make_config(tmp_path)
        args = argparse.Namespace(
            config=config_path, watch=False, dry_run=True,
            override=None, run_name=None, json=False,
        )
        with patch.dict(os.environ, {"RHOAI_PROJECT_NAME": "ns"}, clear=False):
            submit_mod.cmd_run(None, args)

        out = capsys.readouterr().out
        assert "Dry run" in out
        assert "Pipeline" in out
        assert "test-exp" in out
        assert "granite-3b" in out

    def test_dry_run_json_output(self, tmp_path, capsys):
        config_path = self._make_config(tmp_path)
        args = argparse.Namespace(
            config=config_path, watch=False, dry_run=True,
            override=None, run_name=None, json=True,
        )
        with patch.dict(os.environ, {"RHOAI_PROJECT_NAME": "ns"}, clear=False):
            submit_mod.cmd_run(None, args)

        import json
        result = json.loads(capsys.readouterr().out)
        assert result["experiment"] == "test-exp"
        assert result["parameters"]["model"] == "granite-3b"
        assert result["run_name"] == "test-run"

    def test_dry_run_with_overrides(self, tmp_path, capsys):
        config_path = self._make_config(tmp_path)
        args = argparse.Namespace(
            config=config_path, watch=False, dry_run=True,
            override=["model=llama-70b", "new_param=42"], run_name="override-name",
            json=True,
        )
        with patch.dict(os.environ, {"RHOAI_PROJECT_NAME": "ns"}, clear=False):
            submit_mod.cmd_run(None, args)

        import json
        result = json.loads(capsys.readouterr().out)
        assert result["parameters"]["model"] == "llama-70b"
        assert result["parameters"]["new_param"] == "42"
        assert result["run_name"] == "override-name"

    def test_submit_calls_kfp(self, tmp_path, capsys):
        config_path = self._make_config(tmp_path)
        run_response = SimpleNamespace(run_id="new-run-id-123")
        kfp_client = MagicMock()
        kfp_client.create_run_from_pipeline_package.return_value = run_response

        args = argparse.Namespace(
            config=config_path, watch=False, dry_run=False,
            override=None, run_name=None, json=False,
        )
        with patch.dict(os.environ, {"RHOAI_PROJECT_NAME": "ns"}, clear=False):
            submit_mod.cmd_run(kfp_client, args)

        kfp_client.create_run_from_pipeline_package.assert_called_once()
        call_kwargs = kfp_client.create_run_from_pipeline_package.call_args[1]
        assert call_kwargs["experiment_name"] == "test-exp"
        assert call_kwargs["arguments"] == {"model": "granite-3b"}
        assert call_kwargs["run_name"] == "test-run"
        assert call_kwargs["namespace"] == "ns"

        out = capsys.readouterr().out
        assert "new-run-id-123" in out

    def test_submit_json_output(self, tmp_path, capsys):
        config_path = self._make_config(tmp_path)
        run_response = SimpleNamespace(run_id="new-run-id-456")
        kfp_client = MagicMock()
        kfp_client.create_run_from_pipeline_package.return_value = run_response

        args = argparse.Namespace(
            config=config_path, watch=False, dry_run=False,
            override=None, run_name=None, json=True,
        )
        with patch.dict(os.environ, {"RHOAI_PROJECT_NAME": "ns"}, clear=False):
            submit_mod.cmd_run(kfp_client, args)

        import json
        result = json.loads(capsys.readouterr().out)
        assert result["run_id"] == "new-run-id-456"
        assert result["experiment"] == "test-exp"

    def test_submit_with_service_account(self, tmp_path):
        config_path = self._make_config(tmp_path, extra={"service_account": "custom-sa"})
        run_response = SimpleNamespace(run_id="run-sa")
        kfp_client = MagicMock()
        kfp_client.create_run_from_pipeline_package.return_value = run_response

        args = argparse.Namespace(
            config=config_path, watch=False, dry_run=False,
            override=None, run_name=None, json=False,
        )
        with patch.dict(os.environ, {"RHOAI_PROJECT_NAME": "ns"}, clear=False):
            submit_mod.cmd_run(kfp_client, args)

        call_kwargs = kfp_client.create_run_from_pipeline_package.call_args[1]
        assert call_kwargs["service_account"] == "custom-sa"

    def test_submit_403_exits(self, tmp_path):
        config_path = self._make_config(tmp_path)
        kfp_client = MagicMock()
        kfp_client.create_run_from_pipeline_package.side_effect = Exception("403 Forbidden")

        args = argparse.Namespace(
            config=config_path, watch=False, dry_run=False,
            override=None, run_name=None, json=False,
        )
        with patch.dict(os.environ, {"RHOAI_PROJECT_NAME": "ns"}, clear=False), \
             pytest.raises(SystemExit, match="403 Forbidden"):
            submit_mod.cmd_run(kfp_client, args)

    def test_submit_no_client_exits(self, tmp_path):
        config_path = self._make_config(tmp_path)
        args = argparse.Namespace(
            config=config_path, watch=False, dry_run=False,
            override=None, run_name=None, json=False,
        )
        with pytest.raises(SystemExit, match="KFP client is required"):
            submit_mod.cmd_run(None, args)

    def test_submit_with_watch_delegates(self, tmp_path, capsys):
        config_path = self._make_config(tmp_path)
        run_response = SimpleNamespace(run_id="watch-run-id")
        kfp_client = MagicMock()
        kfp_client.create_run_from_pipeline_package.return_value = run_response

        watch_run = _make_run(state="Succeeded")
        kfp_client.get_run.return_value = watch_run

        args = argparse.Namespace(
            config=config_path, watch=True, dry_run=False,
            override=None, run_name=None, json=False,
        )
        with patch.dict(os.environ, {"RHOAI_PROJECT_NAME": "ns"}, clear=False), \
             pytest.raises(SystemExit) as exc_info:
            submit_mod.cmd_run(kfp_client, args)

        assert exc_info.value.code == 0
        kfp_client.get_run.assert_called_with("watch-run-id")

    def test_minimal_config(self, tmp_path, capsys):
        pipeline = tmp_path / "pipeline.yaml"
        pipeline.write_text("apiVersion: v1")
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"pipeline_package": str(pipeline)}))

        run_response = SimpleNamespace(run_id="minimal-run")
        kfp_client = MagicMock()
        kfp_client.create_run_from_pipeline_package.return_value = run_response

        args = argparse.Namespace(
            config=str(config_path), watch=False, dry_run=False,
            override=None, run_name=None, json=False,
        )
        with patch.dict(os.environ, {"RHOAI_PROJECT_NAME": ""}, clear=False):
            submit_mod.cmd_run(kfp_client, args)

        call_kwargs = kfp_client.create_run_from_pipeline_package.call_args[1]
        assert call_kwargs["experiment_name"] == "Default"
        assert call_kwargs["arguments"] is None
        assert "run_name" not in call_kwargs
