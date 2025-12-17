"""Docker-based diagnostics runner for SWE-bench containers."""

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .runner import DiagnosticResult, DiagnosticIssue
from ..utils.logging import get_logger

logger = get_logger(__name__)


# Script to run inside the SWE-bench container
CONTAINER_DIAGNOSTICS_SCRIPT = '''#!/bin/bash
set -e

# Apply the patch
cd /testbed
git apply --check /tmp/patch.diff 2>/tmp/apply_check.txt || echo "PATCH_CHECK_FAILED"
if [ -f /tmp/apply_check.txt ] && [ -s /tmp/apply_check.txt ]; then
    cat /tmp/apply_check.txt
    exit 1
fi

git apply /tmp/patch.diff 2>/tmp/apply.txt || {
    echo "PATCH_APPLY_FAILED"
    cat /tmp/apply.txt
    exit 1
}

# Get changed Python files from working tree (not HEAD~1 which compares commits)
CHANGED_FILES=$(git diff --name-only | grep '\\.py$' || true)

# Run py_compile on changed files (syntax check)
echo "=== COMPILEALL OUTPUT ==="
if [ -n "$CHANGED_FILES" ]; then
    for f in $CHANGED_FILES; do
        if [ -f "$f" ]; then
            python -m py_compile "$f" 2>&1 || true
        fi
    done
else
    echo "No Python files changed"
fi

# Run pyright if available
echo "=== PYRIGHT OUTPUT ==="
if [ -n "$CHANGED_FILES" ]; then
    if command -v pyright &> /dev/null; then
        pyright --outputjson $CHANGED_FILES 2>&1 || true
    elif python -m pyright --version &> /dev/null 2>&1; then
        python -m pyright --outputjson $CHANGED_FILES 2>&1 || true
    else
        echo "=== PYRIGHT NOT AVAILABLE ==="
    fi
else
    echo "No Python files to check"
fi

echo "=== DIAGNOSTICS COMPLETE ==="
'''


