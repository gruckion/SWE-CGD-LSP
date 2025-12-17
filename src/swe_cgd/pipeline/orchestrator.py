"""Full pipeline orchestrator for CGD validation experiments."""

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..baseline.generator import BaselineGenerator, Prediction
from ..cleanup.cleaner import PatchCleaner, ControlResampler
from ..diagnostics.runner import DiagnosticsRunner, DiagnosticResult
from ..evaluation.comparison import ResultsComparator, ComparisonResult
from ..evaluation.forensics import ForensicsCollector, ForensicsReport
from ..utils.config import Config
from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PipelineResults:
    """Results from a full pipeline run."""

    run_id: str
    timestamp: str
    config: dict

    baseline_predictions_path: Path
    cleanup_predictions_path: Optional[Path] = None
    control_predictions_path: Optional[Path] = None

    baseline_results_path: Optional[Path] = None
    cleanup_results_path: Optional[Path] = None
    control_results_path: Optional[Path] = None

    diagnostics_path: Optional[Path] = None
    forensics_path: Optional[Path] = None

    comparison: Optional[ComparisonResult] = None

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
                "control_predictions": str(self.control_predictions_path)
                if self.control_predictions_path
                else None,
                "baseline_results": str(self.baseline_results_path)
                if self.baseline_results_path
                else None,
                "cleanup_results": str(self.cleanup_results_path)
                if self.cleanup_results_path
                else None,
                "control_results": str(self.control_results_path)
                if self.control_results_path
                else None,
                "diagnostics": str(self.diagnostics_path) if self.diagnostics_path else None,
                "forensics": str(self.forensics_path) if self.forensics_path else None,
            },
            "comparison": self.comparison.to_dict() if self.comparison else None,
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(summary, f, indent=2)


