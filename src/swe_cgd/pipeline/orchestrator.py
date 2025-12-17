"""Full pipeline orchestrator for CGD validation experiments."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
import subprocess

from ..baseline.generator import BaselineGenerator, Prediction, CostMetrics
from ..cleanup.cleaner import PatchCleaner
from ..diagnostics.runner import DiagnosticsRunner, DiagnosticResult
from ..evaluation.comparison import ResultsComparator, ComparisonResult
from ..evaluation.forensics import ForensicsCollector, ForensicsReport, evaluate_decision_gate
from ..utils.config import Config
from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PipelineResults:
    """Results from a full pipeline run with cost tracking."""

    run_id: str
    timestamp: str
    config: dict

    baseline_predictions_path: Path
    cleanup_predictions_path: Optional[Path] = None

    baseline_results_path: Optional[Path] = None
    cleanup_results_path: Optional[Path] = None

    diagnostics_path: Optional[Path] = None
    forensics_path: Optional[Path] = None

    comparison: Optional[ComparisonResult] = None
    forensics_report: Optional[ForensicsReport] = None
    decision_gate: Optional[dict] = None

    # Aggregate cost metrics
    baseline_cost: CostMetrics = field(default_factory=CostMetrics)
    cleanup_cost: CostMetrics = field(default_factory=CostMetrics)
    total_cost: CostMetrics = field(default_factory=CostMetrics)

    def save_summary(self, output_path: Path) -> None:
        """Save pipeline results summary."""
        summary = {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "paths": {
                "baseline_predictions": str(self.baseline_predictions_path),
                "cleanup_predictions": str(self.cleanup_predictions_path)
                if self.cleanup_predictions_path
                else None,
                "baseline_results": str(self.baseline_results_path)
                if self.baseline_results_path
                else None,
                "cleanup_results": str(self.cleanup_results_path)
                if self.cleanup_results_path
                else None,
                "diagnostics": str(self.diagnostics_path) if self.diagnostics_path else None,
                "forensics": str(self.forensics_path) if self.forensics_path else None,
            },
            "cost_metrics": {
                "baseline": self.baseline_cost.to_dict(),
                "cleanup": self.cleanup_cost.to_dict(),
                "total": self.total_cost.to_dict(),
            },
            "comparison": self.comparison.to_dict() if self.comparison else None,
            "decision_gate": self.decision_gate,
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(summary, f, indent=2)


class PipelineOrchestrator:
    """Orchestrates the full CGD validation pipeline."""

    def __init__(
        self,
        config: Config,
        max_repair_iterations: int = 3,
        max_repair_tokens: int = 50000,
    ):
        self.config = config
        self.max_repair_iterations = max_repair_iterations
        self.max_repair_tokens = max_repair_tokens

        self.baseline_generator = BaselineGenerator(config)
        self.diagnostics_runner = DiagnosticsRunner(config)
        self.patch_cleaner = PatchCleaner(
            config,
            max_repair_iterations=max_repair_iterations,
            max_repair_tokens=max_repair_tokens,
        )
        self.forensics_collector = ForensicsCollector(config)
        self.results_comparator = ResultsComparator()

    def run_swebench_evaluation(
        self,
        predictions_path: Path,
        run_id: str,
    ) -> Optional[Path]:
        """Run SWE-bench evaluation using the harness."""
        cmd = [
            "python",
            "-m",
            "swebench.harness.run_evaluation",
            "--predictions_path",
            str(predictions_path),
            "--dataset_name",
            self.config.evaluation.dataset_name,
            "--max_workers",
            str(self.config.evaluation.max_workers),
            "--run_id",
            run_id,
        ]

        if self.config.instance_ids:
            cmd.extend(["--instance_ids"] + self.config.instance_ids)

        logger.info(f"Running SWE-bench evaluation: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.evaluation.timeout_seconds * (self.config.max_instances or 100),
            )

            if result.returncode != 0:
                logger.error(f"SWE-bench evaluation failed: {result.stderr}")
                return None

            results_path = Path("evaluation_results") / run_id / "results.json"
            if results_path.exists():
                return results_path

            logger.warning("Could not find results file after evaluation")
            return None

        except subprocess.TimeoutExpired:
            logger.error("SWE-bench evaluation timed out")
            return None
        except FileNotFoundError:
            logger.error("swebench not installed - run: pip install swebench")
            return None
        except Exception as e:
            logger.error(f"SWE-bench evaluation failed: {e}")
            return None

    def generate_diagnostics_in_container(
        self,
        predictions_path: Path,
        output_path: Path,
        use_docker: bool = True,
    ) -> None:
        """Generate diagnostics by running pyright inside SWE-bench containers."""
        from ..diagnostics.docker_runner import DockerDiagnosticsRunner

        predictions = []
        with open(predictions_path) as f:
            for line in f:
                if line.strip():
                    predictions.append(json.loads(line))

        docker_runner = DockerDiagnosticsRunner(
            timeout=self.config.diagnostics.timeout_seconds
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w") as f:
            for pred in predictions:
                instance_id = pred["instance_id"]
                patch = pred["model_patch"]

                logger.info(f"Collecting diagnostics for {instance_id}")

                if use_docker:
                    try:
                        diag_result = docker_runner.run_in_container(instance_id, patch)
                    except Exception as e:
                        logger.error(f"Docker diagnostics failed for {instance_id}: {e}")
                        diag_result = DiagnosticResult(
                            instance_id=instance_id,
                            patch_applied=False,
                            apply_error=f"Docker error: {e}",
                        )
                else:
                    changed_files = self.diagnostics_runner.get_changed_files(patch)
                    diag_result = DiagnosticResult(
                        instance_id=instance_id,
                        patch_applied=bool(patch.strip()) and bool(changed_files),
                        apply_error="Empty patch" if not patch.strip() else None,
                    )

                logger.debug(f"Diagnostics for {instance_id}: {diag_result.get_summary()}")
                f.write(json.dumps(diag_result.to_dict()) + "\n")
                f.flush()

        logger.info(f"Saved diagnostics to {output_path}")

    def _create_diagnostics_fn(self, use_docker: bool = True):
        """Create a diagnostics function for the repair loop."""
        from ..diagnostics.docker_runner import DockerDiagnosticsRunner

        if use_docker:
            docker_runner = DockerDiagnosticsRunner(
                timeout=self.config.diagnostics.timeout_seconds
            )

            def run_diagnostics(instance_id: str, patch: str) -> DiagnosticResult:
                try:
                    return docker_runner.run_in_container(instance_id, patch)
                except Exception as e:
                    logger.error(f"Docker diagnostics failed for {instance_id}: {e}")
                    return DiagnosticResult(
                        instance_id=instance_id,
                        patch_applied=False,
                        apply_error=f"Docker error: {e}",
                    )

            return run_diagnostics
        else:
            return None  # One-shot mode without re-running diagnostics

    def run_full_experiment(
        self,
        run_id: Optional[str] = None,
        skip_evaluation: bool = False,
        use_repair_loop: bool = True,
    ) -> PipelineResults:
        """Run the complete CGD validation experiment.

        Args:
            run_id: Unique identifier for this run
            skip_evaluation: Skip SWE-bench evaluation (for testing)
            use_repair_loop: Use iterative repair loop (vs one-shot cleanup)

        Returns:
            PipelineResults with all paths, comparison data, and cost metrics
        """
        if run_id is None:
            run_id = datetime.now().strftime("cgd_%Y%m%d_%H%M%S")

        logger.info(f"Starting CGD experiment: {run_id}")

        results = PipelineResults(
            run_id=run_id,
            timestamp=datetime.now().isoformat(),
            config=self.config.model_dump(),
            baseline_predictions_path=self.config.preds_dir / f"{run_id}_baseline.jsonl",
        )

        # Step 1: Generate baseline predictions
        logger.info("Step 1: Generating baseline predictions")
        baseline_preds = self.baseline_generator.generate_all(results.baseline_predictions_path)

        # Calculate baseline cost
        for pred in baseline_preds:
            if pred.cost_metrics:
                results.baseline_cost = results.baseline_cost + pred.cost_metrics
        results.total_cost = results.baseline_cost

        # Step 2: Run SWE-bench evaluation on baseline
        if not skip_evaluation:
            logger.info("Step 2: Evaluating baseline predictions")
            results.baseline_results_path = self.run_swebench_evaluation(
                results.baseline_predictions_path,
                f"{run_id}_baseline",
            )

        # Step 3: Collect diagnostics
        logger.info("Step 3: Collecting diagnostics")
        results.diagnostics_path = self.config.output_dir / f"{run_id}_diagnostics.jsonl"
        self.generate_diagnostics_in_container(
            results.baseline_predictions_path,
            results.diagnostics_path,
        )

        # Step 4: Generate cleanup predictions with repair loop
        logger.info("Step 4: Generating cleanup predictions")
        results.cleanup_predictions_path = self.config.preds_dir / f"{run_id}_cleanup.jsonl"

        # Create diagnostics function for repair loop if enabled
        diagnostics_fn = self._create_diagnostics_fn(use_docker=True) if use_repair_loop else None

        cleanup_cost = self._generate_cleanup_predictions(
            results.baseline_predictions_path,
            results.diagnostics_path,
            results.cleanup_predictions_path,
            diagnostics_fn,
        )
        results.cleanup_cost = cleanup_cost
        results.total_cost = results.total_cost + cleanup_cost

        # Step 5: Run SWE-bench evaluation on cleanup
        if not skip_evaluation:
            logger.info("Step 5: Evaluating cleanup predictions")
            results.cleanup_results_path = self.run_swebench_evaluation(
                results.cleanup_predictions_path,
                f"{run_id}_cleanup",
            )

        # Step 6: Compare results
        if results.baseline_results_path and results.cleanup_results_path:
            logger.info("Step 6: Comparing results")
            results.comparison = self.results_comparator.compare(
                results.baseline_results_path,
                results.cleanup_results_path,
                "baseline",
                "cleanup",
                results.baseline_predictions_path,
                results.cleanup_predictions_path,
            )

        # Step 7: Generate forensics report
        if not skip_evaluation and results.baseline_results_path:
            logger.info("Step 7: Collecting forensics")
            results.forensics_path = self.config.output_dir / f"{run_id}_forensics.json"
            report = self.forensics_collector.collect_from_logs(
                f"{run_id}_baseline",
                Path("logs"),
                results.baseline_results_path,
            )
            if results.diagnostics_path:
                report = self.forensics_collector.enrich_with_diagnostics(
                    report,
                    results.diagnostics_path,
                )
            if results.baseline_predictions_path:
                report = self.forensics_collector.enrich_with_predictions(
                    report,
                    results.baseline_predictions_path,
                )
            report.save(results.forensics_path)
            results.forensics_report = report

            # Step 8: Evaluate decision gate
            logger.info("Step 8: Evaluating decision gate")
            results.decision_gate = evaluate_decision_gate(report)
            logger.info(f"Decision gate: {results.decision_gate['recommendation']}")

        # Save summary
        results.save_summary(self.config.output_dir / f"{run_id}_summary.json")

        logger.info(f"Experiment complete: {run_id}")
        self._log_summary(results)

        return results

    def _generate_cleanup_predictions(
        self,
        baseline_path: Path,
        diagnostics_path: Path,
        output_path: Path,
        diagnostics_fn: Optional[callable] = None,
    ) -> CostMetrics:
        """Generate cleanup predictions from baseline + diagnostics."""
        from datasets import load_dataset

        total_cost = CostMetrics()

        # Load dataset for instance details
        ds = load_dataset(self.config.evaluation.dataset_name, split="test")
        instances = {inst["instance_id"]: inst for inst in ds}

        # Load baseline predictions
        predictions = {}
        with open(baseline_path) as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    predictions[data["instance_id"]] = Prediction.from_dict(data)

        # Load diagnostics
        diagnostics = {}
        with open(diagnostics_path) as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    diagnostics[data["instance_id"]] = DiagnosticResult.from_dict(data)

        # Generate cleaned predictions
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            for instance_id, pred in predictions.items():
                instance = instances.get(instance_id)
                diag = diagnostics.get(instance_id)

                if instance and diag and diag.has_issues:
                    cleaned = self.patch_cleaner.process_prediction(
                        instance, pred, diag, diagnostics_fn
                    )
                    f.write(cleaned.to_json() + "\n")

                    # Track cleanup cost (delta from baseline)
                    if cleaned.cost_metrics and pred.cost_metrics:
                        cleanup_delta = CostMetrics(
                            input_tokens=cleaned.cost_metrics.input_tokens - pred.cost_metrics.input_tokens,
                            output_tokens=cleaned.cost_metrics.output_tokens - pred.cost_metrics.output_tokens,
                            total_tokens=cleaned.cost_metrics.total_tokens - pred.cost_metrics.total_tokens,
                            wall_time_seconds=cleaned.cost_metrics.wall_time_seconds - pred.cost_metrics.wall_time_seconds,
                            llm_calls=cleaned.cost_metrics.llm_calls - pred.cost_metrics.llm_calls,
                            failed_calls=cleaned.cost_metrics.failed_calls,
                        )
                        total_cost = total_cost + cleanup_delta
                else:
                    f.write(pred.to_json() + "\n")

        return total_cost

    def _log_summary(self, results: PipelineResults) -> None:
        """Log a summary of the experiment results."""
        logger.info("\n" + "=" * 60)
        logger.info("EXPERIMENT SUMMARY")
        logger.info("=" * 60)

        logger.info(f"\nCost Metrics:")
        logger.info(f"  Baseline tokens: {results.baseline_cost.total_tokens:,}")
        logger.info(f"  Cleanup tokens:  {results.cleanup_cost.total_tokens:,}")
        logger.info(f"  Total tokens:    {results.total_cost.total_tokens:,}")
        logger.info(f"  Total LLM calls: {results.total_cost.llm_calls}")
        logger.info(f"  Total wall time: {results.total_cost.wall_time_seconds:.1f}s")

        if results.comparison:
            logger.info(f"\nPass Rate:")
            logger.info(f"  Baseline: {results.comparison.baseline.pass_rate:.1%}")
            logger.info(f"  Cleanup:  {results.comparison.treatment.pass_rate:.1%}")
            logger.info(f"  Delta:    {results.comparison.net_improvement:+d} instances")

        if results.forensics_report:
            pm = results.forensics_report.predictive_metrics
            logger.info(f"\nPredictive Metrics:")
            logger.info(f"  P(fail | LSP error):    {pm.p_fail_given_error:.1%}")
            logger.info(f"  P(fail | no LSP error): {pm.p_fail_given_no_error:.1%}")
            logger.info(f"  Predictiveness ratio:   {pm.predictiveness_ratio:.2f}x")
            logger.info(f"  Ceiling:                {pm.ceiling:.1%}")

        if results.decision_gate:
            logger.info(f"\nDecision Gate:")
            logger.info(f"  {results.decision_gate['recommendation']}")

        logger.info("\n" + "=" * 60)
