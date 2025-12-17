"""CLI interface for SWE-CGD validation pipeline."""

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from .utils.config import Config, load_config
from .utils.logging import setup_logging, get_logger

app = typer.Typer(
    name="swe-cgd",
    help="SWE-bench Code Generation with Diagnostics (CGD) validation pipeline",
)
console = Console()
logger = get_logger(__name__)


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """SWE-CGD: Validate diagnostic-guided cleanup for SWE-bench."""
    import logging

    setup_logging(level=logging.DEBUG if verbose else logging.INFO)


@app.command()
def baseline(
    output: Path = typer.Option(Path("preds/baseline.jsonl"), "--output", "-o"),
    model: str = typer.Option("claude-sonnet-4-20250514", "--model", "-m"),
    dataset: str = typer.Option("princeton-nlp/SWE-bench_Lite", "--dataset", "-d"),
    max_instances: Optional[int] = typer.Option(None, "--max", "-n"),
    instance_ids: Optional[str] = typer.Option(None, "--instances", "-i", help="Comma-separated"),
):
    """Generate baseline predictions for SWE-bench instances."""
    from .baseline.generator import BaselineGenerator

    config = load_config()
    config.llm.model = model
    config.evaluation.dataset_name = dataset
    config.max_instances = max_instances

    if instance_ids:
        config.instance_ids = [i.strip() for i in instance_ids.split(",")]

    generator = BaselineGenerator(config)
    predictions = generator.generate_all(output_path=output)

    console.print(f"[green]Generated {len(predictions)} predictions -> {output}[/green]")


@app.command()
def cleanup(
    baseline_preds: Path = typer.Argument(..., help="Path to baseline predictions JSONL"),
    diagnostics_file: Path = typer.Argument(..., help="Path to diagnostics JSONL"),
    output: Path = typer.Option(Path("preds/cleanup_oneshot.jsonl"), "--output", "-o"),
    model: str = typer.Option("claude-sonnet-4-20250514", "--model", "-m"),
    dataset: str = typer.Option("princeton-nlp/SWE-bench_Lite", "--dataset", "-d"),
):
    """Generate cleaned predictions using diagnostic feedback."""
    from datasets import load_dataset

    from .baseline.generator import Prediction
    from .cleanup.cleaner import PatchCleaner
    from .diagnostics.runner import DiagnosticResult

    config = load_config()
    config.llm.model = model
    config.evaluation.dataset_name = dataset

    # Load baseline predictions
    predictions = {}
    with open(baseline_preds) as f:
        for line in f:
            if line.strip():
                data = json.loads(line)
                predictions[data["instance_id"]] = Prediction(**data)

    # Load diagnostics - properly parse all fields including errors
    diagnostics = {}
    with open(diagnostics_file) as f:
        for line in f:
            if line.strip():
                data = json.loads(line)
                diagnostics[data["instance_id"]] = DiagnosticResult.from_dict(data)

    # Load dataset for instance details
    ds = load_dataset(dataset, split="test")
    instances = {inst["instance_id"]: inst for inst in ds}

    cleaner = PatchCleaner(config)

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        for instance_id, pred in predictions.items():
            if instance_id not in diagnostics:
                # No diagnostics, keep original
                f.write(pred.to_json() + "\n")
                continue

            instance = instances.get(instance_id)
            if not instance:
                f.write(pred.to_json() + "\n")
                continue

            diag = diagnostics[instance_id]
            cleaned_pred = cleaner.process_prediction(instance, pred, diag)
            f.write(cleaned_pred.to_json() + "\n")
            f.flush()

    console.print(f"[green]Generated cleaned predictions -> {output}[/green]")


@app.command()
def compare(
    baseline_results: Path = typer.Argument(..., help="Baseline results JSON"),
    treatment_results: Path = typer.Argument(..., help="Treatment results JSON"),
    baseline_name: str = typer.Option("baseline", "--baseline-name"),
    treatment_name: str = typer.Option("cleanup", "--treatment-name"),
    output: Optional[Path] = typer.Option(None, "--output", "-o"),
):
    """Compare baseline vs treatment experiment results."""
    from .evaluation.comparison import ResultsComparator

    comparator = ResultsComparator()
    comparison = comparator.compare(
        baseline_results,
        treatment_results,
        baseline_name,
        treatment_name,
    )

    comparison.print_table()
    console.print(comparison.get_summary())

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w") as f:
            json.dump(comparison.to_dict(), f, indent=2)
        console.print(f"[green]Saved comparison to {output}[/green]")


