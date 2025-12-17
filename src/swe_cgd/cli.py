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

    # Show cost summary
    from .baseline.generator import CostMetrics
    total_cost = CostMetrics()
    for pred in predictions:
        if pred.cost_metrics:
            total_cost = total_cost + pred.cost_metrics

    console.print(f"[green]Generated {len(predictions)} predictions -> {output}[/green]")
    console.print(f"  Total tokens: {total_cost.total_tokens:,}")
    console.print(f"  Total time: {total_cost.wall_time_seconds:.1f}s")


@app.command()
def cleanup(
    baseline_preds: Path = typer.Argument(..., help="Path to baseline predictions JSONL"),
    diagnostics_file: Path = typer.Argument(..., help="Path to diagnostics JSONL"),
    output: Path = typer.Option(Path("preds/cleanup.jsonl"), "--output", "-o"),
    model: str = typer.Option("claude-sonnet-4-20250514", "--model", "-m"),
    dataset: str = typer.Option("princeton-nlp/SWE-bench_Lite", "--dataset", "-d"),
    max_repair_iterations: int = typer.Option(3, "--max-iterations", help="Max repair iterations"),
    max_repair_tokens: int = typer.Option(50000, "--max-tokens", help="Max tokens for repair"),
):
    """Generate cleaned predictions using diagnostic feedback with repair loop."""
    from datasets import load_dataset

    from .baseline.generator import Prediction, CostMetrics
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
                predictions[data["instance_id"]] = Prediction.from_dict(data)

    # Load diagnostics
    diagnostics = {}
    with open(diagnostics_file) as f:
        for line in f:
            if line.strip():
                data = json.loads(line)
                diagnostics[data["instance_id"]] = DiagnosticResult.from_dict(data)

    # Load dataset for instance details
    ds = load_dataset(dataset, split="test")
    instances = {inst["instance_id"]: inst for inst in ds}

    cleaner = PatchCleaner(
        config,
        max_repair_iterations=max_repair_iterations,
        max_repair_tokens=max_repair_tokens,
    )

    total_cost = CostMetrics()
    cleaned_count = 0

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        for instance_id, pred in predictions.items():
            if instance_id not in diagnostics:
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

            if cleaned_pred.cost_metrics:
                total_cost = total_cost + cleaned_pred.cost_metrics
            if cleaned_pred.had_diagnostic_issues:
                cleaned_count += 1

    console.print(f"[green]Generated cleaned predictions -> {output}[/green]")
    console.print(f"  Instances with issues: {cleaned_count}")
    console.print(f"  Total tokens: {total_cost.total_tokens:,}")
    console.print(f"  Total time: {total_cost.wall_time_seconds:.1f}s")


@app.command()
def compare(
    baseline_results: Path = typer.Argument(..., help="Baseline results JSON"),
    treatment_results: Path = typer.Argument(..., help="Treatment results JSON"),
    baseline_name: str = typer.Option("baseline", "--baseline-name"),
    treatment_name: str = typer.Option("cleanup", "--treatment-name"),
    baseline_preds: Optional[Path] = typer.Option(None, "--baseline-preds", help="Baseline predictions for cost analysis"),
    treatment_preds: Optional[Path] = typer.Option(None, "--treatment-preds", help="Treatment predictions for cost analysis"),
    output: Optional[Path] = typer.Option(None, "--output", "-o"),
):
    """Compare baseline vs treatment experiment results with cost analysis."""
    from .evaluation.comparison import ResultsComparator

    comparator = ResultsComparator()
    comparison = comparator.compare(
        baseline_results,
        treatment_results,
        baseline_name,
        treatment_name,
        baseline_preds,
        treatment_preds,
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
    predictions_file: Optional[Path] = typer.Option(None, "--predictions"),
    output: Path = typer.Option(Path("output/forensics.json"), "--output", "-o"),
):
    """Collect forensics with predictive metrics and decision gate."""
    from .evaluation.forensics import ForensicsCollector, evaluate_decision_gate

    config = load_config()
    collector = ForensicsCollector(config)

    report = collector.collect_from_logs(run_id, logs_dir, results_file)

    if diagnostics_file:
        report = collector.enrich_with_diagnostics(report, diagnostics_file)

    if predictions_file:
        report = collector.enrich_with_predictions(report, predictions_file)

    console.print(report.get_summary())

    # Evaluate decision gate
    gate = evaluate_decision_gate(report)
    console.print("\n[bold]Decision Gate Evaluation[/bold]")
    console.print(f"  Gate passed: {gate['gate_passed']}")
    for name, criterion in gate['criteria'].items():
        status = "[green]✓[/green]" if criterion['passed'] else "[red]✗[/red]"
        console.print(f"  {status} {criterion['description']}: {criterion['value']:.2f}")
    console.print(f"\n[bold]Recommendation:[/bold] {gate['recommendation']}")

    # Save report with decision gate
    report_dict = report.to_dict()
    report_dict['decision_gate'] = gate

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        json.dump(report_dict, f, indent=2)
    console.print(f"\n[green]Saved forensics to {output}[/green]")


