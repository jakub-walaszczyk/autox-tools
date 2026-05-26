"""Tests for PDF report generation.

Skipped when matplotlib is not installed.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from autox_tools.experiments._patterns import (
    PatternMetrics,
    RunPatternData,
)

try:
    from autox_tools.experiments._report import HAS_MATPLOTLIB
except ImportError:
    HAS_MATPLOTLIB = False

pytestmark = pytest.mark.skipif(
    not HAS_MATPLOTLIB, reason="matplotlib not installed",
)


def _sample_run_data(
    run_id: str = "run-abc",
    display_name: str = "Test Experiment",
    n_patterns: int = 5,
    *,
    include_ci: bool = True,
) -> RunPatternData:
    """Build a sample RunPatternData with realistic RAG metrics."""
    patterns = []
    for i in range(1, n_patterns + 1):
        metrics = {
            "answer_correctness": 0.7 + 0.04 * i,
            "faithfulness": 0.6 + 0.05 * i,
            "context_correctness": 0.8 + 0.02 * i,
        }
        if include_ci:
            raw_data: dict = {
                "scores": {
                    name: {
                        "mean": val,
                        "ci_low": val - 0.05,
                        "ci_high": val + 0.05,
                    }
                    for name, val in metrics.items()
                },
            }
        else:
            raw_data = {}

        patterns.append(
            PatternMetrics(name=f"Pattern{i}", metrics=metrics, raw_data=raw_data)
        )

    return RunPatternData(
        run_id=run_id,
        display_name=display_name,
        summary_metrics={
            "answer_correctness": 0.85,
            "faithfulness": 0.88,
            "context_correctness": 0.90,
        },
        patterns=patterns,
        primary_metric="answer_correctness",
        source_key="pfx/evaluation_results.json",
        pipeline_name="autorag-pipeline",
        state="SUCCEEDED",
        duration_seconds=3600.0,
        pipeline_params={
            "optimization_metric": "faithfulness",
            "num_patterns": "8",
            "dataset": "test-dataset",
        },
    )


class TestGenerateResultsPdf:
    def test_creates_file(self):
        from autox_tools.experiments._report import generate_results_pdf

        data = _sample_run_data()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "results.pdf")
            generate_results_pdf(data, path, {})
            assert os.path.isfile(path)
            assert os.path.getsize(path) > 0

    def test_with_display_names(self):
        from autox_tools.experiments._report import generate_results_pdf

        data = _sample_run_data()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "named.pdf")
            generate_results_pdf(data, path, {"run-abc": "My Named Run"})
            assert os.path.isfile(path)

    def test_no_patterns(self):
        from autox_tools.experiments._report import generate_results_pdf

        data = _sample_run_data(n_patterns=0)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "empty.pdf")
            generate_results_pdf(data, path, {})
            assert os.path.isfile(path)

    def test_no_ci_data(self):
        from autox_tools.experiments._report import generate_results_pdf

        data = _sample_run_data(include_ci=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "no_ci.pdf")
            generate_results_pdf(data, path, {})
            assert os.path.isfile(path)
            assert os.path.getsize(path) > 0


class TestGenerateComparePdf:
    def test_creates_file(self):
        from autox_tools.experiments._report import generate_compare_pdf

        data1 = _sample_run_data(run_id="run-1", display_name="Baseline")
        data2 = _sample_run_data(run_id="run-2", display_name="New Config")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "compare.pdf")
            generate_compare_pdf(data1, data2, path, {})
            assert os.path.isfile(path)
            assert os.path.getsize(path) > 0

    def test_with_display_names(self):
        from autox_tools.experiments._report import generate_compare_pdf

        data1 = _sample_run_data(run_id="r1")
        data2 = _sample_run_data(run_id="r2")
        names = {"r1": "Run A", "r2": "Run B"}
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "named_cmp.pdf")
            generate_compare_pdf(data1, data2, path, names)
            assert os.path.isfile(path)

    def test_different_pattern_counts(self):
        from autox_tools.experiments._report import generate_compare_pdf

        data1 = _sample_run_data(run_id="r1", n_patterns=3)
        data2 = _sample_run_data(run_id="r2", n_patterns=7)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "diff.pdf")
            generate_compare_pdf(data1, data2, path, {})
            assert os.path.isfile(path)


class TestExtractCiBounds:
    def test_extracts_valid_ci(self):
        from autox_tools.experiments._report import _extract_ci_bounds

        p = PatternMetrics(
            name="test",
            metrics={"m": 0.8},
            raw_data={"scores": {"m": {"mean": 0.8, "ci_low": 0.7, "ci_high": 0.9}}},
        )
        assert _extract_ci_bounds(p, "m") == (0.7, 0.9)

    def test_missing_scores(self):
        from autox_tools.experiments._report import _extract_ci_bounds

        p = PatternMetrics(name="test", metrics={"m": 0.8}, raw_data={})
        assert _extract_ci_bounds(p, "m") == (None, None)

    def test_missing_metric_in_scores(self):
        from autox_tools.experiments._report import _extract_ci_bounds

        p = PatternMetrics(
            name="test",
            metrics={"m": 0.8},
            raw_data={"scores": {"other": {"mean": 0.5}}},
        )
        assert _extract_ci_bounds(p, "m") == (None, None)

    def test_partial_ci_returns_none(self):
        from autox_tools.experiments._report import _extract_ci_bounds

        p = PatternMetrics(
            name="test",
            metrics={"m": 0.8},
            raw_data={"scores": {"m": {"mean": 0.8, "ci_low": 0.7}}},
        )
        assert _extract_ci_bounds(p, "m") == (None, None)


class TestRequireMatplotlib:
    def test_does_not_raise_when_available(self):
        from autox_tools.experiments._report import require_matplotlib
        require_matplotlib()
