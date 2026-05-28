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

from autox_tools.autorag import _artifacts, _display, _patterns, _resolver, cli

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

    def test_list_of_dicts(self):
        data = [
            {"accuracy": 0.8, "f1_score": 0.7, "name": "p1"},
            {"accuracy": 0.9, "f1_score": 0.8, "name": "p2"},
        ]
        result = _artifacts.extract_metrics(data)
        assert result["accuracy"] == pytest.approx(0.85)
        assert result["f1_score"] == pytest.approx(0.75)
        assert "name" not in result

    def test_empty_list(self):
        assert _artifacts.extract_metrics([]) == {}

    def test_list_of_non_dicts(self):
        assert _artifacts.extract_metrics(["a", "b"]) == {}

    def test_empty_dict(self):
        assert _artifacts.extract_metrics({}) == {}

    def test_scores_structure(self):
        data = {
            "final_score": 0.88,
            "duration_seconds": 42.0,
            "scores": {
                "faithfulness": {"mean": 0.88, "ci_low": 0.8, "ci_high": 0.95},
                "accuracy": {"mean": 0.92, "ci_low": 0.85, "ci_high": 0.98},
            },
        }
        result = _artifacts.extract_metrics(data)
        assert result == {"faithfulness": 0.88, "accuracy": 0.92}
        assert "final_score" not in result
        assert "duration_seconds" not in result

    def test_scores_with_null_mean_skipped(self):
        data = {
            "scores": {
                "accuracy": {"mean": 0.9, "ci_low": None, "ci_high": None},
                "bad_metric": {"mean": None, "ci_low": None, "ci_high": None},
            },
        }
        result = _artifacts.extract_metrics(data)
        assert result == {"accuracy": 0.9}

    def test_empty_scores_falls_back(self):
        data = {"scores": {}, "accuracy": 0.9}
        result = _artifacts.extract_metrics(data)
        assert result == {"accuracy": 0.9}


