"""Diagnostics runner for analyzing patches."""

import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from unidiff import PatchSet

from ..utils.config import Config
from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class DiagnosticIssue:
    """A single diagnostic issue."""

    file: str
    line: int
    column: int
    severity: str  # error, warning, information
    code: str
    message: str

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DiagnosticIssue":
        """Create DiagnosticIssue from dictionary."""
        return cls(
            file=data.get("file", "unknown"),
            line=data.get("line", 1),
            column=data.get("column", 1),
            severity=data.get("severity", "error"),
            code=data.get("code", "unknown"),
            message=data.get("message", ""),
        )


@dataclass
class DiagnosticResult:
    """Result of running diagnostics on a patch."""

    instance_id: str
    patch_applied: bool
    apply_error: Optional[str] = None
    syntax_errors: list[DiagnosticIssue] = field(default_factory=list)
    type_errors: list[DiagnosticIssue] = field(default_factory=list)
    pyright_output: Optional[str] = None
    compileall_output: Optional[str] = None

    @property
    def has_issues(self) -> bool:
        """Check if there are any issues (including patch apply failures)."""
        return bool(
            not self.patch_applied
            or self.syntax_errors
            or self.type_errors
            or self.apply_error
        )

    @property
    def total_errors(self) -> int:
        """Count total errors (excluding warnings)."""
        return sum(
            1 for issue in self.syntax_errors + self.type_errors if issue.severity == "error"
        )

    def get_summary(self) -> str:
        """Get a human-readable summary."""
        if not self.patch_applied:
            return f"Patch failed to apply: {self.apply_error}"

        if not self.has_issues:
            return "No diagnostic issues found"

        parts = []
        if self.syntax_errors:
            parts.append(f"{len(self.syntax_errors)} syntax errors")
        if self.type_errors:
            parts.append(f"{len(self.type_errors)} type errors")

        return ", ".join(parts)

    def to_llm_context(self) -> str:
        """Format diagnostics for LLM consumption."""
        lines = []

        if not self.patch_applied:
            lines.append(f"## Patch Application Error\n{self.apply_error}")
            return "\n".join(lines)

        if self.syntax_errors:
            lines.append("## Syntax Errors")
            for issue in self.syntax_errors:
                lines.append(f"- {issue.file}:{issue.line}:{issue.column}: {issue.message}")

        if self.type_errors:
            lines.append("## Type/Symbol Errors (from Pyright)")
            for issue in self.type_errors[:20]:  # Limit to prevent overwhelming context
                lines.append(
                    f"- {issue.file}:{issue.line}:{issue.column} [{issue.code}]: {issue.message}"
                )
            if len(self.type_errors) > 20:
                lines.append(f"... and {len(self.type_errors) - 20} more type errors")

        if not lines:
            return "No diagnostic issues detected."

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "patch_applied": self.patch_applied,
            "apply_error": self.apply_error,
            "syntax_errors": [e.to_dict() for e in self.syntax_errors],
            "type_errors": [e.to_dict() for e in self.type_errors],
            "pyright_output": self.pyright_output,
            "compileall_output": self.compileall_output,
            "summary": self.get_summary(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DiagnosticResult":
        """Create DiagnosticResult from dictionary."""
        return cls(
            instance_id=data["instance_id"],
            patch_applied=data.get("patch_applied", False),
            apply_error=data.get("apply_error"),
            syntax_errors=[
                DiagnosticIssue.from_dict(e) for e in data.get("syntax_errors", [])
            ],
            type_errors=[
                DiagnosticIssue.from_dict(e) for e in data.get("type_errors", [])
            ],
            pyright_output=data.get("pyright_output"),
            compileall_output=data.get("compileall_output"),
        )


class DiagnosticsRunner:
    """Runs diagnostics (pyright, compileall) on patched code."""

    def __init__(self, config: Config):
        self.config = config

    def get_changed_files(self, patch: str) -> list[str]:
        """Extract list of changed files from a patch."""
        if not patch.strip():
            return []

        try:
            patchset = PatchSet(patch)
            return [pf.path for pf in patchset]
        except Exception as e:
            logger.warning(f"Could not parse patch: {e}")
            # Fallback: try to extract from diff headers
            files = []
            for line in patch.split("\n"):
                if line.startswith("+++ b/"):
                    files.append(line[6:])
                elif line.startswith("+++ ") and not line.startswith("+++ /dev/null"):
                    files.append(line[4:])
            return files

    def apply_patch(self, repo_dir: Path, patch: str) -> tuple[bool, Optional[str]]:
        """Apply a patch to a repository directory."""
        if not patch.strip():
            return False, "Empty patch"

        try:
            result = subprocess.run(
                ["git", "apply", "--check", "-"],
                input=patch,
                capture_output=True,
                text=True,
                cwd=repo_dir,
                timeout=30,
            )

            if result.returncode != 0:
                return False, result.stderr.strip() or "Patch does not apply cleanly"

            # Actually apply the patch
            result = subprocess.run(
                ["git", "apply", "-"],
                input=patch,
                capture_output=True,
                text=True,
                cwd=repo_dir,
                timeout=30,
            )

            if result.returncode != 0:
                return False, result.stderr.strip()

            return True, None

        except subprocess.TimeoutExpired:
            return False, "Patch application timed out"
        except Exception as e:
            return False, str(e)

    def run_py_compile(self, repo_dir: Path, files: list[str]) -> list[DiagnosticIssue]:
        """Run Python's py_compile to check for syntax errors."""
        issues = []

        for file_path in files:
            if not file_path.endswith(".py"):
                continue

            full_path = repo_dir / file_path
            if not full_path.exists():
                continue

            try:
                result = subprocess.run(
                    ["python", "-m", "py_compile", str(full_path)],
                    capture_output=True,
                    text=True,
                    timeout=self.config.diagnostics.timeout_seconds,
                )

                if result.returncode != 0:
                    # Parse syntax error from stderr
                    error_msg = result.stderr.strip()
                    # Try to extract line number from error message
                    line_num = 1
                    if "line " in error_msg.lower():
                        import re
                        match = re.search(r"line (\d+)", error_msg, re.IGNORECASE)
                        if match:
                            line_num = int(match.group(1))

                    issues.append(
                        DiagnosticIssue(
                            file=file_path,
                            line=line_num,
                            column=1,
                            severity="error",
                            code="SyntaxError",
                            message=error_msg,
                        )
                    )

            except subprocess.TimeoutExpired:
                logger.warning(f"Compile check timed out for {file_path}")
            except Exception as e:
                logger.warning(f"Compile check failed for {file_path}: {e}")

        return issues

    def run_pyright(self, repo_dir: Path, files: list[str]) -> tuple[list[DiagnosticIssue], str]:
        """Run pyright type checker on specified files."""
        issues = []

        py_files = [f for f in files if f.endswith(".py")]
        if not py_files:
            return [], ""

        try:
            # Run pyright in JSON output mode
            cmd = [
                "pyright",
                "--outputjson",
                "--level",
                self.config.diagnostics.pyright_level,
            ] + [str(repo_dir / f) for f in py_files if (repo_dir / f).exists()]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=repo_dir,
                timeout=self.config.diagnostics.timeout_seconds,
            )

            raw_output = result.stdout

            try:
                output = json.loads(result.stdout)

                for diag in output.get("generalDiagnostics", []):
                    file_path = diag.get("file", "unknown")
                    # Convert absolute path to relative
                    if file_path.startswith(str(repo_dir)):
                        file_path = file_path[len(str(repo_dir)) + 1 :]

                    range_info = diag.get("range", {})
                    start = range_info.get("start", {})

                    issues.append(
                        DiagnosticIssue(
                            file=file_path,
                            line=start.get("line", 0) + 1,  # 0-indexed to 1-indexed
                            column=start.get("character", 0) + 1,
                            severity=diag.get("severity", "error"),
                            code=diag.get("rule", "unknown"),
                            message=diag.get("message", ""),
                        )
                    )

            except json.JSONDecodeError:
                # Fall back to text parsing if JSON fails
                logger.warning("Pyright JSON parsing failed, using raw output")

            return issues, raw_output

        except FileNotFoundError:
            logger.warning("Pyright not found, skipping type checking")
            return [], "pyright not installed"
        except subprocess.TimeoutExpired:
            logger.warning("Pyright timed out")
            return [], "timeout"
        except Exception as e:
            logger.warning(f"Pyright failed: {e}")
            return [], str(e)

    def run_diagnostics(
        self,
        instance_id: str,
        patch: str,
        repo_dir: Path,
    ) -> DiagnosticResult:
        """Run all diagnostics on a patch applied to a repository."""

        result = DiagnosticResult(instance_id=instance_id, patch_applied=False)

        # Apply the patch
        applied, error = self.apply_patch(repo_dir, patch)
        result.patch_applied = applied
        result.apply_error = error

        if not applied:
            return result

        # Get changed files
        changed_files = self.get_changed_files(patch)

        # Run py_compile for syntax errors
        if self.config.diagnostics.use_compileall:
            result.syntax_errors = self.run_py_compile(repo_dir, changed_files)

        # Run pyright for type errors
        if self.config.diagnostics.use_pyright:
            type_issues, raw_output = self.run_pyright(repo_dir, changed_files)
            result.type_errors = type_issues
            result.pyright_output = raw_output

        return result

    def run_diagnostics_in_temp(
        self,
        instance_id: str,
        patch: str,
        base_repo_url: Optional[str] = None,
        base_commit: Optional[str] = None,
    ) -> DiagnosticResult:
        """Run diagnostics by creating a temporary repo and applying the patch.

        This is useful when you don't have the original repo checked out.
        For SWE-bench evaluation, we typically run diagnostics inside the Docker container.
        """
        # This is a simplified version - in practice, SWE-bench evaluation
        # uses Docker containers with the repo already set up
        result = DiagnosticResult(instance_id=instance_id, patch_applied=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir)

            # Initialize a git repo to test patch application
            subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True)

            # For basic testing, we can still check patch format
            changed_files = self.get_changed_files(patch)

            if not changed_files:
                result.apply_error = "No files changed in patch"
                return result

            # Note: Full diagnostics require the actual repo content
            # This is a placeholder for the Docker-based approach
            result.patch_applied = True
            result.apply_error = "Diagnostics require Docker container (use run_in_container)"

        return result