class DockerDiagnosticsRunner:
    """Run diagnostics inside SWE-bench Docker containers."""

    def __init__(
        self,
        timeout: int = 120,
        pyright_image: str = "python:3.10-slim",
    ):
        self.timeout = timeout
        self.pyright_image = pyright_image

    def run_in_container(
        self,
        instance_id: str,
        patch: str,
        image_name: Optional[str] = None,
    ) -> DiagnosticResult:
        """Run diagnostics inside a SWE-bench container.

        Args:
            instance_id: The SWE-bench instance ID
            patch: The patch to apply and analyze
            image_name: Docker image to use (derived from instance_id if not provided)

        Returns:
            DiagnosticResult with parsed diagnostics
        """
        result = DiagnosticResult(instance_id=instance_id, patch_applied=False)

        if not patch.strip():
            result.apply_error = "Empty patch"
            return result

        # Derive image name from instance_id if not provided
        if not image_name:
            # SWE-bench image naming convention
            repo, issue = instance_id.rsplit("-", 1)
            image_name = f"swebench/{repo.replace('__', '/')}:{instance_id}"

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir_path = Path(tmpdir)

                # Write patch to temp file
                patch_file = tmpdir_path / "patch.diff"
                patch_file.write_text(patch)

                # Write diagnostics script
                script_file = tmpdir_path / "run_diagnostics.sh"
                script_file.write_text(CONTAINER_DIAGNOSTICS_SCRIPT)
                script_file.chmod(0o755)

                # Run diagnostics in a new container
                cmd = [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{patch_file}:/tmp/patch.diff:ro",
                    "-v",
                    f"{script_file}:/tmp/run_diagnostics.sh:ro",
                    image_name,
                    "/tmp/run_diagnostics.sh",
                ]

                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                )

                output = proc.stdout + proc.stderr

                # Check for Docker-level failures (image not found, permission errors, etc.)
                # Note: returncode != 0 from the script itself is expected for patch failures
                # so we check for specific Docker error patterns
                if proc.returncode != 0:
                    # Check if this is a known script-level failure (patch failed)
                    if "PATCH_CHECK_FAILED" in output or "PATCH_APPLY_FAILED" in output:
                        result = self._parse_output(instance_id, output)
                    # Check for Docker-specific errors
                    elif "Unable to find image" in output or "Error response from daemon" in output:
                        result.patch_applied = False
                        result.apply_error = f"Docker error: {output[:500]}"
                    elif "permission denied" in output.lower():
                        result.patch_applied = False
                        result.apply_error = f"Docker permission error: {output[:500]}"
                    elif not output.strip():
                        # Empty output with non-zero exit = likely Docker failure
                        result.patch_applied = False
                        result.apply_error = f"Docker failed with exit code {proc.returncode}"
                    else:
                        # Non-zero exit but has output - try to parse it
                        result = self._parse_output(instance_id, output)
                        # If parsing didn't find any issues but we had non-zero exit,
                        # that's suspicious - mark as potential failure
                        if not result.has_issues and "=== DIAGNOSTICS COMPLETE ===" not in output:
                            result.patch_applied = False
                            result.apply_error = f"Diagnostics incomplete (exit {proc.returncode})"
                else:
                    result = self._parse_output(instance_id, output)

        except subprocess.TimeoutExpired:
            result.apply_error = "Diagnostics timed out"
        except FileNotFoundError:
            result.apply_error = "Docker not found - is Docker installed?"
        except Exception as e:
            result.apply_error = f"Unexpected error: {e}"

        return result

    def _parse_output(self, instance_id: str, output: str) -> DiagnosticResult:
        """Parse diagnostics output from container."""
        result = DiagnosticResult(instance_id=instance_id, patch_applied=True)

        if "PATCH_CHECK_FAILED" in output or "PATCH_APPLY_FAILED" in output:
            result.patch_applied = False
            result.apply_error = "Patch failed to apply"
            return result

        # Parse compileall output
        if "=== COMPILEALL OUTPUT ===" in output:
            compileall_section = output.split("=== COMPILEALL OUTPUT ===")[1]
            if "=== PYRIGHT" in compileall_section:
                compileall_section = compileall_section.split("=== PYRIGHT")[0]

            for line in compileall_section.strip().split("\n"):
                if "SyntaxError" in line or "Error" in line:
                    result.syntax_errors.append(
                        DiagnosticIssue(
                            file="unknown",
                            line=1,
                            column=1,
                            severity="error",
                            code="SyntaxError",
                            message=line.strip(),
                        )
                    )
            result.compileall_output = compileall_section.strip()

        # Parse pyright output (JSON format)
        if "=== PYRIGHT OUTPUT ===" in output:
            pyright_section = output.split("=== PYRIGHT OUTPUT ===")[1]
            if "=== DIAGNOSTICS COMPLETE ===" in pyright_section:
                pyright_section = pyright_section.split("=== DIAGNOSTICS COMPLETE ===")[0]

            result.pyright_output = pyright_section.strip()

            # Try to parse as JSON
            try:
                # Find the JSON object in the output
                json_start = pyright_section.find("{")
                json_end = pyright_section.rfind("}") + 1
                if json_start >= 0 and json_end > json_start:
                    pyright_json = json.loads(pyright_section[json_start:json_end])

                    for diag in pyright_json.get("generalDiagnostics", []):
                        range_info = diag.get("range", {})
                        start = range_info.get("start", {})

                        result.type_errors.append(
                            DiagnosticIssue(
                                file=diag.get("file", "unknown"),
                                line=start.get("line", 0) + 1,
                                column=start.get("character", 0) + 1,
                                severity=diag.get("severity", "error"),
                                code=diag.get("rule", "unknown"),
                                message=diag.get("message", ""),
                            )
                        )
            except json.JSONDecodeError:
                # Not valid JSON, might be plain text output
                pass

        return result

    def ensure_pyright_in_image(self, image_name: str) -> str:
        """Create a derived image with pyright installed.

        Returns the new image name.
        """
        derived_image = f"{image_name}-cgd"

        # Check if derived image already exists
        result = subprocess.run(
            ["docker", "images", "-q", derived_image],
            capture_output=True,
            text=True,
        )
        if result.stdout.strip():
            return derived_image

        # Build derived image
        dockerfile = f"""
FROM {image_name}
RUN pip install pyright || npm install -g pyright || echo "pyright install failed"
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            dockerfile_path = Path(tmpdir) / "Dockerfile"
            dockerfile_path.write_text(dockerfile)

            subprocess.run(
                ["docker", "build", "-t", derived_image, tmpdir],
                check=True,
                capture_output=True,
            )

        return derived_image


def run_diagnostics_batch(
    predictions_file: Path,
    output_file: Path,
    timeout: int = 120,
) -> None:
    """Run diagnostics on a batch of predictions.

    This is the main entry point for batch diagnostics collection.
    """
    runner = DockerDiagnosticsRunner(timeout=timeout)

    with open(predictions_file) as f:
        predictions = [json.loads(line) for line in f if line.strip()]

    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w") as f:
        for pred in predictions:
            instance_id = pred["instance_id"]
            patch = pred["model_patch"]

            logger.info(f"Running diagnostics for {instance_id}")

            result = runner.run_in_container(instance_id, patch)
            f.write(json.dumps(result.to_dict()) + "\n")
            f.flush()

    logger.info(f"Diagnostics saved to {output_file}")
