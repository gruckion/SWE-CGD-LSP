# Implementation Summary

## Project Structure
```
SWE-CGD-LSP/
├── src/swe_cgd/
│   ├── baseline/generator.py      # LLM-based patch generation
│   ├── diagnostics/
│   │   ├── runner.py              # Pyright/py_compile runner + DiagnosticResult
│   │   └── docker_runner.py       # Docker-based diagnostics for SWE-bench
│   ├── cleanup/cleaner.py         # One-shot diagnostic cleanup
│   ├── evaluation/
│   │   ├── forensics.py           # Log analysis + SWE-bench results parsing
│   │   └── comparison.py          # A/B comparison
│   ├── pipeline/orchestrator.py   # Full pipeline runner
│   ├── utils/                     # Config + logging
│   └── cli.py                     # Typer CLI
├── scripts/
│   ├── setup_swebench.sh          # Setup script
│   ├── run_experiment.sh          # Full experiment runner
│   └── quick_test.py              # Quick local tests
├── tests/
│   ├── test_diagnostics.py        # Tests for diagnostics parsing/round-trip
│   └── test_cleanup.py            # Tests for cleanup invocation logic
├── pyproject.toml                 # Project config
└── README.md                      # Documentation
```

## Key Components

1. **Baseline Generator** (`src/swe_cgd/baseline/generator.py`): Generates patches from issue descriptions using LiteLLM. Properly handles `max_instances` with a yielded count (not dataset index).

2. **Diagnostics Runner** (`src/swe_cgd/diagnostics/runner.py`):
   - `DiagnosticIssue` and `DiagnosticResult` dataclasses with full `to_dict()`/`from_dict()` round-trip support
   - `has_issues` property correctly includes `patch_applied=False` as an issue
   - `run_py_compile()` for syntax checking (accurately named)
   - `run_pyright()` for type checking

3. **Docker Diagnostics** (`src/swe_cgd/diagnostics/docker_runner.py`):
   - Runs diagnostics inside SWE-bench Docker containers
   - Uses `git diff --name-only` (not `HEAD~1`) to get changed files from working tree
   - Handles empty file lists gracefully

4. **Cleanup Cleaner** (`src/swe_cgd/cleanup/cleaner.py`):
   - Feeds diagnostics back to LLM for one-shot repair
   - Only invokes cleanup when `diagnostics.has_issues` is True
   - Includes control experiment (resample without diagnostics)

5. **Results Comparator** (`src/swe_cgd/evaluation/comparison.py`): Compares baseline vs cleanup pass rates with detailed metrics.

6. **Forensics Collector** (`src/swe_cgd/evaluation/forensics.py`):
   - Correctly parses SWE-bench `results.json` format (`{"resolved": [...], "failed": [...]}`)
   - Builds status mapping from lists, not dict access

7. **Pipeline Orchestrator** (`src/swe_cgd/pipeline/orchestrator.py`):
   - Runs the full experiment end-to-end
   - Uses Docker diagnostics runner for real diagnostics collection
   - Properly parses diagnostics using `DiagnosticResult.from_dict()`

## CLI Commands
```bash
swe-cgd baseline --max 20          # Generate baseline predictions
swe-cgd cleanup <baseline> <diags> # Generate cleanup predictions
swe-cgd compare <base> <treat>     # Compare results
swe-cgd forensics <run_id>         # Collect forensics from logs
swe-cgd run-pipeline --max 20      # Run full pipeline
```

## Key Fixes Applied

1. **Diagnostics properly parsed**: Both CLI and pipeline now use `DiagnosticResult.from_dict()` to fully parse diagnostics including `syntax_errors` and `type_errors` lists.

2. **`has_issues` includes patch failures**: `DiagnosticResult.has_issues` now returns `True` when `patch_applied=False`, ensuring cleanup is triggered for failed patches.

3. **Docker script fixed**: Changed from `git diff --name-only HEAD~1` to `git diff --name-only` to correctly identify changed files in the working tree after `git apply`.

4. **`max_instances` logic fixed**: `BaselineGenerator.get_instances()` now tracks yielded count separately from dataset index, so filtering by `instance_ids` works correctly with `max_instances`.

5. **Forensics results mapping fixed**: `ForensicsCollector` correctly parses SWE-bench's list-based results format and builds a proper status mapping.

6. **Removed broken container reuse**: The Docker diagnostics runner no longer supports the broken container reuse path.

## Running Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v
```

## Next Steps

1. **Setup**: Run `./scripts/setup_swebench.sh` to install SWE-bench
2. **Validate**: Test with gold patch on `sympy__sympy-20590`
3. **Run experiment**: Use the CLI or orchestrator

## Hypothesis Testing

The framework tests whether diagnostic-guided cleanup improves SWE-bench performance:

- **Baseline**: Single-shot patch generation
- **Cleanup**: Baseline + one diagnostic-guided repair pass (triggered by `has_issues`)
- **Control**: Two independent samples (matched compute comparison)

Key metrics:
- **Improvement Rate**: % of baseline failures fixed by cleanup
- **Regression Rate**: % of baseline passes broken by cleanup
- **Net Improvement**: Cleanup resolved - Baseline resolved
