"""Results comparison between baseline and cleanup experiments."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table

from ..utils.logging import get_logger

logger = get_logger(__name__)
console = Console()


@dataclass
class ExperimentResults:
    """Results from a single experiment run."""

    name: str
    total: int
    resolved: int
    failed: int
    errors: int

    instance_results: dict[str, str] = field(default_factory=dict)  # instance_id -> status

    @property
    def pass_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.resolved / self.total

    @classmethod
    def from_swebench_results(cls, name: str, results_file: Path) -> "ExperimentResults":
        """Load results from SWE-bench evaluation output."""
        if not results_file.exists():
            raise FileNotFoundError(f"Results file not found: {results_file}")

        with open(results_file) as f:
            data = json.load(f)

        # SWE-bench results format
        resolved = len(data.get("resolved", []))
        instance_results = {}

        for instance_id in data.get("resolved", []):
            instance_results[instance_id] = "resolved"
        for instance_id in data.get("failed", []):
            instance_results[instance_id] = "failed"
        for instance_id in data.get("error", []):
            instance_results[instance_id] = "error"

        return cls(
            name=name,
            total=len(instance_results),
            resolved=resolved,
            failed=len(data.get("failed", [])),
            errors=len(data.get("error", [])),
            instance_results=instance_results,
        )


@dataclass
class ComparisonResult:
    """Comparison between baseline and treatment."""

    baseline: ExperimentResults
    treatment: ExperimentResults

    # Analysis
    improved: list[str] = field(default_factory=list)  # Failed baseline -> resolved treatment
    regressed: list[str] = field(default_factory=list)  # Resolved baseline -> failed treatment
    unchanged_pass: list[str] = field(default_factory=list)  # Both resolved
    unchanged_fail: list[str] = field(default_factory=list)  # Both failed

    def analyze(self) -> None:
        """Analyze differences between baseline and treatment."""
        all_instances = set(self.baseline.instance_results.keys()) | set(
            self.treatment.instance_results.keys()
        )

        for instance_id in all_instances:
            baseline_status = self.baseline.instance_results.get(instance_id, "missing")
            treatment_status = self.treatment.instance_results.get(instance_id, "missing")

            baseline_passed = baseline_status == "resolved"
            treatment_passed = treatment_status == "resolved"

            if not baseline_passed and treatment_passed:
                self.improved.append(instance_id)
            elif baseline_passed and not treatment_passed:
                self.regressed.append(instance_id)
            elif baseline_passed and treatment_passed:
                self.unchanged_pass.append(instance_id)
            else:
                self.unchanged_fail.append(instance_id)

    @property
    def improvement_rate(self) -> float:
        """Rate of failures that were fixed by treatment."""
        total_baseline_failures = len(self.improved) + len(self.unchanged_fail)
        if total_baseline_failures == 0:
            return 0.0
        return len(self.improved) / total_baseline_failures

    @property
    def regression_rate(self) -> float:
        """Rate of passes that regressed in treatment."""
        total_baseline_passes = len(self.regressed) + len(self.unchanged_pass)
        if total_baseline_passes == 0:
            return 0.0
        return len(self.regressed) / total_baseline_passes

    @property
    def net_improvement(self) -> int:
        """Net change in resolved instances."""
        return len(self.improved) - len(self.regressed)

    def get_summary(self) -> str:
        """Generate human-readable summary."""
        return f"""
Comparison: {self.baseline.name} vs {self.treatment.name}
=========================================================

Baseline ({self.baseline.name}):
  Total: {self.baseline.total}
  Resolved: {self.baseline.resolved} ({self.baseline.pass_rate:.1%})

Treatment ({self.treatment.name}):
  Total: {self.treatment.total}
  Resolved: {self.treatment.resolved} ({self.treatment.pass_rate:.1%})

Changes:
  Improved (failed->resolved): {len(self.improved)}
  Regressed (resolved->failed): {len(self.regressed)}
  Unchanged (both pass): {len(self.unchanged_pass)}
  Unchanged (both fail): {len(self.unchanged_fail)}

Net Improvement: {self.net_improvement:+d} instances
Improvement Rate: {self.improvement_rate:.1%} of baseline failures fixed
Regression Rate: {self.regression_rate:.1%} of baseline passes regressed
"""

    def print_table(self) -> None:
        """Print comparison as a rich table."""
        table = Table(title="Experiment Comparison")

        table.add_column("Metric", style="cyan")
        table.add_column(self.baseline.name, justify="right")
        table.add_column(self.treatment.name, justify="right")
        table.add_column("Change", justify="right")

        # Pass rates
        table.add_row(
            "Resolved",
            str(self.baseline.resolved),
            str(self.treatment.resolved),
            f"{self.net_improvement:+d}",
        )
        table.add_row(
            "Pass Rate",
            f"{self.baseline.pass_rate:.1%}",
            f"{self.treatment.pass_rate:.1%}",
            f"{(self.treatment.pass_rate - self.baseline.pass_rate):+.1%}",
        )

        table.add_section()
        table.add_row("Improved", "", str(len(self.improved)), "")
        table.add_row("Regressed", "", str(len(self.regressed)), "")

        console.print(table)

    def to_dict(self) -> dict:
        return {
            "baseline": {
                "name": self.baseline.name,
                "total": self.baseline.total,
                "resolved": self.baseline.resolved,
                "pass_rate": self.baseline.pass_rate,
            },
            "treatment": {
                "name": self.treatment.name,
                "total": self.treatment.total,
                "resolved": self.treatment.resolved,
                "pass_rate": self.treatment.pass_rate,
            },
            "improved": self.improved,
            "regressed": self.regressed,
            "unchanged_pass": self.unchanged_pass,
            "unchanged_fail": self.unchanged_fail,
            "net_improvement": self.net_improvement,
            "improvement_rate": self.improvement_rate,
            "regression_rate": self.regression_rate,
        }


class ResultsComparator:
    """Compare results between different experiment runs."""

    def compare(
        self,
        baseline_results: Path,
        treatment_results: Path,
        baseline_name: str = "baseline",
        treatment_name: str = "treatment",
    ) -> ComparisonResult:
        """Compare baseline vs treatment results."""
        baseline = ExperimentResults.from_swebench_results(baseline_name, baseline_results)
        treatment = ExperimentResults.from_swebench_results(treatment_name, treatment_results)

        comparison = ComparisonResult(baseline=baseline, treatment=treatment)
        comparison.analyze()

        return comparison

    def compare_multiple(
        self,
        results_files: list[tuple[str, Path]],
    ) -> None:
        """Compare multiple experiment results."""
        experiments = []
        for name, path in results_files:
            try:
                exp = ExperimentResults.from_swebench_results(name, path)
                experiments.append(exp)
            except FileNotFoundError:
                logger.warning(f"Results file not found: {path}")

        if len(experiments) < 2:
            logger.warning("Need at least 2 experiments to compare")
            return

        # Print comparison table
        table = Table(title="Multi-Experiment Comparison")
        table.add_column("Experiment", style="cyan")
        table.add_column("Total", justify="right")
        table.add_column("Resolved", justify="right")
        table.add_column("Pass Rate", justify="right")

        for exp in experiments:
            table.add_row(exp.name, str(exp.total), str(exp.resolved), f"{exp.pass_rate:.1%}")

        console.print(table)
