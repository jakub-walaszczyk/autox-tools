"""Unit tests for the experiments CLI tool.

All tests mock KFP and S3 clients -- no cluster or bucket access required.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from autox_tools.experiments import _artifacts, _display, _resolver, cli

# ---------------------------------------------------------------------------
# _artifacts.py tests
# ---------------------------------------------------------------------------


class TestCategorize:
    @pytest.mark.parametrize("key,expected", [
        ("prefix/evaluation_results.json", _artifacts.ArtifactCategory.EVALUATION_RESULTS),
        ("prefix/metrics.json", _artifacts.ArtifactCategory.MODEL_METRICS),
        ("prefix/indexing/pattern_001.ipynb", _artifacts.ArtifactCategory.INDEXING_NOTEBOOK),
        ("prefix/inference/pattern_001.ipynb", _artifacts.ArtifactCategory.INFERENCE_NOTEBOOK),
        ("prefix/leaderboard.html", _artifacts.ArtifactCategory.LEADERBOARD),
        ("prefix/results/leaderboard_v2.html", _artifacts.ArtifactCategory.LEADERBOARD),
        ("prefix/rag_patterns/pattern.json", _artifacts.ArtifactCategory.RAG_PATTERN),
        ("prefix/rag_patterns/P1/eval.json", _artifacts.ArtifactCategory.RAG_PATTERN),
        ("prefix/output/v1_responses_body.json", _artifacts.ArtifactCategory.RESPONSE_BODY),
        ("prefix/random_file.txt", _artifacts.ArtifactCategory.OTHER),
        ("prefix/data.csv", _artifacts.ArtifactCategory.OTHER),
    ])
    def test_categorization(self, key, expected):
        assert _artifacts.categorize(key) == expected

    def test_rag_patterns_takes_priority(self):
        """Files inside rag_patterns/ should always be RAG_PATTERN."""
        for key in [
            "pfx/rag_patterns/P1/evaluation_results.json",
            "pfx/rag_patterns/P1/inference_notebook.ipynb",
            "pfx/rag_patterns/P1/leaderboard.html",
        ]:
            assert _artifacts.categorize(key) == _artifacts.ArtifactCategory.RAG_PATTERN


class TestExtractMetrics:
    def test_flat_dict(self):
        data = {"accuracy": 0.95, "f1_score": 0.92, "model_name": "bert"}
        result = _artifacts.extract_metrics(data)
        assert result == {"accuracy": 0.95, "f1_score": 0.92}

    def test_nested_metrics_key(self):
        data = {
            "metrics": {"answer_correctness": 0.847, "faithfulness": 0.912},
            "best_pattern": "p1",
        }
        result = _artifacts.extract_metrics(data)
        assert result == {"answer_correctness": 0.847, "faithfulness": 0.912}

    def test_filters_non_numeric(self):
        data = {"accuracy": 0.9, "name": "exp-1", "labels": ["a", "b"], "count": 42}
        result = _artifacts.extract_metrics(data)
        assert result == {"accuracy": 0.9, "count": 42}

    def test_empty_dict(self):
        assert _artifacts.extract_metrics({}) == {}


class TestListAndCategorize:
    def test_categorizes_objects(self):
        objects = [
            {"Key": "pfx/evaluation_results.json", "Size": 1000, "LastModified": "2026-01-01"},
            {"Key": "pfx/leaderboard.html", "Size": 500, "LastModified": "2026-01-01"},
            {"Key": "pfx/", "Size": 0, "LastModified": "2026-01-01"},
        ]
        s3 = MagicMock()
        with patch("autox_tools.experiments._artifacts._paginate_objects",
                    return_value={"Contents": objects}):
            result = _artifacts.list_and_categorize(s3, "bucket", "pfx/")

        assert len(result) == 2
        assert result[0].category == _artifacts.ArtifactCategory.EVALUATION_RESULTS
        assert result[0].size_bytes == 1000
        assert result[1].category == _artifacts.ArtifactCategory.LEADERBOARD

    def test_empty_listing(self):
        s3 = MagicMock()
        with patch("autox_tools.experiments._artifacts._paginate_objects",
                    return_value={"Contents": []}):
            result = _artifacts.list_and_categorize(s3, "bucket", "pfx/")
        assert result == []

    def test_handles_datetime_last_modified(self):
        dt = datetime(2026, 5, 15, 14, 30, tzinfo=UTC)
        objects = [{"Key": "pfx/file.txt", "Size": 100, "LastModified": dt}]
        s3 = MagicMock()
        with patch("autox_tools.experiments._artifacts._paginate_objects",
                    return_value={"Contents": objects}):
            result = _artifacts.list_and_categorize(s3, "bucket", "pfx/")
        assert "2026" in result[0].last_modified


# ---------------------------------------------------------------------------
# _display.py tests
# ---------------------------------------------------------------------------


class TestFormatSize:
    @pytest.mark.parametrize("nbytes,expected", [
        (0, "0 B"),
        (512, "512 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1048576, "1.0 MB"),
        (1073741824, "1.0 GB"),
        (1099511627776, "1.0 TB"),
    ])
    def test_formatting(self, nbytes, expected):
        assert _display.format_size(nbytes) == expected


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
        assert _display.format_duration(seconds) == expected


class TestDeltaIndicator:
    def test_positive_higher_is_better(self):
        result = _display.delta_indicator(0.05, lower_is_better=False)
        assert result.startswith("+")
        assert "▲" in result

    def test_positive_lower_is_better(self):
        result = _display.delta_indicator(0.05, lower_is_better=True)
        assert result.startswith("+")
        assert "▼" in result

    def test_negative_higher_is_better(self):
        result = _display.delta_indicator(-0.05, lower_is_better=False)
        assert "▼" in result

    def test_negative_lower_is_better(self):
        result = _display.delta_indicator(-0.05, lower_is_better=True)
        assert "▲" in result

    def test_zero(self):
        result = _display.delta_indicator(0.0)
        assert "▲" not in result
        assert "▼" not in result


class TestIsLowerBetter:
    @pytest.mark.parametrize("name", [
        "latency_p50_ms", "error_rate", "loss", "training_time",
        "inference_duration", "api_cost",
    ])
    def test_lower_is_better(self, name):
        assert _display.is_lower_better(name) is True

    @pytest.mark.parametrize("name", [
        "accuracy", "answer_correctness", "faithfulness", "f1_score",
    ])
    def test_higher_is_better(self, name):
        assert _display.is_lower_better(name) is False


# ---------------------------------------------------------------------------
# _resolver.py tests
# ---------------------------------------------------------------------------


def _make_run_obj(pipeline_root=None, parameters=None, pipeline_name="my-pipeline"):
    """Build a mock KFP run object with runtime config."""
    rc = None
    if pipeline_root or parameters:
        rc = SimpleNamespace(
            pipeline_root=pipeline_root,
            parameters=parameters or {},
        )
    return SimpleNamespace(
        run=SimpleNamespace(
            runtime_config=rc,
            pipeline_spec={"pipeline_name": pipeline_name},
        ),
        run_details=SimpleNamespace(task_details=[]),
    )


class TestResolve:
    def test_explicit_prefix(self):
        kfp = MagicMock()
        s3 = MagicMock()

        with patch.dict(os.environ, {"ARTIFACTS_S3_BUCKET": "my-bucket"}):
            loc = _resolver.resolve(
                kfp, s3, "run-1",
                explicit_prefix="custom/prefix/",
            )

        assert loc is not None
        assert loc.prefix == "custom/prefix/"
        assert loc.bucket == "my-bucket"
        assert loc.source == "explicit"
        kfp.get_run.assert_not_called()

    def test_explicit_prefix_with_bucket(self):
        kfp = MagicMock()
        s3 = MagicMock()

        loc = _resolver.resolve(
            kfp, s3, "run-1",
            explicit_prefix="pfx/", explicit_bucket="other-bucket",
        )

        assert loc is not None
        assert loc.bucket == "other-bucket"

    def test_explicit_prefix_no_bucket_returns_none(self):
        kfp = MagicMock()
        s3 = MagicMock()

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ARTIFACTS_S3_BUCKET", None)
            loc = _resolver.resolve(kfp, s3, "run-1", explicit_prefix="pfx/")

        assert loc is None

    def test_run_params_s3_url(self):
        run = _make_run_obj(pipeline_root="s3://art-bucket/pipeline/run-1/")
        kfp = MagicMock()
        kfp.get_run.return_value = run
        s3 = MagicMock()

        with patch("autox_tools.experiments._resolver._paginate_objects",
                    return_value={"Contents": []}):
            loc = _resolver.resolve(kfp, s3, "run-1")

        assert loc is not None
        assert loc.bucket == "art-bucket"
        assert loc.source == "run_params"

    def test_run_params_with_prefix_refinement(self):
        run = _make_run_obj(pipeline_root="s3://bucket/pipeline/")
        kfp = MagicMock()
        kfp.get_run.return_value = run
        s3 = MagicMock()

        def mock_paginate(client, bucket, prefix, **kw):
            if "run-1/" in prefix:
                return {"Contents": [{"Key": f"{prefix}file.json", "Size": 1}]}
            return {"Contents": []}

        with patch("autox_tools.experiments._resolver._paginate_objects",
                    side_effect=mock_paginate):
            loc = _resolver.resolve(kfp, s3, "run-1")

        assert loc is not None
        assert loc.prefix == "pipeline/run-1/"

    def test_run_params_output_parameter(self):
        run = _make_run_obj(parameters={"output": "s3://bucket/output/run-1/"})
        kfp = MagicMock()
        kfp.get_run.return_value = run
        s3 = MagicMock()

        with patch("autox_tools.experiments._resolver._paginate_objects",
                    return_value={"Contents": []}):
            loc = _resolver.resolve(kfp, s3, "run-1")

        assert loc is not None
        assert loc.bucket == "bucket"
        assert "output" in loc.prefix

    def test_scan_fallback(self):
        run = _make_run_obj()
        kfp = MagicMock()
        kfp.get_run.return_value = run
        s3 = MagicMock()

        def mock_paginate(client, bucket, prefix, **kw):
            if prefix == "artifacts/run-1/":
                return {"Contents": [{"Key": "artifacts/run-1/results.json", "Size": 1}]}
            return {"Contents": []}

        with patch.dict(os.environ, {"ARTIFACTS_S3_BUCKET": "scan-bucket"}), \
             patch("autox_tools.experiments._resolver._paginate_objects",
                   side_effect=mock_paginate):
            loc = _resolver.resolve(kfp, s3, "run-1")

        assert loc is not None
        assert loc.bucket == "scan-bucket"
        assert loc.prefix == "artifacts/run-1/"
        assert loc.source == "scan"

    def test_scan_with_pipeline_name(self):
        run = _make_run_obj(pipeline_name="autorag-pipeline")
        kfp = MagicMock()
        kfp.get_run.return_value = run
        s3 = MagicMock()

        def mock_paginate(client, bucket, prefix, **kw):
            if prefix == "autorag-pipeline/run-1/":
                return {"Contents": [{"Key": f"{prefix}f.json", "Size": 1}]}
            return {"Contents": []}

        with patch.dict(os.environ, {"ARTIFACTS_S3_BUCKET": "bucket"}), \
             patch("autox_tools.experiments._resolver._paginate_objects",
                   side_effect=mock_paginate):
            loc = _resolver.resolve(kfp, s3, "run-1")

        assert loc is not None
        assert loc.prefix == "autorag-pipeline/run-1/"

    def test_no_artifacts_returns_none(self):
        run = _make_run_obj()
        kfp = MagicMock()
        kfp.get_run.return_value = run
        s3 = MagicMock()

        with patch.dict(os.environ, {}, clear=True), \
             patch("autox_tools.experiments._resolver._paginate_objects",
                   return_value={"Contents": []}):
            os.environ.pop("ARTIFACTS_S3_BUCKET", None)
            loc = _resolver.resolve(kfp, s3, "run-1")

        assert loc is None


# ---------------------------------------------------------------------------
# CLI parser tests
# ---------------------------------------------------------------------------


class TestParser:
    def test_results_args(self):
        parser = cli._build_parser()
        args = parser.parse_args(["results", "abc-123", "--prefix", "pfx/", "--bucket", "bkt"])
        assert args.command == "results"
        assert args.run_id == "abc-123"
        assert args.prefix == "pfx/"
        assert args.bucket == "bkt"

    def test_compare_args(self):
        parser = cli._build_parser()
        args = parser.parse_args([
            "compare", "run-1", "run-2",
            "--metrics", "acc,f1", "--prefix1", "p1/", "--prefix2", "p2/",
        ])
        assert args.command == "compare"
        assert args.run_id_1 == "run-1"
        assert args.run_id_2 == "run-2"
        assert args.metrics == "acc,f1"
        assert args.prefix1 == "p1/"
        assert args.prefix2 == "p2/"

    def test_export_args(self):
        parser = cli._build_parser()
        args = parser.parse_args(["export", "run-1", "-o", "/tmp/out"])
        assert args.command == "export"
        assert args.run_id == "run-1"
        assert args.output == "/tmp/out"

    def test_info_args(self):
        parser = cli._build_parser()
        args = parser.parse_args(["info", "run-1"])
        assert args.command == "info"
        assert args.run_id == "run-1"

    def test_json_flag(self):
        parser = cli._build_parser()
        args = parser.parse_args(["--json", "results", "run-1"])
        assert args.json is True

    def test_export_defaults(self):
        parser = cli._build_parser()
        args = parser.parse_args(["export", "run-1"])
        assert args.output is None
        assert args.prefix is None
        assert args.bucket is None

    def test_compare_defaults(self):
        parser = cli._build_parser()
        args = parser.parse_args(["compare", "r1", "r2"])
        assert args.metrics is None
        assert args.prefix1 is None
        assert args.prefix2 is None
        assert args.bucket is None


# ---------------------------------------------------------------------------
# Fixtures for command tests
# ---------------------------------------------------------------------------


def _mock_resolve(bucket="test-bucket", prefix="pipeline/run-1/"):
    """Patch resolver.resolve to return a canned location."""
    return patch(
        "autox_tools.experiments.cli.resolve",
        return_value=_resolver.ArtifactLocation(
            bucket=bucket, prefix=prefix, source="run_params",
        ),
    )


def _eval_results_json(**overrides) -> dict:
    """Build a sample evaluation results dict."""
    data: dict[str, Any] = {
        "answer_correctness": 0.847,
        "faithfulness": 0.912,
        "context_relevancy": 0.783,
        "latency_p50_ms": 245,
        "patterns_evaluated": 4,
        "best_pattern": "pattern_003",
    }
    data.update(overrides)
    return data


def _s3_get_object(data: dict) -> dict:
    """Build a mock S3 get_object response."""
    return {"Body": BytesIO(json.dumps(data).encode())}


def _results_ns(**overrides) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "run_id": "a3f1b2c4-dead-beef-1234-567890abcdef",
        "prefix": None, "bucket": None, "json": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _compare_ns(**overrides) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "run_id_1": "run-aaa", "run_id_2": "run-bbb",
        "metrics": None, "prefix1": None, "prefix2": None,
        "bucket": None, "json": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _export_ns(**overrides) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "run_id": "a3f1b2c4-dead-beef-1234-567890abcdef",
        "output": None, "prefix": None, "bucket": None, "json": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _info_ns(**overrides) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "run_id": "a3f1b2c4-dead-beef-1234-567890abcdef",
        "prefix": None, "bucket": None, "json": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# cmd_results
# ---------------------------------------------------------------------------


class TestCmdResults:
    def test_human_output(self, capsys):
        kfp = MagicMock()
        s3 = MagicMock()
        data = _eval_results_json()
        s3.get_object.return_value = _s3_get_object(data)

        with _mock_resolve():
            cli.cmd_results(kfp, s3, _results_ns())

        out = capsys.readouterr().out
        assert "Experiment Results" in out
        assert "answer_correctness" in out
        assert "0.8470" in out
        assert "faithfulness" in out
        assert "Patterns evaluated: 4" in out
        assert "Best pattern: pattern_003" in out

    def test_json_output(self, capsys):
        kfp = MagicMock()
        s3 = MagicMock()
        data = _eval_results_json()
        s3.get_object.return_value = _s3_get_object(data)

        with _mock_resolve():
            cli.cmd_results(kfp, s3, _results_ns(json=True))

        result = json.loads(capsys.readouterr().out)
        assert result["answer_correctness"] == 0.847
        assert result["faithfulness"] == 0.912

    def test_no_location_exits(self):
        kfp = MagicMock()
        s3 = MagicMock()

        with patch("autox_tools.experiments.cli.resolve", return_value=None), \
             pytest.raises(SystemExit, match="Could not locate"):
            cli.cmd_results(kfp, s3, _results_ns())

    def test_no_results_file_exits(self):
        kfp = MagicMock()
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("NoSuchKey")

        with _mock_resolve(), \
             pytest.raises(SystemExit, match=r"No evaluation_results\.json"):
            cli.cmd_results(kfp, s3, _results_ns())

    def test_tries_multiple_subpaths(self):
        kfp = MagicMock()
        s3 = MagicMock()
        data = _eval_results_json()
        call_count = 0

        def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if "evaluation/evaluation_results.json" in kwargs["Key"]:
                return _s3_get_object(data)
            raise Exception("NoSuchKey")

        s3.get_object.side_effect = side_effect

        with _mock_resolve():
            cli.cmd_results(kfp, s3, _results_ns())

        assert call_count >= 2


# ---------------------------------------------------------------------------
# cmd_compare
# ---------------------------------------------------------------------------


class TestCmdCompare:
    def _setup_compare(self, metrics1, metrics2):
        kfp = MagicMock()
        s3 = MagicMock()

        def mock_resolve(kfp_c, s3_c, run_id, **kw):
            prefix = f"pipeline/{run_id}/"
            return _resolver.ArtifactLocation("bucket", prefix, "run_params")

        def mock_get(**kwargs):
            key = kwargs["Key"]
            if "run-aaa" in key:
                return _s3_get_object(metrics1)
            return _s3_get_object(metrics2)

        s3.get_object.side_effect = mock_get
        return kfp, s3, mock_resolve

    def test_human_output(self, capsys):
        m1 = {"accuracy": 0.9, "latency_p50_ms": 100}
        m2 = {"accuracy": 0.85, "latency_p50_ms": 80}
        kfp, s3, mock_res = self._setup_compare(m1, m2)

        with patch("autox_tools.experiments.cli.resolve", side_effect=mock_res):
            cli.cmd_compare(kfp, s3, _compare_ns())

        out = capsys.readouterr().out
        assert "Comparison" in out
        assert "accuracy" in out
        assert "latency_p50_ms" in out
        assert "▲" in out or "▼" in out

    def test_json_output(self, capsys):
        m1 = {"accuracy": 0.9}
        m2 = {"accuracy": 0.85}
        kfp, s3, mock_resolve = self._setup_compare(m1, m2)

        with patch("autox_tools.experiments.cli.resolve", side_effect=mock_resolve):
            cli.cmd_compare(kfp, s3, _compare_ns(json=True))

        result = json.loads(capsys.readouterr().out)
        assert result["run_1"]["id"] == "run-aaa"
        assert result["run_2"]["id"] == "run-bbb"
        assert "accuracy" in result["deltas"]
        assert result["deltas"]["accuracy"] == pytest.approx(-0.05)

    def test_metrics_filter(self, capsys):
        m1 = {"accuracy": 0.9, "f1": 0.8, "loss": 0.1}
        m2 = {"accuracy": 0.85, "f1": 0.82, "loss": 0.12}
        kfp, s3, mock_resolve = self._setup_compare(m1, m2)

        with patch("autox_tools.experiments.cli.resolve", side_effect=mock_resolve):
            cli.cmd_compare(kfp, s3, _compare_ns(metrics="accuracy,f1"))

        out = capsys.readouterr().out
        assert "accuracy" in out
        assert "f1" in out
        assert "loss" not in out

    def test_different_metric_sets(self, capsys):
        m1 = {"accuracy": 0.9, "only_in_run1": 0.5}
        m2 = {"accuracy": 0.85, "only_in_run2": 0.7}
        kfp, s3, mock_resolve = self._setup_compare(m1, m2)

        with patch("autox_tools.experiments.cli.resolve", side_effect=mock_resolve):
            cli.cmd_compare(kfp, s3, _compare_ns())

        out = capsys.readouterr().out
        assert "only_in_run1" in out
        assert "only_in_run2" in out
        assert "—" in out

    def test_no_location_exits(self):
        kfp = MagicMock()
        s3 = MagicMock()

        with patch("autox_tools.experiments.cli.resolve", return_value=None), \
             pytest.raises(SystemExit, match="Could not locate"):
            cli.cmd_compare(kfp, s3, _compare_ns())


# ---------------------------------------------------------------------------
# cmd_export
# ---------------------------------------------------------------------------


class TestCmdExport:
    def test_downloads_and_categorizes(self, capsys):
        kfp = MagicMock()
        s3 = MagicMock()
        artifacts = [
            _artifacts.CategorizedArtifact(
                "pfx/evaluation_results.json",
                _artifacts.ArtifactCategory.EVALUATION_RESULTS, 1000, "2026-01-01",
            ),
            _artifacts.CategorizedArtifact(
                "pfx/leaderboard.html",
                _artifacts.ArtifactCategory.LEADERBOARD, 500, "2026-01-01",
            ),
        ]

        with _mock_resolve(prefix="pfx/"), \
             patch("autox_tools.experiments.cli.list_and_categorize", return_value=artifacts), \
             tempfile.TemporaryDirectory() as tmpdir:
            cli.cmd_export(kfp, s3, _export_ns(output=tmpdir))

        out = capsys.readouterr().out
        assert "Evaluation Results" in out
        assert "Leaderboard" in out
        assert "2 files" in out
        assert "Downloaded" in out
        assert s3.download_file.call_count == 2

    def test_no_location_exits(self):
        kfp = MagicMock()
        s3 = MagicMock()

        with patch("autox_tools.experiments.cli.resolve", return_value=None), \
             pytest.raises(SystemExit, match="Could not locate"):
            cli.cmd_export(kfp, s3, _export_ns())

    def test_no_artifacts_exits(self):
        kfp = MagicMock()
        s3 = MagicMock()

        with _mock_resolve(), \
             patch("autox_tools.experiments.cli.list_and_categorize", return_value=[]), \
             pytest.raises(SystemExit, match="No artifacts"):
            cli.cmd_export(kfp, s3, _export_ns())

    def test_default_output_dir(self, capsys):
        kfp = MagicMock()
        s3 = MagicMock()
        artifacts = [
            _artifacts.CategorizedArtifact(
                "pfx/file.txt",
                _artifacts.ArtifactCategory.OTHER, 100, "2026-01-01",
            ),
        ]

        with _mock_resolve(prefix="pfx/"), \
             patch("autox_tools.experiments.cli.list_and_categorize", return_value=artifacts), \
             tempfile.TemporaryDirectory(), \
             patch("autox_tools.experiments.cli.os.makedirs"):
            s3.download_file = MagicMock()
            cli.cmd_export(kfp, s3, _export_ns())

        out = capsys.readouterr().out
        assert "experiment-a3f1b2c4" in out


# ---------------------------------------------------------------------------
# cmd_info
# ---------------------------------------------------------------------------


class TestCmdInfo:
    def _make_run(self, state="Succeeded"):
        created = datetime(2026, 5, 15, 14, 30, 0, tzinfo=UTC)
        finished = created + timedelta(minutes=12, seconds=34)
        run_obj = SimpleNamespace(
            run_id="a3f1b2c4-dead-beef-1234-567890abcdef",
            state=state,
            display_name="autorag-func-test-20260515",
            name="autorag-func-test-20260515",
            created_at=created,
            finished_at=finished,
            error=None,
            pipeline_spec={"pipeline_name": "autorag-pipeline"},
            runtime_config=SimpleNamespace(
                pipeline_root="s3://bucket/pipeline/run/",
                parameters={},
            ),
        )
        return SimpleNamespace(run=run_obj, run_details=SimpleNamespace(task_details=[]))

    def test_human_output(self, capsys):
        kfp = MagicMock()
        kfp.get_run.return_value = self._make_run()
        s3 = MagicMock()
        artifacts = [
            _artifacts.CategorizedArtifact(
                "pfx/eval.json",
                _artifacts.ArtifactCategory.EVALUATION_RESULTS, 1000, "2026-01-01",
            ),
        ]

        with _mock_resolve(), \
             patch("autox_tools.experiments.cli.list_and_categorize", return_value=artifacts):
            cli.cmd_info(kfp, s3, _info_ns())

        out = capsys.readouterr().out
        assert "a3f1b2c4-dead-beef" in out
        assert "autorag-func-test-20260515" in out
        assert "Succeeded" in out
        assert "12m 34s" in out
        assert "1 files" in out

    def test_json_output(self, capsys):
        kfp = MagicMock()
        kfp.get_run.return_value = self._make_run()
        s3 = MagicMock()

        with _mock_resolve(), \
             patch("autox_tools.experiments.cli.list_and_categorize", return_value=[]):
            cli.cmd_info(kfp, s3, _info_ns(json=True))

        result = json.loads(capsys.readouterr().out)
        assert result["state"] == "Succeeded"
        assert result["duration"] == "12m 34s"
        assert result["artifact_count"] == 0

    def test_no_artifacts_location(self, capsys):
        kfp = MagicMock()
        kfp.get_run.return_value = self._make_run()
        s3 = MagicMock()

        with patch("autox_tools.experiments.cli.resolve", return_value=None):
            cli.cmd_info(kfp, s3, _info_ns())

        out = capsys.readouterr().out
        assert "could not resolve" in out

    def test_run_fetch_failure_exits(self):
        kfp = MagicMock()
        kfp.get_run.side_effect = Exception("connection refused")
        s3 = MagicMock()

        with pytest.raises(SystemExit, match="Failed to get run"):
            cli.cmd_info(kfp, s3, _info_ns())
