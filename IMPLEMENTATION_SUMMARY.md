# Implementation Summary

## Goal

Validate the hypothesis that **preventing LSP-detectable semantic breakage from entering the agent loop** reduces cost per solve and improves pass@budget.

The proxy doesn't make the model smarter—it stops it wasting budget on predictable breakage.

## Current Implementation Status

The current codebase implements **Experiment 2: Counterfactual One-Shot Cleanup** from the validation approach. This is the minimal test to determine if fixing LSP errors rescues outcomes.

### What's Implemented

| Component | Purpose | Status |
|-----------|---------|--------|
| Baseline Generator | Generate patches from issue descriptions | ✅ |
| Diagnostics Runner | Run pyright/py_compile on patches | ✅ |
| Docker Diagnostics | Run diagnostics in SWE-bench containers | ✅ |
| Cleanup Cleaner | One-shot repair using diagnostic feedback | ✅ |
| Forensics Collector | Analyze predictiveness of early errors | ✅ |
| Results Comparator | Compare cost + pass rate | ✅ |
| Pipeline Orchestrator | End-to-end experiment runner | ✅ |

### What's NOT Implemented Yet

| Component | Purpose | Status |
|-----------|---------|--------|
| Lazy Proxy | Buffer → diagnose → hidden repair loop | 🔜 |
| Cost Tracking | Tokens, tool calls, wall time per task | 🔜 |
| Budget-Constrained Eval | Fixed max tool calls / tokens | 🔜 |
| Churn Delta Metrics | Extra tool calls after first LSP error | 🔜 |

## Project Structure

```
SWE-CGD-LSP/
├── src/swe_cgd/
│   ├── baseline/generator.py      # LLM-based patch generation
│   ├── diagnostics/
│   │   ├── runner.py              # Pyright/py_compile + DiagnosticResult
│   │   └── docker_runner.py       # Docker-based diagnostics for SWE-bench
│   ├── cleanup/cleaner.py         # One-shot diagnostic cleanup
│   ├── evaluation/
│   │   ├── forensics.py           # Predictiveness analysis
│   │   └── comparison.py          # Cost + pass rate comparison
│   ├── pipeline/orchestrator.py   # Full pipeline runner
│   ├── utils/                     # Config + logging
│   └── cli.py                     # Typer CLI
├── scripts/
│   ├── setup_swebench.sh          # Setup script
│   ├── run_experiment.sh          # Full experiment runner
│   └── quick_test.py              # Quick local tests
├── tests/
│   ├── test_diagnostics.py        # Tests for diagnostics parsing
│   └── test_cleanup.py            # Tests for cleanup invocation
├── HYPOTHESIS.md                  # Full hypothesis documentation
├── pyproject.toml                 # Project config
└── README.md                      # Documentation
```

## Key Components

### 1. Baseline Generator (`src/swe_cgd/baseline/generator.py`)

Generates patches from issue descriptions using LiteLLM.

- Handles `max_instances` with yielded count (not dataset index)
- Outputs SWE-bench compatible JSONL

### 2. Diagnostics Runner (`src/swe_cgd/diagnostics/runner.py`)

Runs static analysis to detect workspace consistency violations:

- `DiagnosticIssue` / `DiagnosticResult` dataclasses with full serialization
- `has_issues` property includes `patch_applied=False` as an issue
- `run_py_compile()` for syntax checking
- `run_pyright()` for type/semantic errors

**What it catches:**
- Undefined symbols
- Signature mismatches
- Type errors
- Wrong imports
- Wrong member access

### 3. Docker Diagnostics (`src/swe_cgd/diagnostics/docker_runner.py`)

Runs diagnostics inside SWE-bench Docker containers:

- Uses `git diff --name-only` to get changed files
- Handles empty file lists gracefully
- Matches the actual workspace environment

### 4. Cleanup Cleaner (`src/swe_cgd/cleanup/cleaner.py`)

One-shot repair using diagnostic feedback:

- Feeds diagnostics back to LLM
- Only invokes when `diagnostics.has_issues` is True
- Includes control experiment (resample without diagnostics)

### 5. Forensics Collector (`src/swe_cgd/evaluation/forensics.py`)

Analyzes predictiveness of early LSP errors:

- Parses SWE-bench `results.json` format
- Computes P(fail | early LSP error) vs P(fail | no early LSP error)
- Identifies ceiling (fraction of failures with detectable errors)

### 6. Results Comparator (`src/swe_cgd/evaluation/comparison.py`)

Compares baseline vs cleanup:

- Pass rate comparison
- Cost metrics (when implemented)
- Improvement/regression rates

### 7. Pipeline Orchestrator (`src/swe_cgd/pipeline/orchestrator.py`)

Runs the full experiment:

- Uses Docker diagnostics for real workspace parity
- Parses diagnostics using `DiagnosticResult.from_dict()`
- Orchestrates baseline → diagnostics → cleanup → evaluation

## CLI Commands

```bash
swe-cgd baseline --max 20          # Generate baseline predictions
swe-cgd cleanup <baseline> <diags> # Generate cleanup predictions
swe-cgd forensics <run_id>         # Collect predictiveness metrics
swe-cgd compare <base> <treat>     # Compare cost + pass rate
swe-cgd run-pipeline --max 20      # Run full pipeline
```

## Key Fixes Applied

1. **Diagnostics properly parsed**: Both CLI and pipeline use `DiagnosticResult.from_dict()` for full parsing
2. **`has_issues` includes patch failures**: Returns `True` when `patch_applied=False`
3. **Docker script fixed**: Uses `git diff --name-only` (not `HEAD~1`) for working tree changes
4. **`max_instances` logic fixed**: Tracks yielded count separately from dataset index
5. **Forensics results mapping fixed**: Correctly parses SWE-bench's list-based results format

## Validation Experiments

### Experiment 1: Baseline Forensics ✅ (Implemented)

*Is "early LSP error" predictive of failure/cost?*

```bash
swe-cgd forensics <run_id> --logs-dir logs
```

Computes:
- P(fail | early LSP error) vs P(fail | no early LSP error)
- Churn delta (extra tool calls after first error) — *needs cost tracking*
- Ceiling (fraction of failures with early persistent errors)

### Experiment 2: One-Shot Cleanup ✅ (Implemented)

*Does fixing LSP errors actually rescue outcomes?*

```bash
swe-cgd run-pipeline --max 20
```

- Generates baseline patch
- Runs diagnostics
- If errors, one follow-up LLM call to repair
- Compares pass rates

**Interpretation:**
- Pass rate barely moves → thesis weakens
- Pass rate moves materially → real headroom exists

### Experiment 3: Lazy Proxy POC 🔜 (Not Yet Implemented)

*Does preventing broken intermediate states reduce churn?*

Would implement:
- Buffer model output fully
- Apply to workspace
- Run LSP diagnostics
- Hidden repair loop until clean (or budget exhausted)
- Only return clean output to outer agent

## Decision Gate

If experiments cannot show:

1. Early LSP Error is **common** in failures, AND
2. Eliminating it often **rescues the run or reduces churn**

...then the thesis is not strong enough to justify deeper engineering (lazy proxy, token-level CGD, etc.).

## Next Steps

1. **Run Experiment 1+2**: Execute on 100-200 SWE-bench tasks
2. **Add Cost Tracking**: Tokens, tool calls, wall time per task
3. **Evaluate Decision Gate**: Does the data support the hypothesis?
4. **If yes**: Implement Lazy Proxy POC (Experiment 3)
5. **If no**: Pivot or abandon

## Running Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```
