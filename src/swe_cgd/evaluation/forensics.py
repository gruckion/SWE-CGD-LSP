"""Forensics collection and analysis for hypothesis validation."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from datetime import datetime

from ..baseline.generator import CostMetrics
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

    # Diagnostic analysis
    had_syntax_errors: bool = False
    had_type_errors: bool = False
    diagnostic_error_count: int = 0
    diagnostic_summary: str = ""

    # Cost metrics (if available)
    cost_metrics: Optional[CostMetrics] = None
    repair_iterations: int = 0

    # From SWE-bench logs
    build_log_excerpt: str = ""
    test_log_excerpt: str = ""

    @property
    def had_diagnostic_issues(self) -> bool:
        """Did this instance have any diagnostic issues?"""
        return self.had_syntax_errors or self.had_type_errors

    def to_dict(self) -> dict:
        result = {
            "instance_id": self.instance_id,
            "status": self.status,
            "patch_applied": self.patch_applied,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "test_errors": self.test_errors,
            "had_syntax_errors": self.had_syntax_errors,
            "had_type_errors": self.had_type_errors,
            "had_diagnostic_issues": self.had_diagnostic_issues,
            "diagnostic_error_count": self.diagnostic_error_count,
            "diagnostic_summary": self.diagnostic_summary,
            "repair_iterations": self.repair_iterations,
        }
        if self.cost_metrics:
            result["cost_metrics"] = self.cost_metrics.to_dict()
        return result


@dataclass
class PredictiveMetrics:
    """Metrics for evaluating hypothesis: is early LSP error predictive of failure?"""

    # Counts for conditional probability calculation
    total_instances: int = 0
    total_with_diagnostic_issues: int = 0
    total_without_diagnostic_issues: int = 0

    failed_with_diagnostic_issues: int = 0
    failed_without_diagnostic_issues: int = 0
    resolved_with_diagnostic_issues: int = 0
    resolved_without_diagnostic_issues: int = 0

    @property
    def p_fail_given_error(self) -> float:
        """P(fail | early LSP error) - probability of failure given diagnostic issues."""
        if self.total_with_diagnostic_issues == 0:
            return 0.0
        return self.failed_with_diagnostic_issues / self.total_with_diagnostic_issues

    @property
    def p_fail_given_no_error(self) -> float:
        """P(fail | no early LSP error) - probability of failure without diagnostic issues."""
        if self.total_without_diagnostic_issues == 0:
            return 0.0
        return self.failed_without_diagnostic_issues / self.total_without_diagnostic_issues

    @property
    def predictiveness_ratio(self) -> float:
        """Ratio of P(fail|error) / P(fail|no error). >1 means errors are predictive of failure."""
        if self.p_fail_given_no_error == 0:
            return float('inf') if self.p_fail_given_error > 0 else 1.0
        return self.p_fail_given_error / self.p_fail_given_no_error

    @property
    def ceiling(self) -> float:
        """Fraction of failures that had early persistent LSP errors (fixable headroom)."""
        total_failures = self.failed_with_diagnostic_issues + self.failed_without_diagnostic_issues
        if total_failures == 0:
            return 0.0
        return self.failed_with_diagnostic_issues / total_failures

    @property
    def diagnostic_issue_rate(self) -> float:
        """What fraction of all instances had diagnostic issues?"""
        if self.total_instances == 0:
            return 0.0
        return self.total_with_diagnostic_issues / self.total_instances

    def to_dict(self) -> dict:
        return {
            "total_instances": self.total_instances,
            "total_with_diagnostic_issues": self.total_with_diagnostic_issues,
            "total_without_diagnostic_issues": self.total_without_diagnostic_issues,
            "failed_with_diagnostic_issues": self.failed_with_diagnostic_issues,
            "failed_without_diagnostic_issues": self.failed_without_diagnostic_issues,
            "resolved_with_diagnostic_issues": self.resolved_with_diagnostic_issues,
            "resolved_without_diagnostic_issues": self.resolved_without_diagnostic_issues,
            "p_fail_given_error": self.p_fail_given_error,
            "p_fail_given_no_error": self.p_fail_given_no_error,
            "predictiveness_ratio": self.predictiveness_ratio,
            "ceiling": self.ceiling,
            "diagnostic_issue_rate": self.diagnostic_issue_rate,
        }

    def get_summary(self) -> str:
        return f"""
