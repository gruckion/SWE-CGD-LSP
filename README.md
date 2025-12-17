# SWE-CGD-LSP: Code Generation with Diagnostics Validation

A validation framework for testing the hypothesis that **diagnostic-guided cleanup** (LSP/type-checker feedback) improves code generation success rates on SWE-bench.

## Hypothesis

> A one-shot diagnostic cleanup pass (feeding type/syntax errors back to the LLM) is a more effective use of compute than generating independent samples.

This framework tests this by comparing:
- **Baseline**: Single-shot patch generation
- **Cleanup**: Baseline + one diagnostic-guided repair pass
- **Control**: Two independent samples (for matched compute comparison)

## Quick Start

### Prerequisites

- Python 3.10+
- Docker (for SWE-bench evaluation)
- ~120GB free disk space (for Docker images)
- API key (Anthropic or OpenAI)

### Installation

```bash
# Clone and setup
git clone <repo-url>
cd SWE-CGD-LSP

# Run setup script
chmod +x scripts/setup_swebench.sh
./scripts/setup_swebench.sh

# Or manual setup:
python -m venv .venv
source .venv/bin/activate
pip install -e .

# Clone SWE-bench (if not using setup script)
git clone https://github.com/SWE-bench/SWE-bench.git ../SWE-bench
pip install -e ../SWE-bench
```

### Set API Key

```bash
export ANTHROPIC_API_KEY=your-key
# or
export OPENAI_API_KEY=your-key
```

### Validate SWE-bench Setup

```bash
# Run gold patch test (proves Docker + harness works)
python -m swebench.harness.run_evaluation \
  --predictions_path gold \
  --max_workers 1 \
  --instance_ids sympy__sympy-20590 \
  --run_id validate-gold
```

## Usage

### CLI Commands

```bash
# Generate baseline predictions
swe-cgd baseline --max 20 --output preds/baseline.jsonl

# Generate cleanup predictions (after running diagnostics)
swe-cgd cleanup preds/baseline.jsonl output/diagnostics.jsonl

# Compare results
swe-cgd compare evaluation_results/baseline/results.json \
                evaluation_results/cleanup/results.json

# Collect forensics from evaluation logs
swe-cgd forensics run_id --logs-dir logs --output output/forensics.json

# Run full pipeline (orchestrated)
swe-cgd run-pipeline --max 20 --skip-eval  # Skip eval for dry run
```

### Full Experiment

```bash
# Set configuration
export DATASET="princeton-nlp/SWE-bench_Lite"
export MODEL="claude-sonnet-4-20250514"
export MAX_INSTANCES=20

# Run experiment
./scripts/run_experiment.sh
```

## Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    CGD Validation Pipeline                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. Baseline Generation                                          │
│     └─> LLM generates patch from issue description               │
│                                                                  │
│  2. SWE-bench Evaluation (baseline)                              │
│     └─> Run tests in Docker containers                           │
│                                                                  │
│  3. Diagnostics Collection                                       │
│     ├─> Apply patch in container                                 │
│     ├─> Run pyright (type checker)                               │
│     └─> Run compileall (syntax check)                            │
│                                                                  │
│  4. Cleanup Generation                                           │
│     └─> LLM repairs patch using diagnostic feedback              │
│                                                                  │
│  5. SWE-bench Evaluation (cleanup)                               │
│     └─> Run tests on cleaned patches                             │
│                                                                  │
│  6. Results Comparison                                           │
│     ├─> Baseline pass rate vs Cleanup pass rate                  │
│     ├─> Improved instances (failed→resolved)                     │
│     └─> Regressed instances (resolved→failed)                    │
│                                                                  │
│  7. Forensics Report                                             │
│     ├─> Which failures had detectable diagnostics?               │
│     └─> Correlation analysis                                     │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Project Structure

```
SWE-CGD-LSP/
├── src/swe_cgd/
│   ├── baseline/           # Baseline prediction generation
│   │   └── generator.py    # LLM-based patch generator
│   ├── diagnostics/        # Static analysis / type checking
│   │   ├── runner.py       # Local pyright runner
│   │   └── docker_runner.py # Docker-based diagnostics
│   ├── cleanup/            # Diagnostic-guided repair
│   │   └── cleaner.py      # One-shot cleanup logic
│   ├── evaluation/         # Results analysis
│   │   ├── forensics.py    # Log analysis
│   │   └── comparison.py   # A/B comparison
│   ├── pipeline/           # Orchestration
│   │   └── orchestrator.py # Full pipeline runner
│   ├── utils/              # Shared utilities
│   │   ├── config.py       # Configuration
│   │   └── logging.py      # Rich logging
│   └── cli.py              # Typer CLI
├── scripts/
│   ├── setup_swebench.sh   # Setup script
│   ├── run_experiment.sh   # Full experiment runner
│   └── quick_test.py       # Quick local tests
├── preds/                  # Prediction JSONL files
├── output/                 # Analysis outputs
├── logs/                   # SWE-bench logs
└── pyproject.toml          # Project configuration
```

## Configuration

Environment variables:
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`: LLM API key
- `SWE_CGD_MODEL`: Model name (default: `claude-sonnet-4-20250514`)
- `SWE_CGD_DATASET`: Dataset (default: `princeton-nlp/SWE-bench_Lite`)

## Datasets

| Dataset | Description | Recommended For |
|---------|-------------|-----------------|
| `princeton-nlp/SWE-bench_Lite` | 300 instances, faster | Initial validation |
| `princeton-nlp/SWE-bench` | Full 2,294 instances | Complete experiments |
| `princeton-nlp/SWE-bench_Verified` | Curated "solvable" subset | Quality analysis |

## Key Metrics

1. **Pass Rate**: % of instances where tests pass
2. **Improvement Rate**: % of baseline failures fixed by cleanup
3. **Regression Rate**: % of baseline passes broken by cleanup
4. **Diagnostic Coverage**: % of failures that had detectable type/syntax errors

## Interpreting Results

The hypothesis is validated if:
1. Cleanup pass rate > Baseline pass rate
2. Improvement rate > Regression rate
3. Many failures had detectable diagnostics (coverage)

Strong validation if:
- Cleanup outperforms Control (matched compute comparison)
- Improvement correlates with diagnostic signal presence

## Extending to Other Languages

The framework is designed to support multilingual diagnostics:

| Language | Diagnostics Tool | Status |
|----------|------------------|--------|
| Python | pyright | Implemented |
| TypeScript | tsserver | Planned |
| Go | gopls | Planned |
| Rust | rust-analyzer | Planned |

For multilingual SWE-bench, use the multilingual dataset variant.

## Troubleshooting

### Docker Issues
```bash
# Check Docker is running
docker run --rm hello-world

# Check disk space (need ~120GB)
df -h
```

### SWE-bench Import Errors
```bash
# Reinstall SWE-bench
pip install -e ../SWE-bench
```

### API Rate Limits
The pipeline uses exponential backoff for retries. For large runs, consider:
- Using batch API endpoints
- Running overnight
- Reducing `--max-workers`

## References

- [SWE-bench Paper](https://arxiv.org/abs/2310.06770)
- [SWE-bench Repository](https://github.com/SWE-bench/SWE-bench)
- [Pyright](https://github.com/microsoft/pyright)

## License

MIT
