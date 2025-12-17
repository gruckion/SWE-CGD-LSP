"""Forensics collection for failed predictions."""

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from datetime import datetime

from ..utils.config import Config
from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class InstanceForensics:
    """Forensic data for a single instance."""

    instance_id: str
    status: str  # resolved, failed, error, timeout
    patch_applied: bool = False
    tests_passed: int = 0
    tests_failed: int = 0
    test_errors: list[str] = field(default_factory=list)

    # Diagnostic analysis (if we ran it)
    had_syntax_errors: bool = False
    had_type_errors: bool = False
    diagnostic_error_count: int = 0
    diagnostic_summary: str = ""

    # From SWE-bench logs
    build_log_excerpt: str = ""
    test_log_excerpt: str = ""

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "status": self.status,
            "patch_applied": self.patch_applied,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "test_errors": self.test_errors,
            "had_syntax_errors": self.had_syntax_errors,
            "had_type_errors": self.had_type_errors,
            "diagnostic_error_count": self.diagnostic_error_count,
            "diagnostic_summary": self.diagnostic_summary,
        }


@dataclass
class ForensicsReport:
    """Full forensics report for an evaluation run."""

    run_id: str
    timestamp: str
    total_instances: int
    resolved: int
    failed: int
    errors: int
    timeouts: int

    instances: list[InstanceForensics] = field(default_factory=list)

    # Aggregated analysis
    failures_with_diagnostic_issues: int = 0
    failures_without_diagnostic_issues: int = 0

    def add_instance(self, forensics: InstanceForensics) -> None:
        self.instances.append(forensics)

        if forensics.status == "resolved":
            self.resolved += 1
        elif forensics.status == "failed":
            self.failed += 1
            if forensics.had_syntax_errors or forensics.had_type_errors:
                self.failures_with_diagnostic_issues += 1
            else:
                self.failures_without_diagnostic_issues += 1
        elif forensics.status == "error":
            self.errors += 1
        elif forensics.status == "timeout":
            self.timeouts += 1

    @property
    def pass_rate(self) -> float:
        if self.total_instances == 0:
            return 0.0
        return self.resolved / self.total_instances

    def get_summary(self) -> str:
        return f"""
Forensics Report: {self.run_id}
================================
Timestamp: {self.timestamp}
Total Instances: {self.total_instances}

Results:
  Resolved: {self.resolved} ({self.pass_rate:.1%})
  Failed: {self.failed}
  Errors: {self.errors}
  Timeouts: {self.timeouts}

Failure Analysis:
  With diagnostic issues: {self.failures_with_diagnostic_issues}
  Without diagnostic issues: {self.failures_without_diagnostic_issues}
"""

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "total_instances": self.total_instances,
            "resolved": self.resolved,
            "failed": self.failed,
            "errors": self.errors,
            "timeouts": self.timeouts,
            "pass_rate": self.pass_rate,
            "failures_with_diagnostic_issues": self.failures_with_diagnostic_issues,
            "failures_without_diagnostic_issues": self.failures_without_diagnostic_issues,
            "instances": [inst.to_dict() for inst in self.instances],
        }

    def save(self, output_path: Path) -> None:
        """Save report to JSON file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info(f"Saved forensics report to {output_path}")


class ForensicsCollector:
    """Collects forensic data from SWE-bench evaluation results."""

    def __init__(self, config: Config):
        self.config = config

    def collect_from_logs(
        self,
        run_id: str,
        logs_dir: Path,
        results_file: Optional[Path] = None,
    ) -> ForensicsReport:
        """Collect forensics from SWE-bench evaluation logs.

        Args:
            run_id: The run ID used in evaluation
            logs_dir: Directory containing SWE-bench logs
            results_file: Optional path to results JSON

        Returns:
            ForensicsReport with aggregated data
        """
        report = ForensicsReport(
            run_id=run_id,
            timestamp=datetime.now().isoformat(),
            total_instances=0,
            resolved=0,
            failed=0,
            errors=0,
            timeouts=0,
        )

        # Load results if available
        results = {}
        if results_file and results_file.exists():
            with open(results_file) as f:
                results = json.load(f)

        # Process evaluation logs
        eval_logs_dir = logs_dir / "run_evaluation" / run_id
        if eval_logs_dir.exists():
            for instance_dir in eval_logs_dir.iterdir():
                if not instance_dir.is_dir():
                    continue

                instance_id = instance_dir.name
                report.total_instances += 1

                forensics = self._analyze_instance_logs(
                    instance_id,
                    instance_dir,
                    results.get(instance_id, {}),
                )
                report.add_instance(forensics)

        return report

    def _analyze_instance_logs(
        self,
        instance_id: str,
        instance_dir: Path,
        results: dict,
    ) -> InstanceForensics:
        """Analyze logs for a single instance."""

        forensics = InstanceForensics(
            instance_id=instance_id,
            status="unknown",
        )

        # Check test output log
        test_output = instance_dir / "test_output.txt"
        if test_output.exists():
            content = test_output.read_text()
            forensics.test_log_excerpt = content[-2000:] if len(content) > 2000 else content

            # Parse test results (basic heuristics)
            if "PASSED" in content or "OK" in content:
                forensics.status = "resolved"
            elif "FAILED" in content or "ERROR" in content:
                forensics.status = "failed"
                # Count failures
                forensics.tests_failed = content.count("FAILED")
                forensics.tests_passed = content.count("PASSED")

        # Check patch application
        patch_log = instance_dir / "patch_output.txt"
        if patch_log.exists():
            content = patch_log.read_text()
            forensics.patch_applied = "error" not in content.lower()

        # If we have explicit results
        if "status" in results:
            forensics.status = results["status"]

        return forensics

    def enrich_with_diagnostics(
        self,
        report: ForensicsReport,
        diagnostics_file: Path,
    ) -> ForensicsReport:
        """Enrich forensics report with diagnostic data.

        Args:
            report: Base forensics report
            diagnostics_file: JSONL file with diagnostic results

        Returns:
            Enriched report
        """
        if not diagnostics_file.exists():
            logger.warning(f"Diagnostics file not found: {diagnostics_file}")
            return report

        # Load diagnostics
        diagnostics_map = {}
        with open(diagnostics_file) as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    diagnostics_map[data["instance_id"]] = data

        # Enrich instances
        for instance in report.instances:
            if instance.instance_id in diagnostics_map:
                diag = diagnostics_map[instance.instance_id]
                instance.had_syntax_errors = len(diag.get("syntax_errors", [])) > 0
                instance.had_type_errors = len(diag.get("type_errors", [])) > 0
                instance.diagnostic_error_count = (
                    len(diag.get("syntax_errors", [])) + len(diag.get("type_errors", []))
                )
                instance.diagnostic_summary = diag.get("summary", "")

        # Recalculate aggregates
        report.failures_with_diagnostic_issues = sum(
            1
            for inst in report.instances
            if inst.status == "failed" and (inst.had_syntax_errors or inst.had_type_errors)
        )
        report.failures_without_diagnostic_issues = sum(
            1
            for inst in report.instances
            if inst.status == "failed" and not (inst.had_syntax_errors or inst.had_type_errors)
        )

        return report
