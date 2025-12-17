"""Tests for cleanup module."""

import pytest
from unittest.mock import Mock, patch

from swe_cgd.cleanup.cleaner import PatchCleaner
from swe_cgd.baseline.generator import Prediction, CostMetrics
from swe_cgd.diagnostics.runner import DiagnosticResult, DiagnosticIssue
from swe_cgd.utils.config import Config


@pytest.fixture
def config():
    config = Config()
    config.llm.model = "test-model"
    config.llm.api_key = "test-key"
    return config


@pytest.fixture
def cleaner(config):
    return PatchCleaner(config, max_repair_iterations=3, max_repair_tokens=50000)


@pytest.fixture
def sample_instance():
    return {
        "instance_id": "test__test-123",
        "repo": "test/repo",
        "problem_statement": "Fix the bug in the code",
    }


@pytest.fixture
def sample_prediction():
    return Prediction(
        instance_id="test__test-123",
        model_name_or_path="test-model",
        model_patch="diff --git a/test.py b/test.py\n--- a/test.py\n+++ b/test.py\n",
        cost_metrics=CostMetrics(input_tokens=100, output_tokens=50, total_tokens=150, llm_calls=1),
    )


def test_cleanup_skipped_when_no_issues(cleaner, sample_instance, sample_prediction):
    """Test that cleanup is skipped when diagnostics have no issues."""
    # Create diagnostics with no issues
    diagnostics = DiagnosticResult(
        instance_id="test__test-123",
        patch_applied=True,
        syntax_errors=[],
        type_errors=[],
    )

    # should not have issues
    assert not diagnostics.has_issues

    # process_prediction should return original when no issues
    result = cleaner.process_prediction(sample_instance, sample_prediction, diagnostics)

    # Should return prediction with had_diagnostic_issues=False
    assert result.instance_id == sample_prediction.instance_id
    assert result.model_patch == sample_prediction.model_patch
    assert not result.had_diagnostic_issues
    assert result.repair_iterations == 0


def test_cleanup_invoked_when_has_issues(cleaner, sample_instance, sample_prediction):
    """Test that cleanup is invoked when diagnostics have issues."""
    # Create diagnostics with issues
    diagnostics = DiagnosticResult(
        instance_id="test__test-123",
        patch_applied=True,
        syntax_errors=[],
        type_errors=[
            DiagnosticIssue(
                file="test.py",
                line=10,
                column=5,
                severity="error",
                code="reportUndefinedVariable",
                message="'undefined_var' is not defined",
            )
        ],
    )

    # Should have issues
    assert diagnostics.has_issues

    # Mock the _single_cleanup_call method to track if it's called
    with patch.object(
        cleaner,
        "_single_cleanup_call",
        return_value=("cleaned patch\n", CostMetrics(input_tokens=200, output_tokens=100, total_tokens=300, llm_calls=1))
    ) as mock_clean:
        result = cleaner.process_prediction(sample_instance, sample_prediction, diagnostics)

        # _single_cleanup_call should have been called
        mock_clean.assert_called_once()

        # Result should be the cleaned version
        assert "_cleaned" in result.model_name_or_path
        assert result.model_patch == "cleaned patch\n"
        assert result.had_diagnostic_issues
        assert result.repair_iterations >= 1


def test_cleanup_invoked_when_patch_failed(cleaner, sample_instance, sample_prediction):
    """Test that cleanup is invoked when patch failed to apply."""
    # Create diagnostics where patch failed
    diagnostics = DiagnosticResult(
        instance_id="test__test-123",
        patch_applied=False,
        apply_error="Patch does not apply cleanly",
    )

    # Should have issues due to patch_applied=False
    assert diagnostics.has_issues

    # Mock the _single_cleanup_call method
    with patch.object(
        cleaner,
        "_single_cleanup_call",
        return_value=("fixed patch\n", CostMetrics(llm_calls=1))
    ) as mock_clean:
        result = cleaner.process_prediction(sample_instance, sample_prediction, diagnostics)

        # _single_cleanup_call should have been called
        mock_clean.assert_called_once()
        assert result.had_diagnostic_issues