Predictive Metrics (Hypothesis Validation)
==========================================
Total instances: {self.total_instances}
  With diagnostic issues: {self.total_with_diagnostic_issues} ({self.diagnostic_issue_rate:.1%})
  Without diagnostic issues: {self.total_without_diagnostic_issues}

Conditional Failure Rates:
  P(fail | LSP error):    {self.p_fail_given_error:.1%}
  P(fail | no LSP error): {self.p_fail_given_no_error:.1%}
  Predictiveness ratio:   {self.predictiveness_ratio:.2f}x

Ceiling (fixable headroom):
  {self.ceiling:.1%} of failures had detectable LSP errors

Interpretation:
  - Predictiveness ratio > 1.0 means LSP errors predict failure
  - Higher ceiling = more headroom for the intervention
"""


@dataclass
class CostAnalysis:
    """Cost analysis across instances."""

    total_tokens: int = 0
    total_wall_time: float = 0.0
    total_llm_calls: int = 0
    total_failed_calls: int = 0
    total_repair_iterations: int = 0

    # Per-outcome breakdown
    tokens_for_resolved: int = 0
    tokens_for_failed: int = 0
    instances_resolved: int = 0
    instances_failed: int = 0

    @property
    def cost_per_solved(self) -> float:
        """Average tokens per solved instance (headline metric)."""
        if self.instances_resolved == 0:
            return float('inf')
        return self.tokens_for_resolved / self.instances_resolved

    @property
    def cost_per_failed(self) -> float:
        """Average tokens per failed instance."""
        if self.instances_failed == 0:
            return 0.0
        return self.tokens_for_failed / self.instances_failed

    @property
    def cost_per_instance(self) -> float:
        """Average tokens per instance overall."""
        total = self.instances_resolved + self.instances_failed
        if total == 0:
            return 0.0
        return self.total_tokens / total

    @property
    def avg_repair_iterations(self) -> float:
        """Average repair iterations per instance."""
        total = self.instances_resolved + self.instances_failed
        if total == 0:
            return 0.0
        return self.total_repair_iterations / total

    def to_dict(self) -> dict:
        return {
            "total_tokens": self.total_tokens,
            "total_wall_time_seconds": self.total_wall_time,
            "total_llm_calls": self.total_llm_calls,
            "total_failed_calls": self.total_failed_calls,
            "total_repair_iterations": self.total_repair_iterations,
            "tokens_for_resolved": self.tokens_for_resolved,
            "tokens_for_failed": self.tokens_for_failed,
            "instances_resolved": self.instances_resolved,
            "instances_failed": self.instances_failed,
            "cost_per_solved": self.cost_per_solved if self.instances_resolved > 0 else None,
            "cost_per_failed": self.cost_per_failed if self.instances_failed > 0 else None,
            "cost_per_instance": self.cost_per_instance,
            "avg_repair_iterations": self.avg_repair_iterations,
        }

    def get_summary(self) -> str:
        return f"""
Cost Analysis (Headline Metrics)
================================
Total tokens: {self.total_tokens:,}
Total wall time: {self.total_wall_time:.1f}s
Total LLM calls: {self.total_llm_calls}
Failed calls: {self.total_failed_calls}
Total repair iterations: {self.total_repair_iterations}