class PipelineOrchestrator:
    """Orchestrates the full CGD validation pipeline."""

    def __init__(self, config: Config):
        self.config = config
        self.baseline_generator = BaselineGenerator(config)
        self.diagnostics_runner = DiagnosticsRunner(config)
        self.patch_cleaner = PatchCleaner(config)
        self.control_resampler = ControlResampler(config)
        self.forensics_collector = ForensicsCollector(config)
        self.results_comparator = ResultsComparator()

    def run_swebench_evaluation(
        self,
        predictions_path: Path,
        run_id: str,
    ) -> Optional[Path]:
        """Run SWE-bench evaluation using the harness.

        Returns path to results file if successful.
        """
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

            # Find results file
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
        """Generate diagnostics by running pyright inside SWE-bench containers.

        This is the key integration point - we run diagnostics
        inside the same Docker containers that SWE-bench uses.

        Args:
            predictions_path: Path to predictions JSONL
            output_path: Path to write diagnostics JSONL
            use_docker: If True, run in Docker containers; if False, run locally (for testing)
        """
        from ..diagnostics.docker_runner import DockerDiagnosticsRunner

        # Load predictions
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
                    # Run diagnostics in Docker container
                    try:
                        diag_result = docker_runner.run_in_container(instance_id, patch)
                    except Exception as e:
                        logger.error(f"Docker diagnostics failed for {instance_id}: {e}")
                        # Fall back to basic patch analysis
                        diag_result = DiagnosticResult(
                            instance_id=instance_id,
                            patch_applied=False,
                            apply_error=f"Docker error: {e}",
                        )
                else:
                    # Basic diagnostics without Docker (for testing)
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

    def run_full_experiment(
        self,
        run_id: Optional[str] = None,
        skip_evaluation: bool = False,
        include_control: bool = True,
    ) -> PipelineResults:
        """Run the complete CGD validation experiment.

        Args:
            run_id: Unique identifier for this run
            skip_evaluation: Skip SWE-bench evaluation (for testing)
            include_control: Include control experiment (resample without diagnostics)

        Returns:
            PipelineResults with all paths and comparison data
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
        self.baseline_generator.generate_all(results.baseline_predictions_path)

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

        # Step 4: Generate cleanup predictions
        logger.info("Step 4: Generating cleanup predictions")
        results.cleanup_predictions_path = self.config.preds_dir / f"{run_id}_cleanup.jsonl"
        self._generate_cleanup_predictions(
            results.baseline_predictions_path,
            results.diagnostics_path,
            results.cleanup_predictions_path,
        )

        # Step 5: Run SWE-bench evaluation on cleanup
        if not skip_evaluation:
            logger.info("Step 5: Evaluating cleanup predictions")
            results.cleanup_results_path = self.run_swebench_evaluation(
                results.cleanup_predictions_path,
                f"{run_id}_cleanup",
            )

        # Step 6 (optional): Control experiment - resample without diagnostics
        if include_control:
            logger.info("Step 6: Running control experiment (resample)")
            results.control_predictions_path = (
                self.config.preds_dir / f"{run_id}_control.jsonl"
            )
            self._generate_control_predictions(
                results.baseline_predictions_path,
                results.control_predictions_path,
            )

            if not skip_evaluation:
                results.control_results_path = self.run_swebench_evaluation(
                    results.control_predictions_path,
                    f"{run_id}_control",
                )

        # Step 7: Compare results
        if results.baseline_results_path and results.cleanup_results_path:
            logger.info("Step 7: Comparing results")
            results.comparison = self.results_comparator.compare(
                results.baseline_results_path,
                results.cleanup_results_path,
                "baseline",
                "cleanup",
            )

        # Step 8: Generate forensics report
        if not skip_evaluation:
            logger.info("Step 8: Collecting forensics")
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
            report.save(results.forensics_path)

        # Save summary
        results.save_summary(self.config.output_dir / f"{run_id}_summary.json")

        logger.info(f"Experiment complete: {run_id}")
        return results

    def _generate_cleanup_predictions(
        self,
        baseline_path: Path,
        diagnostics_path: Path,
        output_path: Path,
    ) -> None:
        """Generate cleanup predictions from baseline + diagnostics."""
        from datasets import load_dataset

        # Load dataset for instance details
        ds = load_dataset(self.config.evaluation.dataset_name, split="test")
        instances = {inst["instance_id"]: inst for inst in ds}

        # Load baseline predictions
        predictions = {}
        with open(baseline_path) as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    predictions[data["instance_id"]] = Prediction(**data)

        # Load diagnostics - properly parse all fields including errors
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
                    cleaned = self.patch_cleaner.process_prediction(instance, pred, diag)
                    f.write(cleaned.to_json() + "\n")
                else:
                    f.write(pred.to_json() + "\n")

    def _generate_control_predictions(
        self,
        baseline_path: Path,
        output_path: Path,
    ) -> None:
        """Generate control predictions (resample without diagnostics)."""
        from datasets import load_dataset

        ds = load_dataset(self.config.evaluation.dataset_name, split="test")
        instances = {inst["instance_id"]: inst for inst in ds}

        # Load baseline predictions to get instance IDs
        baseline_ids = []
        with open(baseline_path) as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    baseline_ids.append(data["instance_id"])

        # Generate new samples
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            for instance_id in baseline_ids:
                instance = instances.get(instance_id)
                if not instance:
                    continue

                try:
                    patch = self.control_resampler.resample(instance)
                    pred = Prediction(
                        instance_id=instance_id,
                        model_name_or_path=f"{self.config.llm.model}_resample",
                        model_patch=patch,
                    )
                    f.write(pred.to_json() + "\n")
                except Exception as e:
                    logger.error(f"Resample failed for {instance_id}: {e}")
                    pred = Prediction(
                        instance_id=instance_id,
                        model_name_or_path=f"{self.config.llm.model}_resample",
                        model_patch="",
                    )
                    f.write(pred.to_json() + "\n")
