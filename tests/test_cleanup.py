"""Tests for cleanup module."""

import pytest
from unittest.mock import Mock, patch

from swe_cgd.cleanup.cleaner import PatchCleaner
from swe_cgd.baseline.generator import Prediction
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
    return PatchCleaner(config)


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

    # Should return original prediction unchanged
    assert result.instance_id == sample_prediction.instance_id
    assert result.model_patch == sample_prediction.model_patch


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

    # Mock the clean_patch method to track if it's called
    with patch.object(cleaner, "clean_patch", return_value="cleaned patch\n") as mock_clean:
        result = cleaner.process_prediction(sample_instance, sample_prediction, diagnostics)

        # clean_patch should have been called
        mock_clean.assert_called_once()

        # Result should be the cleaned version
        assert "_cleaned" in result.model_name_or_path
        assert result.model_patch == "cleaned patch\n"


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

    # Mock the clean_patch method
    with patch.object(cleaner, "clean_patch", return_value="fixed patch\n") as mock_clean:
        result = cleaner.process_prediction(sample_instance, sample_prediction, diagnostics)

        # clean_patch should have been called
        mock_clean.assert_called_once()


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

    # Mock clean_patch to raise an exception
    with patch.object(cleaner, "clean_patch", side_effect=Exception("LLM error")):
        result = cleaner.process_prediction(sample_instance, sample_prediction, diagnostics)

        # Should return original prediction on error
        assert result.instance_id == sample_prediction.instance_id
        assert result.model_patch == sample_prediction.model_patch