@app.command()
def run_pipeline(
    dataset: str = typer.Option("princeton-nlp/SWE-bench_Lite", "--dataset", "-d"),
    model: str = typer.Option("claude-sonnet-4-20250514", "--model", "-m"),
    max_instances: Optional[int] = typer.Option(None, "--max", "-n"),
    instance_ids: Optional[str] = typer.Option(None, "--instances", "-i"),
    max_workers: int = typer.Option(4, "--workers", "-w"),
    skip_eval: bool = typer.Option(False, "--skip-eval", help="Skip SWE-bench evaluation"),
    max_repair_iterations: int = typer.Option(3, "--max-repair-iterations", help="Max repair iterations per instance"),
    max_repair_tokens: int = typer.Option(50000, "--max-repair-tokens", help="Max tokens for repair per instance"),
    one_shot: bool = typer.Option(False, "--one-shot", help="Use one-shot cleanup (no repair loop)"),
    run_id: str = typer.Option("cgd_experiment", "--run-id"),
):
    """Run the full validation pipeline with cost tracking and decision gate.

    Steps:
    1. Generate baseline predictions
    2. Run SWE-bench evaluation on baseline
    3. Collect diagnostics (pyright, py_compile) in Docker containers
    4. Generate cleanup predictions with repair loop
    5. Run SWE-bench evaluation on cleanup
    6. Compare results with cost analysis
    7. Generate forensics report with predictive metrics
    8. Evaluate decision gate
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
    console.print(f"Repair mode: {'one-shot' if one_shot else f'loop (max {max_repair_iterations} iterations)'}")
    console.print("")

    orchestrator = PipelineOrchestrator(
        config,
        max_repair_iterations=max_repair_iterations,
        max_repair_tokens=max_repair_tokens,
    )

    try:
        results = orchestrator.run_full_experiment(
            run_id=run_id,
            skip_evaluation=skip_eval,
            use_repair_loop=not one_shot,
        )

        console.print(f"\n[bold green]Pipeline complete![/bold green]")
        console.print(f"\n[bold]Output files:[/bold]")
        console.print(f"  Baseline predictions: {results.baseline_predictions_path}")
        console.print(f"  Cleanup predictions:  {results.cleanup_predictions_path}")
        console.print(f"  Diagnostics:          {results.diagnostics_path}")

        console.print(f"\n[bold]Cost Metrics:[/bold]")
        console.print(f"  Baseline tokens: {results.baseline_cost.total_tokens:,}")
        console.print(f"  Cleanup tokens:  {results.cleanup_cost.total_tokens:,}")
        console.print(f"  Total tokens:    {results.total_cost.total_tokens:,}")
        console.print(f"  Total LLM calls: {results.total_cost.llm_calls}")

        if results.baseline_results_path:
            console.print(f"\n[bold]Evaluation results:[/bold]")
            console.print(f"  Baseline: {results.baseline_results_path}")
            console.print(f"  Cleanup:  {results.cleanup_results_path}")

        if results.comparison:
            console.print(f"\n[bold]Comparison:[/bold]")
            results.comparison.print_table()

        if results.decision_gate:
            console.print(f"\n[bold]Decision Gate:[/bold]")
            gate = results.decision_gate
            console.print(f"  Gate passed: {gate['gate_passed']}")
            console.print(f"  Recommendation: {gate['recommendation']}")

        if results.forensics_path:
            console.print(f"\n  Forensics report: {results.forensics_path}")

    except Exception as e:
        console.print(f"\n[bold red]Pipeline failed: {e}[/bold red]")
        logger.exception("Pipeline failed")
        raise typer.Exit(1)


@app.command()
def decision_gate(
    forensics_file: Path = typer.Argument(..., help="Path to forensics JSON file"),
):
    """Evaluate the decision gate from a forensics report.

    Checks whether the hypothesis is supported:
    1. LSP errors are COMMON in failures (ceiling >= 30%)
    2. LSP errors are PREDICTIVE of failure (predictiveness ratio >= 1.2x)

    Returns a recommendation on whether to proceed with deeper engineering.
    """
    from .evaluation.forensics import ForensicsReport, PredictiveMetrics, evaluate_decision_gate

    if not forensics_file.exists():
        console.print(f"[red]Forensics file not found: {forensics_file}[/red]")
        raise typer.Exit(1)

    with open(forensics_file) as f:
        data = json.load(f)

    # Reconstruct predictive metrics
    pm_data = data.get("predictive_metrics", {})
    pm = PredictiveMetrics(
        total_instances=pm_data.get("total_instances", 0),
        total_with_diagnostic_issues=pm_data.get("total_with_diagnostic_issues", 0),
        total_without_diagnostic_issues=pm_data.get("total_without_diagnostic_issues", 0),
        failed_with_diagnostic_issues=pm_data.get("failed_with_diagnostic_issues", 0),
        failed_without_diagnostic_issues=pm_data.get("failed_without_diagnostic_issues", 0),
        resolved_with_diagnostic_issues=pm_data.get("resolved_with_diagnostic_issues", 0),
        resolved_without_diagnostic_issues=pm_data.get("resolved_without_diagnostic_issues", 0),
    )

    # Create minimal report for evaluation
    from .evaluation.forensics import ForensicsReport
    report = ForensicsReport(
        run_id=data.get("run_id", "unknown"),
        timestamp=data.get("timestamp", ""),
        total_instances=data.get("total_instances", 0),
        resolved=data.get("resolved", 0),
        failed=data.get("failed", 0),
        errors=data.get("errors", 0),
        timeouts=data.get("timeouts", 0),
        predictive_metrics=pm,
    )

    gate = evaluate_decision_gate(report)

    console.print("\n[bold]Decision Gate Evaluation[/bold]")
    console.print("=" * 50)

    console.print(f"\n[bold]Predictive Metrics:[/bold]")
    console.print(f"  P(fail | LSP error):    {pm.p_fail_given_error:.1%}")
    console.print(f"  P(fail | no LSP error): {pm.p_fail_given_no_error:.1%}")
    console.print(f"  Predictiveness ratio:   {pm.predictiveness_ratio:.2f}x")
    console.print(f"  Ceiling:                {pm.ceiling:.1%}")

    console.print(f"\n[bold]Criteria:[/bold]")
    for name, criterion in gate['criteria'].items():
        status = "[green]✓ PASS[/green]" if criterion['passed'] else "[red]✗ FAIL[/red]"
        console.print(f"  {status} {criterion['description']}")
        console.print(f"         Actual: {criterion['value']:.2f}, Required: {criterion['threshold']:.2f}")

    console.print(f"\n[bold]Overall Result:[/bold]")
    if gate['gate_passed']:
        console.print("[green]GATE PASSED[/green]")
    else:
        console.print("[red]GATE FAILED[/red]")

    console.print(f"\n[bold]Recommendation:[/bold]")
    console.print(f"  {gate['recommendation']}")


if __name__ == "__main__":
    app()
