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

    # Load diagnostics
    diagnostics = {}
    with open(diagnostics_file) as f:
        for line in f:
            if line.strip():
                data = json.loads(line)
                diagnostics[data["instance_id"]] = DiagnosticResult(
                    instance_id=data["instance_id"],
                    patch_applied=data.get("patch_applied", False),
                    apply_error=data.get("apply_error"),
                    syntax_errors=[],
                    type_errors=[],
                )

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
    skip_baseline: bool = typer.Option(False, "--skip-baseline"),
    skip_eval: bool = typer.Option(False, "--skip-eval", help="Skip SWE-bench evaluation"),
    run_id: str = typer.Option("cgd_experiment", "--run-id"),
):
    """Run the full validation pipeline.

    Steps:
    1. Generate baseline predictions
    2. Run SWE-bench evaluation on baseline
    3. Collect diagnostics on failed instances
    4. Generate cleanup predictions
    5. Run SWE-bench evaluation on cleanup
    6. Compare results
    """
    from datetime import datetime

    config = load_config()
    config.llm.model = model
    config.evaluation.dataset_name = dataset
    config.evaluation.max_workers = max_workers
    config.max_instances = max_instances

    if instance_ids:
        config.instance_ids = [i.strip() for i in instance_ids.split(",")]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"{run_id}_{timestamp}"

    console.print(f"[bold blue]Starting CGD validation pipeline[/bold blue]")
    console.print(f"Run ID: {run_id}")
    console.print(f"Dataset: {dataset}")
    console.print(f"Model: {model}")

    # Step 1: Generate baseline predictions
    baseline_path = config.preds_dir / f"baseline_{run_id}.jsonl"
    if not skip_baseline:
        console.print("\n[bold]Step 1: Generating baseline predictions...[/bold]")
        from .baseline.generator import BaselineGenerator

        generator = BaselineGenerator(config)
        generator.generate_all(output_path=baseline_path)
    else:
        console.print("\n[bold]Step 1: Skipping baseline (using existing)[/bold]")

    console.print(f"\n[green]Baseline predictions: {baseline_path}[/green]")

    # Note: Steps 2-6 require running SWE-bench evaluation
    # which needs Docker and the swebench harness
    if skip_eval:
        console.print("\n[yellow]Skipping evaluation steps (--skip-eval)[/yellow]")
        console.print("\nTo run full evaluation, use:")
        console.print(f"  python -m swebench.harness.run_evaluation \\")
        console.print(f"    --predictions_path {baseline_path} \\")
        console.print(f"    --dataset_name {dataset} \\")
        console.print(f"    --max_workers {max_workers} \\")
        console.print(f"    --run_id {run_id}_baseline")
    else:
        console.print("\n[bold]Step 2: Running SWE-bench evaluation...[/bold]")
        console.print("[yellow]Note: This requires swebench to be installed separately[/yellow]")
        # The actual evaluation would be run via:
        # python -m swebench.harness.run_evaluation ...

    console.print(f"\n[bold green]Pipeline setup complete![/bold green]")


if __name__ == "__main__":
    app()