class TestListAndCategorize:
    def test_categorizes_objects(self):
        objects = [
            {"Key": "pfx/evaluation_results.json", "Size": 1000, "LastModified": "2026-01-01"},
            {"Key": "pfx/leaderboard.html", "Size": 500, "LastModified": "2026-01-01"},
            {"Key": "pfx/", "Size": 0, "LastModified": "2026-01-01"},
        ]
        s3 = MagicMock()
        with patch("autox_tools.autorag._artifacts._paginate_objects",
                    return_value={"Contents": objects}):
            result = _artifacts.list_and_categorize(s3, "bucket", "pfx/")

        assert len(result) == 2
        assert result[0].category == _artifacts.ArtifactCategory.EVALUATION_RESULTS
        assert result[0].size_bytes == 1000
        assert result[1].category == _artifacts.ArtifactCategory.LEADERBOARD

    def test_empty_listing(self):
        s3 = MagicMock()
        with patch("autox_tools.autorag._artifacts._paginate_objects",
                    return_value={"Contents": []}):
            result = _artifacts.list_and_categorize(s3, "bucket", "pfx/")
        assert result == []

    def test_handles_datetime_last_modified(self):
        dt = datetime(2026, 5, 15, 14, 30, tzinfo=UTC)
        objects = [{"Key": "pfx/file.txt", "Size": 100, "LastModified": dt}]
        s3 = MagicMock()
        with patch("autox_tools.autorag._artifacts._paginate_objects",
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


class TestShortId:
    def test_uuid(self):
        assert _display.short_id("a3f1b2c4-dead-beef-1234-567890abcdef") == "a3f1b2c4"

    def test_short_id_unchanged(self):
        assert _display.short_id("run-1") == "run-1"

    def test_exactly_eight(self):
        assert _display.short_id("12345678") == "12345678"


class TestFilterMetrics:
    def test_excludes_max_combinations(self):
        metrics = {"accuracy": 0.9, "max_combinations": 100}
        assert _display.filter_metric_dict(metrics) == {"accuracy": 0.9}

    def test_excludes_iteration(self):
        metrics = {"f1": 0.8, "iteration": 5}
        assert _display.filter_metric_dict(metrics) == {"f1": 0.8}

    def test_excludes_duration_keyword(self):
        metrics = {"accuracy": 0.9, "total_duration_s": 45.2, "duration": 30.0}
        assert _display.filter_metric_dict(metrics) == {"accuracy": 0.9}

    def test_preserves_normal_metrics(self):
        metrics = {"accuracy": 0.9, "f1_score": 0.85, "faithfulness": 0.8}
        assert _display.filter_metric_dict(metrics) == metrics

    def test_empty_dict(self):
        assert _display.filter_metric_dict({}) == {}

    def test_filter_metric_names(self):
        names = ["accuracy", "max_combinations", "iteration", "f1_score", "duration"]
        assert _display.filter_metric_names(names) == ["accuracy", "f1_score"]


class TestFormatRunHeader:
    def test_with_display_name(self):
        result = _display.format_run_header("run-123", "My Experiment")
        assert "My Experiment" in result
        assert "run-123" in result

    def test_without_display_name(self):
        result = _display.format_run_header("run-123")
        assert "run-123" in result

    def test_with_source_key(self):
        result = _display.format_run_header("run-123", source_key="s3://b/k")
        assert "s3://b/k" in result

    def test_separator_width(self):
        result = _display.format_run_header("run-123")
        first_line = result.splitlines()[0]
        assert len(first_line) == 100


class TestFormatLeaderboard:
    def test_ranks_by_primary_metric(self):
        patterns = [
            _patterns.PatternMetrics("P1", {"accuracy": 0.9, "f1": 0.8}, {}),
            _patterns.PatternMetrics("P2", {"accuracy": 0.95, "f1": 0.85}, {}),
            _patterns.PatternMetrics("P3", {"accuracy": 0.88, "f1": 0.82}, {}),
        ]
        result = _display.format_leaderboard(patterns, "accuracy", ["accuracy", "f1"])
        lines = result.splitlines()
        pattern_lines = [ln for ln in lines if "P" in ln and "0." in ln]
        assert "P2" in pattern_lines[0]
        assert "P1" in pattern_lines[1]
        assert "P3" in pattern_lines[2]

    def test_empty_patterns(self):
        result = _display.format_leaderboard([], "accuracy")
        assert "no pattern data" in result

    def test_lower_is_better_ranking(self):
        patterns = [
            _patterns.PatternMetrics("P1", {"latency": 100}, {}),
            _patterns.PatternMetrics("P2", {"latency": 50}, {}),
        ]
        result = _display.format_leaderboard(patterns, "latency", ["latency"])
        lines = result.splitlines()
        pattern_lines = [ln for ln in lines if "P" in ln and ("100" in ln or "50" in ln)]
        assert "P2" in pattern_lines[0]


class TestFormatPatternDetail:
    def test_formats_metrics(self):
        p = _patterns.PatternMetrics("Pattern1", {"accuracy": 0.95, "f1": 0.88}, {})
        result = _display.format_pattern_detail(p)
        assert "Pattern1" in result
        assert "0.9500" in result
        assert "0.8800" in result

    def test_empty_metrics(self):
        p = _patterns.PatternMetrics("Pattern1", {}, {})
        result = _display.format_pattern_detail(p)
        assert "no metrics" in result


class TestFormatCompareHeader:
    def test_with_names(self):
        result = _display.format_compare_header("r1", "r2", "Baseline", "New Config")
        assert "Baseline" in result
        assert "New Config" in result

    def test_without_names_uses_short_id(self):
        result = _display.format_compare_header(
            "72cdd9f0-1d2f-4571-8219-d008b2c4316a",
            "511849b7-339e-4c62-ab14-f5ec07a3e722",
        )
        assert "72cdd9f0" in result
        assert "511849b7" in result

    def test_shows_full_ids_when_using_short_labels(self):
        id1 = "72cdd9f0-1d2f-4571-8219-d008b2c4316a"
        id2 = "511849b7-339e-4c62-ab14-f5ec07a3e722"
        result = _display.format_compare_header(id1, id2)
        assert f"Run A: {id1}" in result
        assert f"Run B: {id2}" in result

    def test_shows_full_ids_when_using_names(self):
        id1 = "72cdd9f0-1d2f-4571-8219-d008b2c4316a"
        id2 = "511849b7-339e-4c62-ab14-f5ec07a3e722"
        result = _display.format_compare_header(id1, id2, "Baseline", "New Config")
        assert f"Run A: {id1}" in result
        assert f"Run B: {id2}" in result

    def test_separator_width(self):
        result = _display.format_compare_header("r1", "r2")
        first_line = result.splitlines()[0]
        assert len(first_line) == 100


class TestFormatSummaryMetrics:
    def test_primary_metric_first(self):
        metrics = {"zebra": 0.1, "answer_correctness": 0.85, "alpha": 0.5}
        result = _display.format_summary_metrics(metrics, primary_metric="answer_correctness")
        lines = [ln.strip() for ln in result.splitlines() if "0." in ln]
        assert "answer_correctness" in lines[0]
        assert "alpha" in lines[1]
        assert "zebra" in lines[2]

    def test_without_primary_metric_alphabetical(self):
        metrics = {"zebra": 0.1, "alpha": 0.5}
        result = _display.format_summary_metrics(metrics)
        lines = [ln.strip() for ln in result.splitlines() if "0." in ln]
        assert "alpha" in lines[0]
        assert "zebra" in lines[1]

    def test_optimization_label_in_title(self):
        metrics = {"accuracy": 0.9}
        result = _display.format_summary_metrics(metrics, primary_metric="accuracy")
        assert "optimization: accuracy" in result

    def test_empty_metrics(self):
        result = _display.format_summary_metrics({})
        assert "no summary metrics" in result


class TestFormatPatternSettings:
    def test_shows_non_metric_keys(self):
        p = _patterns.PatternMetrics(
            "P1",
            {"accuracy": 0.9, "f1": 0.85},
            {"accuracy": 0.9, "f1": 0.85, "model": "gpt-4", "embedding": "all-MiniLM"},
        )
        result = _display.format_pattern_settings(p)
        assert "model" in result
        assert "gpt-4" in result
        assert "embedding" in result
        assert "all-MiniLM" in result
        assert "accuracy" not in result

    def test_empty_raw_data(self):
        p = _patterns.PatternMetrics("P1", {"accuracy": 0.9}, {})
        result = _display.format_pattern_settings(p)
        assert "no settings data" in result

    def test_all_keys_are_metrics(self):
        p = _patterns.PatternMetrics(
            "P1", {"accuracy": 0.9}, {"accuracy": 0.9},
        )
        result = _display.format_pattern_settings(p)
        assert "no settings data" in result

    def test_excludes_metrics_subkey(self):
        p = _patterns.PatternMetrics(
            "P1",
            {"accuracy": 0.9},
            {"metrics": {"accuracy": 0.9}, "model": "gpt-4"},
        )
        result = _display.format_pattern_settings(p)
        assert "model" in result
        assert "gpt-4" in result
        assert "metrics" not in result.split("model")[0]

    def test_dict_value_formatting(self):
        p = _patterns.PatternMetrics(
            "P1", {}, {"config": {"k": 5, "temp": 0.7}},
        )
        result = _display.format_pattern_settings(p)
        assert "config" in result
        assert "k: 5" in result

    def test_list_value_formatting(self):
        p = _patterns.PatternMetrics(
            "P1", {}, {"labels": ["a", "b", "c"]},
        )
        result = _display.format_pattern_settings(p)
        assert "labels" in result
        assert "a" in result

    def test_nested_settings_structure(self):
        p = _patterns.PatternMetrics(
            "P1",
            {"faithfulness": 0.88},
            {
                "faithfulness": 0.88,
                "scores": {"faithfulness": {"mean": 0.88}},
                "final_score": 0.88,
                "duration_seconds": 8.0,
                "name": "P1",
                "settings": {
                    "chunking": {"method": "recursive", "chunk_size": 2048},
                    "embedding": {"model_id": "bge-m3", "distance_metric": "cosine"},
                },
            },
        )
        result = _display.format_pattern_settings(p)
        assert "Chunking:" in result
        assert "recursive" in result
        assert "chunk_size" in result
        assert "Embedding:" in result
        assert "bge-m3" in result
        assert "duration_seconds" not in result
        assert "final_score" not in result
        assert "scores" not in result.lower().split("chunking")[0]


class TestFormatPipelineParams:
    def test_formats_simple_params(self):
        params = {"model": "gpt-4", "temperature": 0.7}
        result = _display.format_pipeline_params(params)
        assert "Pipeline Parameters" in result
        assert "model" in result
        assert "gpt-4" in result
        assert "temperature" in result
        assert "0.7" in result

    def test_formats_list_values(self):
        params = {"models": ["gpt-4", "gpt-3.5"]}
        result = _display.format_pipeline_params(params)
        assert "models" in result
        assert "gpt-4, gpt-3.5" in result

    def test_formats_dict_values(self):
        params = {"config": {"k": 5, "temp": 0.7}}
        result = _display.format_pipeline_params(params)
        assert "config" in result
        assert "k: 5" in result

    def test_empty_params(self):
        assert _display.format_pipeline_params({}) == ""

    def test_sorted_keys(self):
        params = {"zebra": "z", "alpha": "a"}
        result = _display.format_pipeline_params(params)
        lines = result.splitlines()
        param_lines = [line for line in lines if "alpha" in line or "zebra" in line]
        assert "alpha" in param_lines[0]
        assert "zebra" in param_lines[1]


class TestFormatBestPatterns:
    def test_single_best_pattern(self):
        patterns = [
            _patterns.PatternMetrics("P1", {"accuracy": 0.8}, {"model": "v1"}),
            _patterns.PatternMetrics("P2", {"accuracy": 0.95}, {"model": "v2"}),
        ]
        result = _display.format_best_patterns(patterns, "accuracy", n=1)
        assert "Top Pattern: P2" in result
        assert "accuracy: 0.9500" in result
        assert "model" in result
        assert "v2" in result

    def test_multiple_best_patterns(self):
        patterns = [
            _patterns.PatternMetrics("P1", {"accuracy": 0.8}, {"model": "v1"}),
            _patterns.PatternMetrics("P2", {"accuracy": 0.95}, {"model": "v2"}),
            _patterns.PatternMetrics("P3", {"accuracy": 0.9}, {"model": "v3"}),
        ]
        result = _display.format_best_patterns(patterns, "accuracy", n=2)
        assert "#1 P2" in result
        assert "#2 P3" in result
        assert "Top Pattern" not in result

    def test_empty_patterns(self):
        result = _display.format_best_patterns([], "accuracy")
        assert result == ""

    def test_no_sort_metric(self):
        patterns = [_patterns.PatternMetrics("P1", {"acc": 0.9}, {})]
        result = _display.format_best_patterns(patterns, None)
        assert result == ""


class TestFormatRunHeaderEnhanced:
    def test_shows_pipeline_name(self):
        result = _display.format_run_header(
            "run-123", "My Experiment",
            pipeline_name="autorag-pipeline",
        )
        assert "autorag-pipeline" in result
        assert "Pipeline:" in result

    def test_shows_state(self):
        result = _display.format_run_header(
            "run-123", state="Succeeded",
        )
        assert "Succeeded" in result
        assert "State:" in result

    def test_shows_duration(self):
        result = _display.format_run_header(
            "run-123", duration_seconds=754.0,
        )
        assert "12m 34s" in result
        assert "Duration:" in result

    def test_shows_created_at(self):
        dt = datetime(2026, 5, 15, 14, 30, 0, tzinfo=UTC)
        result = _display.format_run_header(
            "run-123", created_at=dt,
        )
        assert "2026-05-15 14:30 UTC" in result

    def test_full_header(self):
        dt = datetime(2026, 5, 15, 14, 30, 0, tzinfo=UTC)
        result = _display.format_run_header(
            "a3f1b2c4-dead-beef-1234-567890abcdef",
            "My Experiment",
            pipeline_name="autorag-pipeline",
            state="Succeeded",
            duration_seconds=754.0,
            created_at=dt,
        )
        assert "My Experiment" in result
        assert "a3f1b2c4-dead-beef" in result
        assert "autorag-pipeline" in result
        assert "Succeeded" in result
        assert "12m 34s" in result
        assert "2026-05-15 14:30 UTC" in result


class TestFormatCompareHeaderEnhanced:
    def test_shows_run_details(self):
        result = _display.format_compare_header(
            "r1", "r2", "Baseline", "New Config",
            pipeline1="autorag", pipeline2="autorag",
            state1="Succeeded", state2="Succeeded",
            duration1=600.0, duration2=754.0,
        )
        assert "Baseline" in result
        assert "New Config" in result
        assert "autorag" in result
        assert "Succeeded" in result

    def test_omits_empty_details(self):
        result = _display.format_compare_header("r1", "r2")
        assert "Run A:" not in result or "Run B:" not in result


class TestFormatLeaderboardPrimaryFirst:
    def test_primary_metric_first_column(self):
        patterns = [
            _patterns.PatternMetrics("P1", {"accuracy": 0.9, "f1": 0.8, "alpha": 0.7}, {}),
        ]
        result = _display.format_leaderboard(
            patterns, "accuracy", ["alpha", "f1", "accuracy"],
        )
        header_line = next(
            line for line in result.splitlines()
            if "accuracy" in line and "f1" in line
        )
        acc_pos = header_line.index("accuracy")
        f1_pos = header_line.index("f1")
        assert acc_pos < f1_pos


# ---------------------------------------------------------------------------
# _patterns.py tests
# ---------------------------------------------------------------------------


class TestNaturalSortKey:
    def test_numeric_aware(self):
        names = ["Pattern11", "Pattern2", "Pattern1", "Pattern10", "Pattern3"]
        result = sorted(names, key=_patterns.natural_sort_key)
        assert result == ["Pattern1", "Pattern2", "Pattern3", "Pattern10", "Pattern11"]

    def test_alphabetic_fallback(self):
        names = ["beta", "alpha", "gamma"]
        result = sorted(names, key=_patterns.natural_sort_key)
        assert result == ["alpha", "beta", "gamma"]

    def test_case_insensitive(self):
        names = ["B1", "a1", "A2"]
        result = sorted(names, key=_patterns.natural_sort_key)
        assert result == ["a1", "A2", "B1"]


class TestParseNames:
    def test_single_entry(self):
        assert _patterns.parse_names("abc=My Run") == {"abc": "My Run"}

    def test_multiple_entries(self):
        result = _patterns.parse_names("id1=Baseline,id2=New Config")
        assert result == {"id1": "Baseline", "id2": "New Config"}

    def test_empty(self):
        assert _patterns.parse_names(None) == {}
        assert _patterns.parse_names("") == {}

    def test_whitespace_handling(self):
        result = _patterns.parse_names("  id1 = Name 1 , id2 = Name 2  ")
        assert result == {"id1": "Name 1", "id2": "Name 2"}

    def test_value_with_equals(self):
        result = _patterns.parse_names("id=a=b")
        assert result == {"id": "a=b"}


class TestDetectPrimaryMetric:
    def test_prefers_answer_correctness(self):
        metrics = {"f1_score": 0.9, "answer_correctness": 0.85, "accuracy": 0.8}
        assert _patterns.detect_primary_metric(metrics) == "answer_correctness"

    def test_falls_back_to_accuracy(self):
        metrics = {"f1_score": 0.9, "accuracy": 0.8}
        assert _patterns.detect_primary_metric(metrics) == "accuracy"

    def test_falls_back_to_sorted_first(self):
        metrics = {"zebra_metric": 0.5, "alpha_metric": 0.9}
        assert _patterns.detect_primary_metric(metrics) == "alpha_metric"

    def test_empty(self):
        assert _patterns.detect_primary_metric({}) is None

    def test_considers_pattern_metrics(self):
        patterns = [
            _patterns.PatternMetrics("P1", {"answer_correctness": 0.8}, {}),
        ]
        assert _patterns.detect_primary_metric({}, patterns) == "answer_correctness"

    def test_uses_optimization_metric_from_params(self):
        metrics = {"final_score": 0.9, "duration_seconds": 42.0}
        params = {"optimization_metric": "final_score"}
        assert _patterns.detect_primary_metric(metrics, pipeline_params=params) == "final_score"

    def test_excludes_duration_from_fallback(self):
        metrics = {"duration_seconds": 42.0, "final_score": 0.9}
        result = _patterns.detect_primary_metric(metrics)
        assert result == "final_score"

    def test_optimization_metric_not_in_pool_ignored(self):
        metrics = {"accuracy": 0.9}
        params = {"optimization_metric": "nonexistent"}
        assert _patterns.detect_primary_metric(metrics, pipeline_params=params) == "accuracy"


class TestPatternDiscovery:
    def test_find_rag_patterns_prefix(self):
        s3 = MagicMock()
        objects = [
            {"Key": "pfx/comp/task-id/rag_patterns/P1/pattern.json", "Size": 100},
        ]
        with patch("autox_tools.autorag._patterns._paginate_objects",
                    return_value={"Contents": objects}):
            result = _patterns.find_rag_patterns_prefix(s3, "bucket", "pfx/")
        assert result == "pfx/comp/task-id/rag_patterns/"

    def test_find_rag_patterns_prefix_not_found(self):
        s3 = MagicMock()
        with patch("autox_tools.autorag._patterns._paginate_objects",
                    return_value={"Contents": []}):
            result = _patterns.find_rag_patterns_prefix(s3, "bucket", "pfx/")
        assert result is None

    def test_discover_patterns_natural_sorted(self):
        s3 = MagicMock()
        common_prefixes = [
            {"Prefix": "rag/Pattern11/"},
            {"Prefix": "rag/Pattern2/"},
            {"Prefix": "rag/Pattern1/"},
        ]
        with patch("autox_tools.autorag._patterns._paginate_objects",
                    return_value={"CommonPrefixes": common_prefixes}):
            result = _patterns.discover_patterns(s3, "bucket", "rag/")
        assert result == ["Pattern1", "Pattern2", "Pattern11"]


class TestExtractPatternScores:
    def test_extracts_from_scores_structure(self):
        data = {
            "name": "Pattern4",
            "duration_seconds": 8.35,
            "final_score": 0.8886,
            "scores": {
                "answer_correctness": {"mean": 0.9178, "ci_low": 0.8611, "ci_high": 0.9734},
                "faithfulness": {"mean": 0.8886, "ci_low": 0.8142, "ci_high": 0.9659},
                "context_correctness": {"mean": 1.0, "ci_low": None, "ci_high": None},
            },
        }
        result = _patterns._extract_pattern_scores(data)
        assert result == {
            "answer_correctness": 0.9178,
            "faithfulness": 0.8886,
            "context_correctness": 1.0,
        }
        assert "duration_seconds" not in result
        assert "final_score" not in result

    def test_falls_back_to_extract_metrics(self):
        data = {"accuracy": 0.9, "f1_score": 0.85, "name": "P1"}
        result = _patterns._extract_pattern_scores(data)
        assert result == {"accuracy": 0.9, "f1_score": 0.85}

    def test_empty_scores_dict_falls_back(self):
        data = {"scores": {}, "accuracy": 0.9}
        result = _patterns._extract_pattern_scores(data)
        assert "accuracy" in result


class TestDetectFinalScoreMetric:
    def test_matches_faithfulness(self):
        data = {"final_score": 0.8886}
        metrics = {"answer_correctness": 0.9178, "faithfulness": 0.8886}
        assert _patterns._detect_final_score_metric(data, metrics) == "faithfulness"

    def test_no_final_score(self):
        data = {"name": "P1"}
        metrics = {"accuracy": 0.9}
        assert _patterns._detect_final_score_metric(data, metrics) is None

    def test_no_match(self):
        data = {"final_score": 0.5}
        metrics = {"accuracy": 0.9, "f1": 0.8}
        assert _patterns._detect_final_score_metric(data, metrics) is None


class TestDetectPrimaryMetricFinalScore:
    def test_detects_from_final_score(self):
        patterns = [
            _patterns.PatternMetrics(
                "P1",
                {"answer_correctness": 0.9178, "faithfulness": 0.8886},
                {"final_score": 0.8886},
            ),
        ]
        result = _patterns.detect_primary_metric({}, patterns)
        assert result == "faithfulness"


class TestFetchPatternMetrics:
    def test_fetches_and_extracts(self):
        s3 = MagicMock()
        data = {"accuracy": 0.92, "f1_score": 0.85, "name": "P1"}
        s3.get_object.return_value = {"Body": BytesIO(json.dumps(data).encode())}

        result = _patterns.fetch_pattern_metrics(s3, "bucket", "rag/", "Pattern1")
        assert result is not None
        assert result.name == "Pattern1"
        assert result.metrics == {"accuracy": 0.92, "f1_score": 0.85}

    def test_fetches_scores_structure(self):
        s3 = MagicMock()
        data = {
            "name": "Pattern1",
            "final_score": 0.8886,
            "scores": {
                "faithfulness": {"mean": 0.8886, "ci_low": 0.8, "ci_high": 0.95},
                "accuracy": {"mean": 0.92, "ci_low": 0.85, "ci_high": 0.98},
            },
        }
        s3.get_object.return_value = {"Body": BytesIO(json.dumps(data).encode())}

        result = _patterns.fetch_pattern_metrics(s3, "bucket", "rag/", "Pattern1")
        assert result is not None
        assert result.metrics == {"faithfulness": 0.8886, "accuracy": 0.92}
        assert "final_score" not in result.metrics
        assert result.raw_data["final_score"] == 0.8886

    def test_returns_none_on_failure(self):
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("NoSuchKey")
        result = _patterns.fetch_pattern_metrics(s3, "bucket", "rag/", "P1")
        assert result is None

    def test_fetch_all_orchestrates(self):
        s3 = MagicMock()
        data = {"accuracy": 0.9}

        def mock_get(**kwargs):
            return {"Body": BytesIO(json.dumps(data).encode())}

        s3.get_object.side_effect = mock_get

        with patch("autox_tools.autorag._patterns.find_rag_patterns_prefix",
                    return_value="pfx/rag_patterns/"), \
             patch("autox_tools.autorag._patterns.discover_patterns",
                    return_value=["P1", "P2"]):
            result = _patterns.fetch_all_pattern_metrics(s3, "bucket", "pfx/")

        assert len(result) == 2
        assert result[0].name == "P1"
        assert result[1].name == "P2"

    def test_fetch_all_no_rag_prefix(self):
        s3 = MagicMock()
        with patch("autox_tools.autorag._patterns.find_rag_patterns_prefix",
                    return_value=None):
            result = _patterns.fetch_all_pattern_metrics(s3, "bucket", "pfx/")
        assert result == []


class TestExtractRunMetadata:
    def test_extracts_all_fields(self):
        created = datetime(2026, 5, 15, 14, 30, 0, tzinfo=UTC)
        finished = created + timedelta(minutes=12, seconds=34)
        run_obj = SimpleNamespace(
            display_name="my-experiment",
            state="Succeeded",
            created_at=created,
            finished_at=finished,
            pipeline_spec={"pipeline_name": "autorag-pipeline"},
            runtime_config=SimpleNamespace(
                pipeline_root=None,
                parameters={
                    "optimization_metric": "faithfulness",
                    "output": "s3://bucket/output/",
                },
            ),
        )
        kfp = MagicMock()
        kfp.get_run.return_value = SimpleNamespace(run=run_obj)

        result = _patterns._extract_run_metadata(kfp, "run-1")
        assert result["pipeline_name"] == "autorag-pipeline"
        assert result["state"] == "Succeeded"
        assert result["display_name"] == "my-experiment"
        assert result["duration_seconds"] == pytest.approx(754.0)
        assert result["pipeline_params"] == {"optimization_metric": "faithfulness"}
        assert "output" not in result["pipeline_params"]

    def test_handles_kfp_failure(self):
        kfp = MagicMock()
        kfp.get_run.side_effect = Exception("connection refused")

        result = _patterns._extract_run_metadata(kfp, "run-1")
        assert result["pipeline_name"] is None
        assert result["state"] is None
        assert result["pipeline_params"] == {}

    def test_handles_missing_runtime_config(self):
        run_obj = SimpleNamespace(
            display_name="test",
            state="Running",
            created_at=None,
            finished_at=None,
            pipeline_spec=None,
            runtime_config=None,
        )
        kfp = MagicMock()
        kfp.get_run.return_value = SimpleNamespace(run=run_obj)

        result = _patterns._extract_run_metadata(kfp, "run-1")
        assert result["state"] == "Running"
        assert result["pipeline_params"] == {}
        assert result["duration_seconds"] is None


class TestCollectRunData:
    def test_collects_summary_and_patterns(self):
        kfp = MagicMock()
        s3 = MagicMock()
        location = _resolver.ArtifactLocation("bucket", "pfx/", "run_params")
        summary = {"answer_correctness": 0.85, "faithfulness": 0.9}
        patterns = [
            _patterns.PatternMetrics("P1", {"answer_correctness": 0.8}, {}),
            _patterns.PatternMetrics("P2", {"answer_correctness": 0.9}, {}),
        ]

        with patch("autox_tools.autorag._patterns.resolve", return_value=location), \
             patch("autox_tools.autorag._patterns._find_summary_results",
                   return_value=(summary, "pfx/evaluation_results.json")), \
             patch("autox_tools.autorag._patterns.fetch_all_pattern_metrics",
                   return_value=patterns):
            result = _patterns.collect_run_data(kfp, s3, "run-1")

        assert result is not None
        assert result.run_id == "run-1"
        assert result.summary_metrics == summary
        assert len(result.patterns) == 2
        assert result.primary_metric == "answer_correctness"

    def test_returns_none_on_no_location(self):
        kfp = MagicMock()
        s3 = MagicMock()
        with patch("autox_tools.autorag._patterns.resolve", return_value=None):
            result = _patterns.collect_run_data(kfp, s3, "run-1")
        assert result is None

    def test_returns_none_on_resolve_exception(self):
        kfp = MagicMock()
        s3 = MagicMock()
        with patch("autox_tools.autorag._patterns.resolve",
                    side_effect=Exception("connection refused")):
            result = _patterns.collect_run_data(kfp, s3, "run-1")
        assert result is None

    def test_uses_display_name_from_names(self):
        kfp = MagicMock()
        s3 = MagicMock()
        location = _resolver.ArtifactLocation("bucket", "pfx/", "run_params")

        with patch("autox_tools.autorag._patterns.resolve", return_value=location), \
             patch("autox_tools.autorag._patterns._find_summary_results",
                   return_value=({}, None)), \
             patch("autox_tools.autorag._patterns.fetch_all_pattern_metrics",
                   return_value=[]):
            result = _patterns.collect_run_data(
                kfp, s3, "run-1", names={"run-1": "My Experiment"},
            )

        assert result is not None
        assert result.display_name == "My Experiment"

    def test_derives_summary_from_best_pattern(self):
        kfp = MagicMock()
        s3 = MagicMock()
        location = _resolver.ArtifactLocation("bucket", "pfx/", "run_params")
        patterns = [
            _patterns.PatternMetrics("P1", {"accuracy": 0.8, "f1": 0.7}, {}),
            _patterns.PatternMetrics("P2", {"accuracy": 0.95, "f1": 0.9}, {}),
        ]

        with patch("autox_tools.autorag._patterns.resolve", return_value=location), \
             patch("autox_tools.autorag._patterns._find_summary_results",
                   return_value=(None, None)), \
             patch("autox_tools.autorag._patterns.fetch_all_pattern_metrics",
                   return_value=patterns):
            result = _patterns.collect_run_data(kfp, s3, "run-1")

        assert result is not None
        assert result.summary_metrics == {"accuracy": 0.95, "f1": 0.9}

    def test_graceful_no_summary_no_patterns(self):
        kfp = MagicMock()
        s3 = MagicMock()
        location = _resolver.ArtifactLocation("bucket", "pfx/", "run_params")

        with patch("autox_tools.autorag._patterns.resolve", return_value=location), \
             patch("autox_tools.autorag._patterns._find_summary_results",
                   return_value=(None, None)), \
             patch("autox_tools.autorag._patterns.fetch_all_pattern_metrics",
                   return_value=[]):
            result = _patterns.collect_run_data(kfp, s3, "run-1")

        assert result is not None
        assert result.summary_metrics == {}
        assert result.patterns == []


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

        with patch("autox_tools.autorag._resolver._paginate_objects",
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

        with patch("autox_tools.autorag._resolver._paginate_objects",
                    side_effect=mock_paginate):
            loc = _resolver.resolve(kfp, s3, "run-1")

        assert loc is not None
        assert loc.prefix == "pipeline/run-1/"

    def test_run_params_output_parameter(self):
        run = _make_run_obj(parameters={"output": "s3://bucket/output/run-1/"})
        kfp = MagicMock()
        kfp.get_run.return_value = run
        s3 = MagicMock()

        with patch("autox_tools.autorag._resolver._paginate_objects",
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
             patch("autox_tools.autorag._resolver._paginate_objects",
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
             patch("autox_tools.autorag._resolver._paginate_objects",
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
             patch("autox_tools.autorag._resolver._paginate_objects",
                   return_value={"Contents": []}):
            os.environ.pop("ARTIFACTS_S3_BUCKET", None)
            loc = _resolver.resolve(kfp, s3, "run-1")

        assert loc is None

    def test_minio_url(self):
        run = _make_run_obj(pipeline_root="minio://minio-bucket/artifacts/run-1/")
        kfp = MagicMock()
        kfp.get_run.return_value = run
        s3 = MagicMock()

        with patch("autox_tools.autorag._resolver._paginate_objects",
                    return_value={"Contents": []}):
            loc = _resolver.resolve(kfp, s3, "run-1")

        assert loc is not None
        assert loc.bucket == "minio-bucket"
        assert "artifacts" in loc.prefix

    def test_https_endpoint_url(self):
        run = _make_run_obj(
            pipeline_root="https://minio.apps.cluster.example.com/bucket/pfx/run-1/",
        )
        kfp = MagicMock()
        kfp.get_run.return_value = run
        s3 = MagicMock()

        with patch("autox_tools.autorag._resolver._paginate_objects",
                    return_value={"Contents": []}):
            loc = _resolver.resolve(kfp, s3, "run-1")

        assert loc is not None
        assert loc.bucket == "bucket"
        assert "pfx" in loc.prefix

    def test_pipeline_scan_templates(self):
        run = _make_run_obj(pipeline_name="my-pipeline")
        kfp = MagicMock()
        kfp.get_run.return_value = run
        s3 = MagicMock()

        def mock_paginate(client, bucket, prefix, **kw):
            if prefix == "pipelines/my-pipeline/run-1/":
                return {"Contents": [{"Key": f"{prefix}f.json", "Size": 1}]}
            return {"Contents": []}

        with patch.dict(os.environ, {"ARTIFACTS_S3_BUCKET": "bucket"}), \
             patch("autox_tools.autorag._resolver._paginate_objects",
                   side_effect=mock_paginate):
            loc = _resolver.resolve(kfp, s3, "run-1")

        assert loc is not None
        assert loc.prefix == "pipelines/my-pipeline/run-1/"


class TestParseObjectUrl:
    @pytest.mark.parametrize("url,expected", [
        ("s3://bucket/prefix/", ("bucket", "prefix/")),
        ("s3://bucket/", ("bucket", "")),
        ("s3://bucket", ("bucket", "")),
        ("minio://minio-bkt/pfx/run/", ("minio-bkt", "pfx/run/")),
        ("https://host.example.com/bucket/pfx/", ("bucket", "pfx/")),
        ("http://minio:9000/bucket/dir/file", ("bucket", "dir/file")),
    ])
    def test_recognised_schemes(self, url, expected):
        assert _resolver._parse_object_url(url) == expected

    @pytest.mark.parametrize("url", [
        "/local/path",
        "gs://bucket/prefix",
        "",
    ])
    def test_unrecognised_schemes(self, url):
        assert _resolver._parse_object_url(url) is None


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

    def test_results_pdf_flag(self):
        parser = cli._build_parser()
        args = parser.parse_args(["results", "run-1", "--pdf", "report.pdf"])
        assert args.pdf == "report.pdf"

    def test_results_names_flag(self):
        parser = cli._build_parser()
        args = parser.parse_args(["results", "run-1", "--names", "run-1=My Run"])
        assert args.names == "run-1=My Run"

    def test_results_sort_by_flag(self):
        parser = cli._build_parser()
        args = parser.parse_args(["results", "run-1", "--sort-by", "accuracy"])
        assert args.sort_by == "accuracy"

    def test_compare_pdf_flag(self):
        parser = cli._build_parser()
        args = parser.parse_args(["compare", "r1", "r2", "--pdf", "cmp.pdf"])
        assert args.pdf == "cmp.pdf"

    def test_compare_names_flag(self):
        parser = cli._build_parser()
        args = parser.parse_args(["compare", "r1", "r2", "--names", "r1=A,r2=B"])
        assert args.names == "r1=A,r2=B"

    def test_results_detailed_flag(self):
        parser = cli._build_parser()
        args = parser.parse_args(["results", "run-1", "--detailed"])
        assert args.detailed is True

    def test_results_detailed_short_flag(self):
        parser = cli._build_parser()
        args = parser.parse_args(["results", "run-1", "-d"])
        assert args.detailed is True

    def test_results_detailed_default(self):
        parser = cli._build_parser()
        args = parser.parse_args(["results", "run-1"])
        assert args.detailed is False

    def test_results_top_n_flag(self):
        parser = cli._build_parser()
        args = parser.parse_args(["results", "run-1", "--top-n", "3"])
        assert args.top_n == 3

    def test_results_top_n_default(self):
        parser = cli._build_parser()
        args = parser.parse_args(["results", "run-1"])
        assert args.top_n == 1

    def test_compare_detailed_flag(self):
        parser = cli._build_parser()
        args = parser.parse_args(["compare", "r1", "r2", "--detailed"])
        assert args.detailed is True

    def test_compare_detailed_default(self):
        parser = cli._build_parser()
        args = parser.parse_args(["compare", "r1", "r2"])
        assert args.detailed is False

    def test_artifacts_args(self):
        parser = cli._build_parser()
        args = parser.parse_args(["artifacts", "run-1", "--pattern", "Pattern1"])
        assert args.command == "artifacts"
        assert args.run_id == "run-1"
        assert args.pattern == "Pattern1"

    def test_artifacts_all_patterns(self):
        parser = cli._build_parser()
        args = parser.parse_args(["artifacts", "run-1", "--pattern", "all"])
        assert args.pattern == "all"

    def test_artifacts_with_artifact_and_print(self):
        parser = cli._build_parser()
        args = parser.parse_args([
            "artifacts", "run-1",
            "--pattern", "P1", "--artifact", "pattern.json", "--print",
        ])
        assert args.artifact == "pattern.json"
        assert args.print_content is True

    def test_artifacts_download(self):
        parser = cli._build_parser()
        args = parser.parse_args(["artifacts", "run-1", "--download", "/tmp/out"])
        assert args.download == "/tmp/out"

    def test_artifacts_defaults(self):
        parser = cli._build_parser()
        args = parser.parse_args(["artifacts", "run-1"])
        assert args.pattern is None
        assert args.artifact is None
        assert args.print_content is False
        assert args.download is None

    def test_artifacts_prefix_and_bucket(self):
        parser = cli._build_parser()
        args = parser.parse_args([
            "artifacts", "run-1", "--prefix", "pfx/", "--bucket", "bkt",
        ])
        assert args.prefix == "pfx/"
        assert args.bucket == "bkt"


# ---------------------------------------------------------------------------
# Fixtures for command tests
# ---------------------------------------------------------------------------

_PATTERN_RAW_DATA = {
    "model": "gpt-4",
    "embedding": "all-MiniLM-L6-v2",
    "chunk_size": 512,
    "answer_correctness": 0.9,
    "faithfulness": 0.85,
}


_SAMPLE_PIPELINE_PARAMS = {
    "embedding_models": ["bge-large-en-v1.5", "all-minilm-l6-v2"],
    "generation_models": ["granite-3b-code-instruct"],
    "optimization_metric": "faithfulness",
    "vector_io_provider_id": "milvus-prod",
}


def _mock_collect(
    run_id: str = "run-1",
    summary: dict[str, float] | None = None,
    patterns: list[_patterns.PatternMetrics] | None = None,
    display_name: str | None = None,
    pipeline_name: str | None = "autorag-pipeline",
    state: str | None = "Succeeded",
    duration_seconds: float | None = 754.0,
    pipeline_params: dict[str, Any] | None = None,
) -> _patterns.RunPatternData:
    """Build a sample RunPatternData."""
    if summary is None:
        summary = {"answer_correctness": 0.847, "faithfulness": 0.912}
    if patterns is None:
        patterns = [
            _patterns.PatternMetrics(
                "Pattern1", {"answer_correctness": 0.8, "faithfulness": 0.9},
                {"answer_correctness": 0.8, "faithfulness": 0.9, "model": "gpt-3.5"},
            ),
            _patterns.PatternMetrics(
                "Pattern2", {"answer_correctness": 0.9, "faithfulness": 0.85},
                _PATTERN_RAW_DATA,
            ),
            _patterns.PatternMetrics(
                "Pattern3", {"answer_correctness": 0.85, "faithfulness": 0.88},
                {"answer_correctness": 0.85, "faithfulness": 0.88, "model": "gpt-4"},
            ),
        ]
    return _patterns.RunPatternData(
        run_id=run_id,
        display_name=display_name or run_id,
        summary_metrics=summary,
        patterns=patterns,
        primary_metric=_patterns.detect_primary_metric(summary, patterns),
        source_key="pfx/evaluation_results.json",
        pipeline_name=pipeline_name,
        state=state,
        duration_seconds=duration_seconds,
        pipeline_params=pipeline_params or {},
    )


def _results_ns(**overrides) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "run_id": "a3f1b2c4-dead-beef-1234-567890abcdef",
        "prefix": None, "bucket": None, "json": False,
        "pdf": None, "names": None, "sort_by": None,
        "detailed": False, "top_n": 1,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _compare_ns(**overrides) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "run_id_1": "run-aaa", "run_id_2": "run-bbb",
        "metrics": None, "prefix1": None, "prefix2": None,
        "bucket": None, "json": False,
        "pdf": None, "names": None,
        "detailed": False,
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
    def test_human_output_with_leaderboard(self, capsys):
        kfp = MagicMock()
        s3 = MagicMock()
        data = _mock_collect()

        with patch("autox_tools.autorag.cli.collect_run_data", return_value=data):
            cli.cmd_results(kfp, s3, _results_ns())

        out = capsys.readouterr().out
        assert "Experiment Results" in out
        assert "answer_correctness" in out
        assert "Leaderboard" in out
        assert "Pattern1" in out
        assert "Pattern2" in out
        assert "Per-Pattern Detail" not in out

    def test_detailed_shows_per_pattern_and_settings(self, capsys):
        kfp = MagicMock()
        s3 = MagicMock()
        data = _mock_collect()

        with patch("autox_tools.autorag.cli.collect_run_data", return_value=data):
            cli.cmd_results(kfp, s3, _results_ns(detailed=True))

        out = capsys.readouterr().out
        assert "Per-Pattern Detail" in out
        assert "Top Pattern" in out
        assert "Pattern2" in out
        assert "model" in out
        assert "gpt-4" in out

    def test_json_output_includes_patterns(self, capsys):
        kfp = MagicMock()
        s3 = MagicMock()
        data = _mock_collect()

        with patch("autox_tools.autorag.cli.collect_run_data", return_value=data):
            cli.cmd_results(kfp, s3, _results_ns(json=True))

        result = json.loads(capsys.readouterr().out)
        assert result["summary_metrics"]["answer_correctness"] == 0.847
        assert "patterns" in result
        assert len(result["patterns"]) == 3

    def test_no_location_exits(self):
        kfp = MagicMock()
        s3 = MagicMock()

        with patch("autox_tools.autorag.cli.collect_run_data", return_value=None), \
             pytest.raises(SystemExit, match="Could not locate"):
            cli.cmd_results(kfp, s3, _results_ns())

    def test_no_results_exits(self):
        kfp = MagicMock()
        s3 = MagicMock()
        data = _mock_collect(summary={}, patterns=[])

        with patch("autox_tools.autorag.cli.collect_run_data", return_value=data), \
             pytest.raises(SystemExit, match="No evaluation_results"):
            cli.cmd_results(kfp, s3, _results_ns())

    def test_summary_only_no_patterns(self, capsys):
        kfp = MagicMock()
        s3 = MagicMock()
        data = _mock_collect(patterns=[])

        with patch("autox_tools.autorag.cli.collect_run_data", return_value=data):
            cli.cmd_results(kfp, s3, _results_ns())

        out = capsys.readouterr().out
        assert "Experiment Results" in out
        assert "answer_correctness" in out
        assert "Leaderboard" not in out

    def test_names_in_header(self, capsys):
        kfp = MagicMock()
        s3 = MagicMock()
        run_id = "a3f1b2c4-dead-beef-1234-567890abcdef"
        data = _mock_collect(run_id=run_id, display_name="My Cool Experiment")

        with patch("autox_tools.autorag.cli.collect_run_data", return_value=data):
            cli.cmd_results(kfp, s3, _results_ns(names=f"{run_id}=My Cool Experiment"))

        out = capsys.readouterr().out
        assert "My Cool Experiment" in out

    def test_pdf_calls_report(self, capsys):
        kfp = MagicMock()
        s3 = MagicMock()
        data = _mock_collect()

        with patch("autox_tools.autorag.cli.collect_run_data", return_value=data), \
             patch("autox_tools.autorag._report.require_matplotlib"), \
             patch("autox_tools.autorag._report.generate_results_pdf") as mock_gen:
            cli.cmd_results(kfp, s3, _results_ns(pdf="/tmp/report.pdf"))

        mock_gen.assert_called_once()
        assert capsys.readouterr().out.strip().endswith("/tmp/report.pdf")

    def test_sort_by_metric(self, capsys):
        kfp = MagicMock()
        s3 = MagicMock()
        data = _mock_collect()

        with patch("autox_tools.autorag.cli.collect_run_data", return_value=data):
            cli.cmd_results(kfp, s3, _results_ns(sort_by="faithfulness"))

        out = capsys.readouterr().out
        assert "faithfulness" in out

    def test_excludes_clutter_metrics(self, capsys):
        kfp = MagicMock()
        s3 = MagicMock()
        data = _mock_collect(
            summary={
                "accuracy": 0.9,
                "max_combinations": 100,
                "iteration": 5,
                "total_duration_s": 45.2,
            },
            patterns=[],
        )

        with patch("autox_tools.autorag.cli.collect_run_data", return_value=data):
            cli.cmd_results(kfp, s3, _results_ns())

        out = capsys.readouterr().out
        assert "accuracy" in out
        assert "max_combinations" not in out
        assert "iteration" not in out
        assert "duration" not in out

    def test_primary_metric_shown_first(self, capsys):
        kfp = MagicMock()
        s3 = MagicMock()
        data = _mock_collect(
            summary={"zebra": 0.1, "answer_correctness": 0.85, "alpha": 0.5},
            patterns=[],
        )

        with patch("autox_tools.autorag.cli.collect_run_data", return_value=data):
            cli.cmd_results(kfp, s3, _results_ns())

        out = capsys.readouterr().out
        metric_lines = [ln for ln in out.splitlines() if "0." in ln and "==" not in ln]
        assert "answer_correctness" in metric_lines[0]

    def test_optimization_label_in_summary(self, capsys):
        kfp = MagicMock()
        s3 = MagicMock()
        data = _mock_collect()

        with patch("autox_tools.autorag.cli.collect_run_data", return_value=data):
            cli.cmd_results(kfp, s3, _results_ns())

        out = capsys.readouterr().out
        assert "optimization: answer_correctness" in out

    def test_header_shows_pipeline_metadata(self, capsys):
        kfp = MagicMock()
        s3 = MagicMock()
        data = _mock_collect(
            pipeline_name="autorag-pipeline",
            state="Succeeded",
            duration_seconds=754.0,
        )

        with patch("autox_tools.autorag.cli.collect_run_data", return_value=data):
            cli.cmd_results(kfp, s3, _results_ns())

        out = capsys.readouterr().out
        assert "autorag-pipeline" in out
        assert "Succeeded" in out
        assert "12m 34s" in out

    def test_pipeline_params_displayed(self, capsys):
        kfp = MagicMock()
        s3 = MagicMock()
        data = _mock_collect(pipeline_params=_SAMPLE_PIPELINE_PARAMS)

        with patch("autox_tools.autorag.cli.collect_run_data", return_value=data):
            cli.cmd_results(kfp, s3, _results_ns())

        out = capsys.readouterr().out
        assert "Pipeline Parameters" in out
        assert "optimization_metric" in out
        assert "faithfulness" in out
        assert "vector_io_provider_id" in out
        assert "milvus-prod" in out

    def test_no_pipeline_params_no_section(self, capsys):
        kfp = MagicMock()
        s3 = MagicMock()
        data = _mock_collect(pipeline_params={})

        with patch("autox_tools.autorag.cli.collect_run_data", return_value=data):
            cli.cmd_results(kfp, s3, _results_ns())

        out = capsys.readouterr().out
        assert "Pipeline Parameters" not in out

    def test_top_n_shows_multiple_patterns(self, capsys):
        kfp = MagicMock()
        s3 = MagicMock()
        data = _mock_collect()

        with patch("autox_tools.autorag.cli.collect_run_data", return_value=data):
            cli.cmd_results(kfp, s3, _results_ns(detailed=True, top_n=2))

        out = capsys.readouterr().out
        assert "#1 Pattern2" in out
        assert "#2 Pattern3" in out


# ---------------------------------------------------------------------------
# cmd_compare
# ---------------------------------------------------------------------------


class TestCmdCompare:
    def _setup_data(self):
        data1 = _mock_collect(
            run_id="run-aaa",
            summary={"accuracy": 0.9, "latency_p50_ms": 100},
            patterns=[
                _patterns.PatternMetrics("P1", {"accuracy": 0.85}, {"accuracy": 0.85, "model": "v1"}),
                _patterns.PatternMetrics("P2", {"accuracy": 0.92}, {"accuracy": 0.92, "model": "v1"}),
            ],
        )
        data2 = _mock_collect(
            run_id="run-bbb",
            summary={"accuracy": 0.85, "latency_p50_ms": 80},
            patterns=[
                _patterns.PatternMetrics("P1", {"accuracy": 0.88}, {"accuracy": 0.88, "model": "v2"}),
                _patterns.PatternMetrics("P2", {"accuracy": 0.95}, {"accuracy": 0.95, "model": "v2"}),
            ],
        )
        return data1, data2

    def test_human_output(self, capsys):
        data1, data2 = self._setup_data()

        def mock_collect(kfp, s3, run_id, **kw):
            return data1 if run_id == "run-aaa" else data2

        with patch("autox_tools.autorag.cli.collect_run_data", side_effect=mock_collect):
            cli.cmd_compare(MagicMock(), MagicMock(), _compare_ns())

        out = capsys.readouterr().out
        assert "Comparison" in out
        assert "accuracy" in out
        assert "latency_p50_ms" in out
        assert "Leaderboard" not in out

    def test_detailed_shows_leaderboards(self, capsys):
        data1, data2 = self._setup_data()

        def mock_collect(kfp, s3, run_id, **kw):
            return data1 if run_id == "run-aaa" else data2

        with patch("autox_tools.autorag.cli.collect_run_data", side_effect=mock_collect):
            cli.cmd_compare(MagicMock(), MagicMock(), _compare_ns(detailed=True))

        out = capsys.readouterr().out
        assert "Leaderboard" in out
        assert "Per-Pattern Leaderboard Comparison" in out

    def test_detailed_shows_pattern_settings(self, capsys):
        data1, data2 = self._setup_data()

        def mock_collect(kfp, s3, run_id, **kw):
            return data1 if run_id == "run-aaa" else data2

        with patch("autox_tools.autorag.cli.collect_run_data", side_effect=mock_collect):
            cli.cmd_compare(MagicMock(), MagicMock(), _compare_ns(detailed=True))

        out = capsys.readouterr().out
        assert "Best Pattern Settings" in out
        assert "model" in out

    def test_json_output(self, capsys):
        data1, data2 = self._setup_data()

        def mock_collect(kfp, s3, run_id, **kw):
            return data1 if run_id == "run-aaa" else data2

        with patch("autox_tools.autorag.cli.collect_run_data", side_effect=mock_collect):
            cli.cmd_compare(MagicMock(), MagicMock(), _compare_ns(json=True))

        result = json.loads(capsys.readouterr().out)
        assert result["run_1"]["id"] == "run-aaa"
        assert result["run_2"]["id"] == "run-bbb"
        assert "deltas" in result
        assert "patterns" in result["run_1"]

    def test_metrics_filter(self, capsys):
        data1, data2 = self._setup_data()

        def mock_collect(kfp, s3, run_id, **kw):
            return data1 if run_id == "run-aaa" else data2

        with patch("autox_tools.autorag.cli.collect_run_data", side_effect=mock_collect):
            cli.cmd_compare(MagicMock(), MagicMock(), _compare_ns(metrics="accuracy"))

        out = capsys.readouterr().out
        assert "accuracy" in out

    def test_no_location_exits(self):
        with patch("autox_tools.autorag.cli.collect_run_data", return_value=None), \
             pytest.raises(SystemExit, match="Could not locate"):
            cli.cmd_compare(MagicMock(), MagicMock(), _compare_ns())

    def test_both_runs_fail_reports_both(self):
        with patch("autox_tools.autorag.cli.collect_run_data", return_value=None), \
             pytest.raises(SystemExit) as exc_info:
            cli.cmd_compare(MagicMock(), MagicMock(), _compare_ns())
        msg = str(exc_info.value)
        assert "run-aaa" in msg
        assert "run-bbb" in msg

    def test_exception_in_collect_exits_cleanly(self):
        with patch("autox_tools.autorag.cli.collect_run_data",
                    side_effect=Exception("connection refused")), \
             pytest.raises(SystemExit, match="Could not locate"):
            cli.cmd_compare(MagicMock(), MagicMock(), _compare_ns())

    def test_names_in_header(self, capsys):
        data1, data2 = self._setup_data()
        data1 = _patterns.RunPatternData(
            **{**data1.__dict__, "display_name": "Baseline"},
        )
        data2 = _patterns.RunPatternData(
            **{**data2.__dict__, "display_name": "New Config"},
        )

        def mock_collect(kfp, s3, run_id, **kw):
            return data1 if run_id == "run-aaa" else data2

        with patch("autox_tools.autorag.cli.collect_run_data", side_effect=mock_collect):
            cli.cmd_compare(
                MagicMock(), MagicMock(),
                _compare_ns(names="run-aaa=Baseline,run-bbb=New Config"),
            )

        out = capsys.readouterr().out
        assert "Baseline" in out
        assert "New Config" in out

    def test_pdf_calls_report(self, capsys):
        data1, data2 = self._setup_data()

        def mock_collect(kfp, s3, run_id, **kw):
            return data1 if run_id == "run-aaa" else data2

        with patch("autox_tools.autorag.cli.collect_run_data", side_effect=mock_collect), \
             patch("autox_tools.autorag._report.require_matplotlib"), \
             patch("autox_tools.autorag._report.generate_compare_pdf") as mock_gen:
            cli.cmd_compare(MagicMock(), MagicMock(), _compare_ns(pdf="/tmp/cmp.pdf"))

        mock_gen.assert_called_once()

    def test_no_patterns_still_works(self, capsys):
        data1 = _mock_collect(run_id="run-aaa", summary={"acc": 0.9}, patterns=[])
        data2 = _mock_collect(run_id="run-bbb", summary={"acc": 0.85}, patterns=[])

        def mock_collect(kfp, s3, run_id, **kw):
            return data1 if run_id == "run-aaa" else data2

        with patch("autox_tools.autorag.cli.collect_run_data", side_effect=mock_collect):
            cli.cmd_compare(MagicMock(), MagicMock(), _compare_ns())

        out = capsys.readouterr().out
        assert "Comparison" in out
        assert "Leaderboard" not in out

    def test_uses_short_id_labels(self, capsys):
        data1 = _mock_collect(
            run_id="72cdd9f0-1d2f-4571-8219-d008b2c4316a",
            summary={"accuracy": 0.9},
            patterns=[],
        )
        data2 = _mock_collect(
            run_id="511849b7-339e-4c62-ab14-f5ec07a3e722",
            summary={"accuracy": 0.85},
            patterns=[],
        )

        def mock_collect(kfp, s3, run_id, **kw):
            return data1 if "72cdd9f0" in run_id else data2

        with patch("autox_tools.autorag.cli.collect_run_data", side_effect=mock_collect):
            cli.cmd_compare(
                MagicMock(), MagicMock(),
                _compare_ns(
                    run_id_1="72cdd9f0-1d2f-4571-8219-d008b2c4316a",
                    run_id_2="511849b7-339e-4c62-ab14-f5ec07a3e722",
                ),
            )

        out = capsys.readouterr().out
        assert "72cdd9f0" in out
        assert "511849b7" in out
        assert "...b2c4316a" not in out

    def test_excludes_clutter_metrics(self, capsys):
        data1 = _mock_collect(
            run_id="run-aaa",
            summary={"accuracy": 0.9, "max_combinations": 100, "iteration": 5},
            patterns=[],
        )
        data2 = _mock_collect(
            run_id="run-bbb",
            summary={"accuracy": 0.85, "max_combinations": 200, "iteration": 10},
            patterns=[],
        )

        def mock_collect(kfp, s3, run_id, **kw):
            return data1 if run_id == "run-aaa" else data2

        with patch("autox_tools.autorag.cli.collect_run_data", side_effect=mock_collect):
            cli.cmd_compare(MagicMock(), MagicMock(), _compare_ns())

        out = capsys.readouterr().out
        assert "accuracy" in out
        assert "max_combinations" not in out
        assert "iteration" not in out


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

        with patch("autox_tools.autorag.cli.resolve",
                    return_value=_resolver.ArtifactLocation("b", "pfx/", "run_params")), \
             patch("autox_tools.autorag.cli.list_and_categorize", return_value=artifacts), \
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

        with patch("autox_tools.autorag.cli.resolve", return_value=None), \
             pytest.raises(SystemExit, match="Could not locate"):
            cli.cmd_export(kfp, s3, _export_ns())

    def test_no_artifacts_exits(self):
        kfp = MagicMock()
        s3 = MagicMock()

        with patch("autox_tools.autorag.cli.resolve",
                    return_value=_resolver.ArtifactLocation("b", "pfx/", "run_params")), \
             patch("autox_tools.autorag.cli.list_and_categorize", return_value=[]), \
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

        with patch("autox_tools.autorag.cli.resolve",
                    return_value=_resolver.ArtifactLocation("b", "pfx/", "run_params")), \
             patch("autox_tools.autorag.cli.list_and_categorize", return_value=artifacts), \
             tempfile.TemporaryDirectory(), \
             patch("autox_tools.autorag.cli.os.makedirs"):
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

        with patch("autox_tools.autorag.cli.resolve",
                    return_value=_resolver.ArtifactLocation("b", "pfx/", "run_params")), \
             patch("autox_tools.autorag.cli.list_and_categorize", return_value=artifacts):
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

        with patch("autox_tools.autorag.cli.resolve",
                    return_value=_resolver.ArtifactLocation("b", "pfx/", "run_params")), \
             patch("autox_tools.autorag.cli.list_and_categorize", return_value=[]):
            cli.cmd_info(kfp, s3, _info_ns(json=True))

        result = json.loads(capsys.readouterr().out)
        assert result["state"] == "Succeeded"
        assert result["duration"] == "12m 34s"
        assert result["artifact_count"] == 0

    def test_no_artifacts_location(self, capsys):
        kfp = MagicMock()
        kfp.get_run.return_value = self._make_run()
        s3 = MagicMock()

        with patch("autox_tools.autorag.cli.resolve", return_value=None):
            cli.cmd_info(kfp, s3, _info_ns())

        out = capsys.readouterr().out
        assert "could not resolve" in out

    def test_run_fetch_failure_exits(self):
        kfp = MagicMock()
        kfp.get_run.side_effect = Exception("connection refused")
        s3 = MagicMock()

        with pytest.raises(SystemExit, match="Failed to get run"):
            cli.cmd_info(kfp, s3, _info_ns())
