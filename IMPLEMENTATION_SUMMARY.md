# Implementation Summary

## Project Structure
```
SWE-CGD-LSP/
├── src/swe_cgd/
│   ├── baseline/generator.py      # LLM-based patch generation
│   ├── diagnostics/
│   │   ├── runner.py              # Pyright/compileall runner
│   │   └── docker_runner.py       # Docker-based diagnostics
│   ├── cleanup/cleaner.py         # One-shot diagnostic cleanup
│   ├── evaluation/
│   │   ├── forensics.py           # Log analysis
│   │   └── comparison.py          # A/B comparison
│   ├── pipeline/orchestrator.py   # Full pipeline runner
│   ├── utils/                     # Config + logging
│   └── cli.py                     # Typer CLI
├── scripts/
│   ├── setup_swebench.sh          # Setup script
│   ├── run_experiment.sh          # Full experiment runner
│   └── quick_test.py              # Quick local tests
├── tests/test_diagnostics.py      # Unit tests
├── pyproject.toml                 # Project config
└── README.md                      # Documentation
```

## Key Components

1. **Baseline Generator** (`src/swe_cgd/baseline/generator.py:48`): Generates patches from issue descriptions using LiteLLM

2. **Diagnostics Runner** (`src/swe_cgd/diagnostics/runner.py:95`): Runs pyright + compileall on patched code

3. **Cleanup Cleaner** (`src/swe_cgd/cleanup/cleaner.py:55`): Feeds diagnostics back to LLM for one-shot repair

4. **Results Comparator** (`src/swe_cgd/evaluation/comparison.py:73`): Compares baseline vs cleanup pass rates

5. **Pipeline Orchestrator** (`src/swe_cgd/pipeline/orchestrator.py:48`): Runs the full experiment end-to-end

## CLI Commands
```bash
swe-cgd baseline --max 20          # Generate baseline predictions
swe-cgd cleanup <baseline> <diags> # Generate cleanup predictions
swe-cgd compare <base> <treat>     # Compare results
swe-cgd run-pipeline --max 20      # Run full pipeline
```

## Next Steps

1. **Setup**: Run `./scripts/setup_swebench.sh` to install SWE-bench
2. **Validate**: Test with gold patch on `sympy__sympy-20590`
3. **Run experiment**: Use `./scripts/run_experiment.sh` or the CLI

## Hypothesis Testing

The framework is designed to test the hypothesis that diagnostic-guided cleanup improves SWE-bench performance:

- **Baseline**: Single-shot patch generation
- **Cleanup**: Baseline + one diagnostic-guided repair pass
- **Control**: Two independent samples (matched compute comparison)

The comparison metrics will show if diagnostic-guided cleanup improves pass rate more effectively than independent resampling.
