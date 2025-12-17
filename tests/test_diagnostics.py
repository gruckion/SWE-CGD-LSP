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


def test_diagnostic_result_patch_not_applied_has_issues():
    """Test that patch_applied=False triggers has_issues even without apply_error."""
    result = DiagnosticResult(
        instance_id="test-123",
        patch_applied=False,
    )
    # has_issues should be True when patch_applied is False
    assert result.has_issues


def test_diagnostic_issue_from_dict():
    """Test DiagnosticIssue.from_dict() round-trip."""
    original = DiagnosticIssue(
        file="test.py",
        line=42,
        column=10,
        severity="error",
        code="reportUndefinedVariable",
        message="'foo' is not defined",
    )

    # Convert to dict and back
    data = original.to_dict()
    restored = DiagnosticIssue.from_dict(data)

    assert restored.file == original.file
    assert restored.line == original.line
    assert restored.column == original.column
    assert restored.severity == original.severity
    assert restored.code == original.code
    assert restored.message == original.message


def test_diagnostic_result_from_dict():
    """Test DiagnosticResult.from_dict() round-trip."""
    original = DiagnosticResult(
        instance_id="test__test-123",
        patch_applied=True,
        apply_error=None,
        syntax_errors=[
            DiagnosticIssue(
                file="module.py",
                line=5,
                column=1,
                severity="error",
                code="SyntaxError",
                message="invalid syntax",
            )
        ],
        type_errors=[
            DiagnosticIssue(
                file="module.py",
                line=10,
                column=5,
                severity="error",
                code="reportUndefinedVariable",
                message="'x' is not defined",
            ),
            DiagnosticIssue(
                file="module.py",
                line=15,
                column=8,
                severity="warning",
                code="reportUnusedVariable",
                message="'y' is not accessed",
            ),
        ],
    )

    # Convert to dict and back
    data = original.to_dict()
    restored = DiagnosticResult.from_dict(data)

    assert restored.instance_id == original.instance_id
    assert restored.patch_applied == original.patch_applied
    assert restored.apply_error == original.apply_error
    assert len(restored.syntax_errors) == len(original.syntax_errors)
    assert len(restored.type_errors) == len(original.type_errors)
    assert restored.syntax_errors[0].message == "invalid syntax"
    assert restored.type_errors[0].code == "reportUndefinedVariable"
    assert restored.type_errors[1].severity == "warning"


def test_diagnostic_result_from_dict_empty_errors():
    """Test DiagnosticResult.from_dict() with empty error lists."""
    data = {
        "instance_id": "test-456",
        "patch_applied": True,
        "apply_error": None,
        "syntax_errors": [],
        "type_errors": [],
    }

    restored = DiagnosticResult.from_dict(data)

    assert restored.instance_id == "test-456"
    assert restored.patch_applied is True
    assert len(restored.syntax_errors) == 0
    assert len(restored.type_errors) == 0
    assert not restored.has_issues


def test_diagnostic_result_from_dict_missing_fields():
    """Test DiagnosticResult.from_dict() handles missing optional fields."""
    data = {
        "instance_id": "test-789",
        # patch_applied missing - should default to False
        # syntax_errors/type_errors missing - should default to []
    }

    restored = DiagnosticResult.from_dict(data)

    assert restored.instance_id == "test-789"
    assert restored.patch_applied is False  # Default
    assert len(restored.syntax_errors) == 0
    assert len(restored.type_errors) == 0
