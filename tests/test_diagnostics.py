"""Tests for diagnostics module."""

import pytest
from swe_cgd.diagnostics.runner import (
    DiagnosticsRunner,
    DiagnosticResult,
    DiagnosticIssue,
)
from swe_cgd.utils.config import Config


@pytest.fixture
def config():
    return Config()


@pytest.fixture
def runner(config):
    return DiagnosticsRunner(config)


def test_get_changed_files_standard_diff(runner):
    """Test parsing standard git diff format."""
    patch = """diff --git a/src/module.py b/src/module.py
--- a/src/module.py
+++ b/src/module.py
@@ -1,3 +1,4 @@
 def foo():
-    return 1
+    return 2
"""
    files = runner.get_changed_files(patch)
    assert "src/module.py" in files


def test_get_changed_files_multiple_files(runner):
    """Test parsing diff with multiple files."""
    patch = """diff --git a/file1.py b/file1.py
--- a/file1.py
+++ b/file1.py
@@ -1 +1 @@
-old
+new
diff --git a/file2.py b/file2.py
--- a/file2.py
+++ b/file2.py
@@ -1 +1 @@
-old
+new
"""
    files = runner.get_changed_files(patch)
    assert len(files) >= 2
    assert "file1.py" in files or "b/file1.py" in files


def test_get_changed_files_empty_patch(runner):
    """Test handling empty patch."""
    files = runner.get_changed_files("")
    assert files == []


def test_diagnostic_result_no_issues():
    """Test DiagnosticResult with no issues."""
    result = DiagnosticResult(
        instance_id="test-123",
        patch_applied=True,
    )
    assert not result.has_issues
    assert result.total_errors == 0
    assert "No diagnostic issues" in result.get_summary()


def test_diagnostic_result_with_errors():
    """Test DiagnosticResult with errors."""
    result = DiagnosticResult(
        instance_id="test-123",
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
    assert result.has_issues
    assert result.total_errors == 2
    assert "syntax error" in result.get_summary().lower()


def test_diagnostic_result_llm_context():
    """Test formatting for LLM consumption."""
    result = DiagnosticResult(
        instance_id="test-123",
        patch_applied=True,
        type_errors=[
            DiagnosticIssue(
                file="module.py",
                line=15,
                column=10,
                severity="error",
                code="reportUndefinedVariable",
                message="'undefined_func' is not defined",
            )
        ],
    )
    context = result.to_llm_context()
    assert "Type/Symbol Errors" in context
    assert "undefined_func" in context
    assert "module.py:15:10" in context


def test_diagnostic_result_patch_failed():
    """Test DiagnosticResult when patch fails to apply."""
    result = DiagnosticResult(
        instance_id="test-123",
        patch_applied=False,
        apply_error="Patch does not apply cleanly",
    )
    assert result.has_issues
    assert "Patch failed" in result.get_summary()