@app.command()
def forensics(
    run_id: str = typer.Argument(..., help="SWE-bench run ID"),
    logs_dir: Path = typer.Option(Path("logs"), "--logs-dir"),
    results_file: Optional[Path] = typer.Option(None, "--results"),
    diagnostics_file: Optional[Path] = typer.Option(None, "--diagnostics"),
    output: Path = typer.Option(Path("output/forensics.json"), "--output", "-o"),
):
    """Collect forensics from evaluation logs."""
    from .evaluation.forensics import ForensicsCollector

    config = load_config()
    collector = ForensicsCollector(config)

    report = collector.collect_from_logs(run_id, logs_dir, results_file)

    if diagnostics_file:
        report = collector.enrich_with_diagnostics(report, diagnostics_file)

    console.print(report.get_summary())
    report.save(output)


@app.command()
def run_pipeline(
    dataset: str = typer.Option("princeton-nlp/SWE-bench_Lite", "--dataset", "-d"),
    model: str = typer.Option("claude-sonnet-4-20250514", "--model", "-m"),
    max_instances: Optional[int] = typer.Option(None, "--max", "-n"),
    instance_ids: Optional[str] = typer.Option(None, "--instances", "-i"),
    max_workers: int = typer.Option(4, "--workers", "-w"),
    skip_eval: bool = typer.Option(False, "--skip-eval", help="Skip SWE-bench evaluation"),
    include_control: bool = typer.Option(True, "--control/--no-control", help="Include control experiment"),
    run_id: str = typer.Option("cgd_experiment", "--run-id"),
):
    """Run the full validation pipeline.

    Steps:
    1. Generate baseline predictions
    2. Run SWE-bench evaluation on baseline
    3. Collect diagnostics (pyright, py_compile) in Docker containers
    4. Generate cleanup predictions using diagnostic feedback
    5. Run SWE-bench evaluation on cleanup
    6. (Optional) Run control experiment (resample without diagnostics)
    7. Compare results and generate forensics report
    """
    from .pipeline.orchestrator import PipelineOrchestrator

    config = load_config()
    config.llm.model = model
    config.evaluation.dataset_name = dataset
    config.evaluation.max_workers = max_workers
    config.max_instances = max_instances

    if instance_ids:
        config.instance_ids = [i.strip() for i in instance_ids.split(",")]

    console.print(f"[bold blue]Starting CGD validation pipeline[/bold blue]")
    console.print(f"Run ID: {run_id}")
    console.print(f"Dataset: {dataset}")
    console.print(f"Model: {model}")
    console.print(f"Max instances: {max_instances or 'all'}")
    console.print(f"Skip evaluation: {skip_eval}")
    console.print(f"Include control: {include_control}")
    console.print("")

    orchestrator = PipelineOrchestrator(config)

    try:
        results = orchestrator.run_full_experiment(
            run_id=run_id,
            skip_evaluation=skip_eval,
            include_control=include_control,
        )

        console.print(f"\n[bold green]Pipeline complete![/bold green]")
        console.print(f"\n[bold]Output files:[/bold]")
        console.print(f"  Baseline predictions: {results.baseline_predictions_path}")
        console.print(f"  Cleanup predictions:  {results.cleanup_predictions_path}")
        if results.control_predictions_path:
            console.print(f"  Control predictions:  {results.control_predictions_path}")
        console.print(f"  Diagnostics:          {results.diagnostics_path}")

        if results.baseline_results_path:
            console.print(f"\n[bold]Evaluation results:[/bold]")
            console.print(f"  Baseline: {results.baseline_results_path}")
            console.print(f"  Cleanup:  {results.cleanup_results_path}")
            if results.control_results_path:
                console.print(f"  Control:  {results.control_results_path}")

        if results.comparison:
            console.print(f"\n[bold]Comparison:[/bold]")
            results.comparison.print_table()
            console.print(results.comparison.get_summary())

        if results.forensics_path:
            console.print(f"\n  Forensics report: {results.forensics_path}")

    except Exception as e:
        console.print(f"\n[bold red]Pipeline failed: {e}[/bold red]")
        logger.exception("Pipeline failed")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