def test_cleanup_with_syntax_errors(cleaner, sample_instance, sample_prediction):
    """Test cleanup is invoked with syntax errors."""
    diagnostics = DiagnosticResult(
        instance_id="test__test-123",
        patch_applied=True,
        syntax_errors=[
            DiagnosticIssue(
                file="test.py",
                line=5,
                column=1,
                severity="error",
                code="SyntaxError",
                message="invalid syntax",
            )
        ],
    )

    assert diagnostics.has_issues

    # Check that LLM context includes syntax errors
    context = diagnostics.to_llm_context()
    assert "Syntax Errors" in context
    assert "invalid syntax" in context


def test_cleanup_handles_exception_gracefully(cleaner, sample_instance, sample_prediction):
    """Test that cleanup handles exceptions and returns original prediction."""
    diagnostics = DiagnosticResult(
        instance_id="test__test-123",
        patch_applied=True,
        type_errors=[
            DiagnosticIssue(
                file="test.py",
                line=10,
                column=5,
                severity="error",
                code="reportUndefinedVariable",
                message="'x' is not defined",
            )
        ],
    )

    # Mock _single_cleanup_call to raise an exception
    with patch.object(cleaner, "_single_cleanup_call", side_effect=Exception("LLM error")):
        result = cleaner.process_prediction(sample_instance, sample_prediction, diagnostics)

        # Should return original prediction on error but mark had_diagnostic_issues
        assert result.instance_id == sample_prediction.instance_id
        assert result.model_patch == sample_prediction.model_patch
        assert result.had_diagnostic_issues  # Should still track that there were issues


def test_cost_metrics_aggregation(cleaner, sample_instance, sample_prediction):
    """Test that cost metrics are properly aggregated during cleanup."""
    diagnostics = DiagnosticResult(
        instance_id="test__test-123",
        patch_applied=True,
        type_errors=[
            DiagnosticIssue(
                file="test.py",
                line=10,
                column=5,
                severity="error",
                code="reportUndefinedVariable",
                message="'x' is not defined",
            )
        ],
    )

    cleanup_cost = CostMetrics(input_tokens=300, output_tokens=150, total_tokens=450, llm_calls=1)

    with patch.object(
        cleaner,
        "_single_cleanup_call",
        return_value=("cleaned patch\n", cleanup_cost)
    ):
        result = cleaner.process_prediction(sample_instance, sample_prediction, diagnostics)

        # Cost should be original + cleanup
        assert result.cost_metrics is not None
        assert result.cost_metrics.total_tokens == 150 + 450  # original + cleanup
        assert result.cost_metrics.llm_calls == 2  # 1 original + 1 cleanup


def test_prediction_from_dict():
    """Test Prediction.from_dict() round-trip with cost metrics."""
    original = Prediction(
        instance_id="test-123",
        model_name_or_path="test-model",
        model_patch="diff --git ...",
        cost_metrics=CostMetrics(
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            wall_time_seconds=1.5,
            llm_calls=1,
            failed_calls=0,
        ),
        repair_iterations=2,
        had_diagnostic_issues=True,
    )

    # Convert to dict and back
    data = original.to_dict()
    restored = Prediction.from_dict(data)

    assert restored.instance_id == original.instance_id
    assert restored.model_name_or_path == original.model_name_or_path
    assert restored.model_patch == original.model_patch
    assert restored.repair_iterations == original.repair_iterations
    assert restored.had_diagnostic_issues == original.had_diagnostic_issues
    assert restored.cost_metrics.total_tokens == original.cost_metrics.total_tokens
    assert restored.cost_metrics.llm_calls == original.cost_metrics.llm_calls
