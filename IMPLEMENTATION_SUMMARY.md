# Implementation Summary

## Goal

Validate the hypothesis that **preventing LSP-detectable semantic breakage from entering the agent loop** reduces cost per solve and improves pass@budget.

The proxy doesn't make the model smarter—it stops it wasting budget on predictable breakage.

## Current Implementation Status

The codebase implements **Experiments 1 & 2** from the validation approach with full cost tracking and decision gate automation.

### What's Implemented

| Component | Purpose | Status |
|-----------|---------|--------|
| Baseline Generator | Generate patches from issue descriptions with cost tracking | ✅ |
| CostMetrics | Track tokens, time, LLM calls per operation | ✅ |
| Diagnostics Runner | Run pyright/py_compile on patches | ✅ |
| Docker Diagnostics | Run diagnostics in SWE-bench containers | ✅ |
| PatchCleaner | Iterative repair loop with token budget | ✅ |
| Predictive Metrics | P(fail\|error), ceiling, predictiveness ratio | ✅ |
| Cost Analysis | Cost per solved, cost per instance | ✅ |
| Decision Gate | Automated go/no-go evaluation | ✅ |
| Results Comparator | Compare cost + pass rate | ✅ |
| Pipeline Orchestrator | End-to-end experiment runner with cost tracking | ✅ |

### What's NOT Implemented Yet

| Component | Purpose | Status |
|-----------|---------|--------|
| Lazy Proxy | Buffer → diagnose → hidden repair loop → return clean | 🔜 |
| Budget-Constrained Eval | Fixed max tool calls / tokens | 🔜 |
| Churn Delta Metrics | Extra tool calls after first LSP error | 🔜 |
| Variance/Tail Analysis | Frequency of token blow-ups | 🔜 |

## Key Data Structures

### CostMetrics
```python
@dataclass
class CostMetrics:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    wall_time_seconds: float = 0.0
    llm_calls: int = 0
    failed_calls: int = 0
```

### Prediction (with cost tracking)
```python
@dataclass
class Prediction:
    instance_id: str
    model_name_or_path: str
    model_patch: str
    cost_metrics: Optional[CostMetrics] = None
    repair_iterations: int = 0
    had_diagnostic_issues: bool = False
```

### PredictiveMetrics (hypothesis validation)
```python
@dataclass
class PredictiveMetrics:
    # Computes:
    # - P(fail | LSP error)
    # - P(fail | no LSP error)
    # - Predictiveness ratio
    # - Ceiling (fraction of failures with errors)
```

## CLI Commands

```bash
# Generate baseline predictions with cost tracking
swe-cgd baseline --max 20

# Run diagnostics and generate cleanup predictions with repair loop
swe-cgd cleanup preds/baseline.jsonl output/diagnostics.jsonl \
    --max-iterations 3 \
    --max-tokens 50000

# Collect forensics with predictive metrics
swe-cgd forensics <run_id> --diagnostics output/diags.jsonl

# Compare results with cost analysis
swe-cgd compare baseline.json cleanup.json \
    --baseline-preds preds/baseline.jsonl \
    --treatment-preds preds/cleanup.jsonl

# Evaluate decision gate from forensics
swe-cgd decision-gate output/forensics.json

# Run full pipeline
swe-cgd run-pipeline --max 20 --max-repair-iterations 3
# Or one-shot mode:
swe-cgd run-pipeline --max 20 --one-shot
```

## Key Metrics

### Headline Metric

**Cost per solved task** = total tokens / #solved instances

### Predictive Metrics (Hypothesis Validation)

| Metric | Description |
|--------|-------------|
| P(fail \| LSP error) | Failure rate when diagnostic issues present |
| P(fail \| no LSP error) | Failure rate when no diagnostic issues |
| Predictiveness ratio | P(fail\|error) / P(fail\|no error) |
| Ceiling | Fraction of failures with detectable LSP errors |

### Cost Metrics

| Metric | Description |
|--------|-------------|
| cost_per_solved | Tokens per solved instance (headline) |
| cost_per_failed | Tokens per failed instance |
| cost_per_instance | Average tokens across all instances |
| total_llm_calls | Total LLM API calls |
| total_repair_iterations | Total repair loop iterations |

## Decision Gate

The decision gate automatically evaluates whether the hypothesis is supported:

```
Criterion 1: Ceiling >= 30%
  (LSP errors are COMMON in failures)

Criterion 2: Predictiveness ratio >= 1.2x
  (LSP errors PREDICT failure)
```

**Outcomes:**
- **PASS both**: PROCEED with lazy proxy implementation
- **PASS 1 only**: LIMITED HEADROOM - expand diagnostic coverage
- **PASS 2 only**: WEAK SIGNAL - investigate why errors don't predict failure
- **FAIL both**: DO NOT PROCEED - pivot to different approach

## Repair Loop

The PatchCleaner now supports iterative repair:

```python
cleaner = PatchCleaner(
    config,
    max_repair_iterations=3,  # Max iterations per instance
    max_repair_tokens=50000,  # Token budget for repair
)

result = cleaner.clean_patch_with_loop(
    instance,
    original_patch,
    diagnostics,
    run_diagnostics_fn=...,  # Re-run diagnostics after each fix
)
```

The loop continues until:
- No more diagnostic issues, OR
- Max iterations reached, OR
- Token budget exhausted

## Project Structure

```
SWE-CGD-LSP/
├── src/swe_cgd/
│   ├── baseline/generator.py      # CostMetrics, Prediction, BaselineGenerator
│   ├── diagnostics/
│   │   ├── runner.py              # DiagnosticResult, DiagnosticsRunner
│   │   └── docker_runner.py       # Docker-based diagnostics
│   ├── cleanup/cleaner.py         # PatchCleaner with repair loop
│   ├── evaluation/
│   │   ├── forensics.py           # PredictiveMetrics, CostAnalysis, decision gate
│   │   └── comparison.py          # Cost-aware comparison
│   ├── pipeline/orchestrator.py   # Full pipeline with cost tracking
│   ├── utils/                     # Config + logging
│   └── cli.py                     # Typer CLI with new commands
├── tests/
│   ├── test_cleanup.py            # Cleanup + cost tests
│   ├── test_diagnostics.py        # Diagnostics parsing tests
│   └── test_forensics.py          # Predictive metrics + decision gate tests
├── HYPOTHESIS.md                  # Full hypothesis documentation
├── pyproject.toml
└── README.md
```

## Next Steps

1. **Run Experiment 1+2**: Execute on 100-200 SWE-bench tasks
2. **Evaluate Decision Gate**: Does the data support the hypothesis?
3. **If PROCEED**: Implement Lazy Proxy POC (Experiment 3)
4. **If NOT PROCEED**: Pivot or expand diagnostic coverage

## Running Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## Example Output

```
Decision Gate Evaluation
==========================
Predictive Metrics:
  P(fail | LSP error):    75.0%
  P(fail | no LSP error): 45.0%
  Predictiveness ratio:   1.67x
  Ceiling:                55.0%

Criteria:
  ✓ PASS Ceiling >= 30%
         Actual: 0.55, Required: 0.30
  ✓ PASS Predictiveness ratio >= 1.2x
         Actual: 1.67, Required: 1.20

Overall Result: GATE PASSED

Recommendation:
  PROCEED: The data supports the hypothesis.
  LSP errors are common in failures and predictive of failure.
  Consider implementing the lazy proxy POC.
```
