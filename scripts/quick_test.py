#!/usr/bin/env python
"""Quick test of the CGD pipeline without Docker/SWE-bench."""

import json
import sys
from pathlib import Path

# Add src to path for local testing
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from swe_cgd.utils.config import load_config
from swe_cgd.utils.logging import setup_logging, get_logger
from swe_cgd.baseline.generator import BaselineGenerator, Prediction
from swe_cgd.diagnostics.runner import DiagnosticsRunner, DiagnosticResult, DiagnosticIssue
from swe_cgd.cleanup.cleaner import PatchCleaner

setup_logging()
logger = get_logger(__name__)


def test_baseline_generator():
    """Test baseline prediction generation on a small sample."""
    logger.info("Testing baseline generator...")

    config = load_config()
    config.max_instances = 2
    config.evaluation.dataset_name = "princeton-nlp/SWE-bench_Lite"

    generator = BaselineGenerator(config)
    generator.load_dataset()

    # Just get the first instance
    for instance in generator.get_instances():
        logger.info(f"Instance: {instance['instance_id']}")
        logger.info(f"Repo: {instance['repo']}")
        logger.info(f"Problem: {instance['problem_statement'][:200]}...")
        break

    logger.info("Baseline generator test: OK")


def test_diagnostics_parser():
    """Test diagnostics parsing without Docker."""
    logger.info("Testing diagnostics parser...")

    config = load_config()
    runner = DiagnosticsRunner(config)

    # Test patch file extraction
    test_patch = """diff --git a/test.py b/test.py
--- a/test.py
+++ b/test.py
@@ -1,3 +1,4 @@
 def foo():
-    return 1
+    return undefined_var
+    print(x)
"""

    files = runner.get_changed_files(test_patch)
    assert "test.py" in files, f"Expected test.py in {files}"

    logger.info(f"Changed files: {files}")
    logger.info("Diagnostics parser test: OK")


def test_cleanup_prompt_generation():
    """Test cleanup prompt generation."""
    logger.info("Testing cleanup prompt generation...")

    # Create a mock diagnostic result
    diag = DiagnosticResult(
        instance_id="test__test-123",
        patch_applied=True,
        syntax_errors=[
            DiagnosticIssue(
                file="test.py",
                line=5,
                column=10,
                severity="error",
                code="SyntaxError",
                message="invalid syntax",
            )
        ],
        type_errors=[
            DiagnosticIssue(
                file="test.py",
                line=3,
                column=5,
                severity="error",
                code="reportUndefinedVariable",
                message="'undefined_var' is not defined",
            )
        ],
    )

    context = diag.to_llm_context()
    logger.info(f"LLM Context:\n{context}")

    assert "Syntax Errors" in context
    assert "Type/Symbol Errors" in context
    assert "undefined_var" in context

    logger.info("Cleanup prompt generation test: OK")


def test_prediction_serialization():
    """Test prediction JSON serialization."""
    logger.info("Testing prediction serialization...")

    pred = Prediction(
        instance_id="test__test-123",
        model_name_or_path="test-model",
        model_patch="diff --git a/test.py b/test.py\n--- a/test.py\n+++ b/test.py\n",
    )

    json_str = pred.to_json()
    data = json.loads(json_str)

    assert data["instance_id"] == "test__test-123"
    assert data["model_name_or_path"] == "test-model"
    assert "diff --git" in data["model_patch"]

    logger.info("Prediction serialization test: OK")


def main():
    """Run all quick tests."""
    logger.info("=== SWE-CGD Quick Tests ===")

    try:
        test_prediction_serialization()
        test_diagnostics_parser()
        test_cleanup_prompt_generation()

        # This test requires HuggingFace dataset download
        # test_baseline_generator()

        logger.info("")
        logger.info("=== All tests passed! ===")
        return 0

    except Exception as e:
        logger.error(f"Test failed: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