Cost per solved task: {self.cost_per_solved:,.0f} tokens (HEADLINE METRIC)
Cost per failed task: {self.cost_per_failed:,.0f} tokens
Cost per instance: {self.cost_per_instance:,.0f} tokens
Avg repair iterations: {self.avg_repair_iterations:.2f}
"""


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

    # Predictive metrics (hypothesis validation)
    predictive_metrics: PredictiveMetrics = field(default_factory=PredictiveMetrics)

    # Cost analysis
    cost_analysis: CostAnalysis = field(default_factory=CostAnalysis)

    # Legacy fields for backward compatibility
    failures_with_diagnostic_issues: int = 0
    failures_without_diagnostic_issues: int = 0

    def add_instance(self, forensics: InstanceForensics) -> None:
        self.instances.append(forensics)

        # Update counts
        if forensics.status == "resolved":
            self.resolved += 1
        elif forensics.status == "failed":
            self.failed += 1
        elif forensics.status == "error":
            self.errors += 1
        elif forensics.status == "timeout":
            self.timeouts += 1

    def compute_metrics(self) -> None:
        """Compute all derived metrics from instance data."""
        # Reset metrics
        self.predictive_metrics = PredictiveMetrics()
        self.cost_analysis = CostAnalysis()

        for inst in self.instances:
            self.predictive_metrics.total_instances += 1

            has_issues = inst.had_diagnostic_issues

            if has_issues:
                self.predictive_metrics.total_with_diagnostic_issues += 1
                if inst.status == "failed":
                    self.predictive_metrics.failed_with_diagnostic_issues += 1
                elif inst.status == "resolved":
                    self.predictive_metrics.resolved_with_diagnostic_issues += 1
            else:
                self.predictive_metrics.total_without_diagnostic_issues += 1
                if inst.status == "failed":
                    self.predictive_metrics.failed_without_diagnostic_issues += 1
                elif inst.status == "resolved":
                    self.predictive_metrics.resolved_without_diagnostic_issues += 1

            # Cost analysis
            if inst.cost_metrics:
                self.cost_analysis.total_tokens += inst.cost_metrics.total_tokens
                self.cost_analysis.total_wall_time += inst.cost_metrics.wall_time_seconds
                self.cost_analysis.total_llm_calls += inst.cost_metrics.llm_calls
                self.cost_analysis.total_failed_calls += inst.cost_metrics.failed_calls

                if inst.status == "resolved":
                    self.cost_analysis.tokens_for_resolved += inst.cost_metrics.total_tokens
                    self.cost_analysis.instances_resolved += 1
                elif inst.status == "failed":
                    self.cost_analysis.tokens_for_failed += inst.cost_metrics.total_tokens
                    self.cost_analysis.instances_failed += 1

            self.cost_analysis.total_repair_iterations += inst.repair_iterations

        # Update legacy fields
        self.failures_with_diagnostic_issues = self.predictive_metrics.failed_with_diagnostic_issues
        self.failures_without_diagnostic_issues = self.predictive_metrics.failed_without_diagnostic_issues

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

{self.predictive_metrics.get_summary()}
{self.cost_analysis.get_summary()}
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
            "predictive_metrics": self.predictive_metrics.to_dict(),
            "cost_analysis": self.cost_analysis.to_dict(),
            # Legacy fields
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
        """Collect forensics from SWE-bench evaluation logs."""
        report = ForensicsReport(
            run_id=run_id,
            timestamp=datetime.now().isoformat(),
            total_instances=0,
            resolved=0,
            failed=0,
            errors=0,
            timeouts=0,
        )

        # Load results if available and build status mapping
        status_by_instance: dict[str, str] = {}
        if results_file and results_file.exists():
            with open(results_file) as f:
                results = json.load(f)
                for instance_id in results.get("resolved", []):
                    status_by_instance[instance_id] = "resolved"
                for instance_id in results.get("failed", []):
                    status_by_instance[instance_id] = "failed"
                for instance_id in results.get("error", []):
                    status_by_instance[instance_id] = "error"

        # Process evaluation logs
        eval_logs_dir = logs_dir / "run_evaluation" / run_id
        if eval_logs_dir.exists():
            for instance_dir in eval_logs_dir.iterdir():
                if not instance_dir.is_dir():
                    continue

                instance_id = instance_dir.name
                report.total_instances += 1

                status = status_by_instance.get(instance_id, "unknown")
                forensics = self._analyze_instance_logs(
                    instance_id,
                    instance_dir,
                    status,
                )
                report.add_instance(forensics)

        report.compute_metrics()
        return report

    def _analyze_instance_logs(
        self,
        instance_id: str,
        instance_dir: Path,
        status_from_results: str,
    ) -> InstanceForensics:
        """Analyze logs for a single instance."""
        forensics = InstanceForensics(
            instance_id=instance_id,
            status=status_from_results if status_from_results != "unknown" else "unknown",
        )

        # Check test output log
        test_output = instance_dir / "test_output.txt"
        if test_output.exists():
            content = test_output.read_text()
            forensics.test_log_excerpt = content[-2000:] if len(content) > 2000 else content

            if forensics.status == "unknown":
                if "PASSED" in content or "OK" in content:
                    forensics.status = "resolved"
                elif "FAILED" in content or "ERROR" in content:
                    forensics.status = "failed"

            forensics.tests_failed = content.count("FAILED")
            forensics.tests_passed = content.count("PASSED")

        # Check patch application
        patch_log = instance_dir / "patch_output.txt"
        if patch_log.exists():
            content = patch_log.read_text()
            forensics.patch_applied = "error" not in content.lower()

        return forensics

    def enrich_with_diagnostics(
        self,
        report: ForensicsReport,
        diagnostics_file: Path,
    ) -> ForensicsReport:
        """Enrich forensics report with diagnostic data."""
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

        # Recompute metrics with enriched data
        report.compute_metrics()
        return report

    def enrich_with_predictions(
        self,
        report: ForensicsReport,
        predictions_file: Path,
    ) -> ForensicsReport:
        """Enrich forensics report with prediction cost data."""
        if not predictions_file.exists():
            logger.warning(f"Predictions file not found: {predictions_file}")
            return report

        # Load predictions
        predictions_map = {}
        with open(predictions_file) as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    predictions_map[data["instance_id"]] = data

        # Enrich instances with cost metrics
        for instance in report.instances:
            if instance.instance_id in predictions_map:
                pred = predictions_map[instance.instance_id]
                if "cost_metrics" in pred:
                    instance.cost_metrics = CostMetrics.from_dict(pred["cost_metrics"])
                instance.repair_iterations = pred.get("repair_iterations", 0)

        # Recompute metrics with enriched data
        report.compute_metrics()
        return report


def evaluate_decision_gate(report: ForensicsReport) -> dict:
    """
    Evaluate the decision gate criteria from the hypothesis.

    Returns a dict with:
    - gate_passed: bool - whether the thesis is supported
    - criteria: dict - individual criteria evaluations
    - recommendation: str - what to do next
    """
    pm = report.predictive_metrics

    # Criterion 1: Early LSP Error is COMMON in failures
    # "Common" = at least 30% of failures had diagnostic issues
    criterion_1_threshold = 0.30
    criterion_1_value = pm.ceiling
    criterion_1_passed = criterion_1_value >= criterion_1_threshold

    # Criterion 2: LSP errors are PREDICTIVE of failure
    # Predictive = P(fail|error) > P(fail|no error) by meaningful margin
    criterion_2_threshold = 1.2  # At least 20% more likely to fail
    criterion_2_value = pm.predictiveness_ratio
    criterion_2_passed = criterion_2_value >= criterion_2_threshold

    # Overall gate
    gate_passed = criterion_1_passed and criterion_2_passed

    if gate_passed:
        recommendation = (
            "PROCEED: The data supports the hypothesis. "
            "LSP errors are common in failures and predictive of failure. "
            "Consider implementing the lazy proxy POC."
        )
    elif criterion_1_passed and not criterion_2_passed:
        recommendation = (
            "WEAK SIGNAL: LSP errors are common but not strongly predictive. "
            "The intervention may have limited impact. "
            "Consider investigating why errors don't predict failure."
        )
    elif not criterion_1_passed and criterion_2_passed:
        recommendation = (
            "LIMITED HEADROOM: LSP errors predict failure but are rare. "
            "The ceiling for improvement is low. "
            "Consider expanding diagnostic coverage."
        )
    else:
        recommendation = (
            "DO NOT PROCEED: LSP errors are neither common nor predictive. "
            "The thesis is not supported by the data. "
            "Consider pivoting to a different approach."
        )

    return {
        "gate_passed": gate_passed,
        "criteria": {
            "errors_common_in_failures": {
                "passed": criterion_1_passed,
                "value": criterion_1_value,
                "threshold": criterion_1_threshold,
                "description": f"Ceiling >= {criterion_1_threshold:.0%}",
            },
            "errors_predictive_of_failure": {
                "passed": criterion_2_passed,
                "value": criterion_2_value,
                "threshold": criterion_2_threshold,
                "description": f"Predictiveness ratio >= {criterion_2_threshold:.1f}x",
            },
        },
        "recommendation": recommendation,
    }
