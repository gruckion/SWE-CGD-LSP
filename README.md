# SWE-CGD-LSP: Preventing Workspace Consistency Violations in Agentic Code Editing

A validation framework for testing the hypothesis that **preventing LSP-detectable semantic breakage from entering the agent loop** reduces cost per solve and improves pass@budget in agentic code editing.

## Hypothesis

> **Agentic editing pipelines waste budget repairing predictable, workspace-detectable semantic breakage after the model has already conditioned on it.**

A material share of failures and cost blow-ups in instruction-based code editing are driven by early, non-transient "workspace consistency" violations—undefined symbols, signature mismatches, type errors, wrong imports, wrong member access—that are detectable via LSP/static tooling.

If these violations are **prevented from reaching the outer agent loop** (or repaired before the agent continues), then:

1. **pass@budget increases** — fewer runs die in repair loops before timeout
2. **cost per solve decreases** — fewer tool calls, fewer iterations, less context churn

See [HYPOTHESIS.md](./HYPOTHESIS.md) for the full hypothesis and validation approach.

## Core Insight

The proxy doesn't make the model smarter. It **stops it wasting budget on predictable breakage**.

### Why This Matters

- **Budgets are real** — Agents have max steps, tokens, timeouts. Runs that "would eventually fix it" fail by exhaustion.
- **Context pollution degrades decisions** — Diagnostic dumps, partial patches, contradictory hypotheses push the model into worse action selection later.
- **Every tool call is a failure opportunity** — Flaky tests, environment quirks, rate limits. Fewer calls = higher mechanical reliability.

## Validation Approach

This framework implements three experiments to get a go/no-go signal:

### 1. Baseline Forensics
*Is "early LSP error" predictive of failure/cost?*

- Run baseline agent on SWE-bench tasks
- Log when first LSP Error appears, whether it persists, total tool calls
- Compute: P(fail | early LSP error) vs P(fail | no early LSP error)
- Measure churn delta and ceiling (fraction of failures with early persistent errors)

### 2. Counterfactual One-Shot Cleanup
*Does fixing LSP errors actually rescue outcomes?*

- Let baseline agent produce patch
- Apply patch, run LSP diagnostics
- If errors, one follow-up call to eliminate diagnostics
- Re-run tests
- If pass rate barely moves → thesis weakens. If it moves materially → real headroom.

### 3. Lazy Proxy POC
*Does preventing broken intermediate states reduce churn?*

- Buffer model output fully
- Apply it to workspace
- Run LSP diagnostics
- If violations exceed threshold, trigger hidden repair loop
- Only return clean output to outer agent

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
```

### Set API Key

```bash
export ANTHROPIC_API_KEY=your-key
# or
export OPENAI_API_KEY=your-key
```

## Usage

### CLI Commands

```bash
# Generate baseline predictions
swe-cgd baseline --max 20 --output preds/baseline.jsonl

# Run diagnostics and generate cleanup predictions
swe-cgd cleanup preds/baseline.jsonl output/diagnostics.jsonl

# Collect forensics (predictiveness analysis)
swe-cgd forensics run_id --logs-dir logs --output output/forensics.json

# Compare results (cost + pass rate)
swe-cgd compare evaluation_results/baseline/results.json \
                evaluation_results/cleanup/results.json

# Run full pipeline
swe-cgd run-pipeline --max 20
```

### Full Experiment

```bash
./scripts/run_experiment.sh
```

## Key Metrics

### Headline Metric

**Cost per solved task** = (tokens + tool calls + wall time) / #solved

### Secondary Metrics

| Metric | Description |
|--------|-------------|
| pass@budget | Pass rate under fixed budget constraints |
| Tool-call count | Total tool invocations |
| Failed-tool-call count | Tool calls that errored |
| Churn delta | Extra tool calls after first LSP error |
| Variance / tail risk | Frequency of token blow-ups |

### Forensics Metrics

| Metric | Description |
|--------|-------------|
| P(fail \| early LSP error) | Failure rate when early error present |
| P(fail \| no early LSP error) | Failure rate when no early error |
| Ceiling | Fraction of failures with early persistent errors |

## Evaluation Regimes

### 1. Unbounded / Generous Budget
- Expect: pass rate may be similar
- Measure: cost per solve, tool calls, variance

### 2. Fixed-Budget (What Matters Economically)
- Cap max tool calls / wall time / tokens
- Expect: if proxy provides value, pass@budget increases

## Two Possible Outcomes

### Hypothesis A: Cost Reduction Only (Still Valuable)
- Pass rate stays ~flat
- Cost per solve drops significantly
- Strong product wedge if delta is large

### Hypothesis B: Cost Reduction + Pass@Budget Improvement
- Pass rate improves under fixed budgets
- Cost per solve also drops
- The stronger, more defensible story

## Decision Gate

If baseline forensics + one-shot cleanup cannot show:

1. Early LSP Error is **common** in failures, AND
2. Eliminating it often **rescues the run or reduces churn**

...then the thesis is not strong enough to justify deeper engineering.

## Project Structure

```
SWE-CGD-LSP/
├── src/swe_cgd/
│   ├── baseline/           # Baseline prediction generation
│   ├── diagnostics/        # LSP / static analysis
│   │   ├── runner.py       # Local diagnostics runner
│   │   └── docker_runner.py # Docker-based diagnostics
│   ├── cleanup/            # Diagnostic-guided repair
│   ├── evaluation/         # Results analysis
│   │   ├── forensics.py    # Predictiveness analysis
│   │   └── comparison.py   # Cost + pass rate comparison
│   ├── pipeline/           # Orchestration
│   └── cli.py              # Typer CLI
├── scripts/
│   ├── setup_swebench.sh   # Setup script
│   └── run_experiment.sh   # Full experiment runner
├── HYPOTHESIS.md           # Full hypothesis documentation
└── pyproject.toml
```

## Claude Code Integration Notes

For Claude Code specifically, the intervention focuses on:

- **Tool-use correctness** — Anthropic tool_use ↔ tool_result pairing must be preserved
- **File-edit tool schemas** — Read/Edit/Write operations
- **Workspace consistency gates** — Around file edits + commands

This avoids diff-grammar work and focuses on preventing broken intermediate states from entering the tool loop.

## Scope

### What LSP Diagnostics Catch

- Undefined symbols
- Signature mismatches
- Type errors
- Wrong imports
- Wrong member access

### Language Support

| Language | Diagnostics Tool | Status |
|----------|------------------|--------|
| Python | pyright | Implemented |
| TypeScript | tsserver | Planned |
| Go | gopls | Planned |
| Rust | rust-analyzer | Planned |

LSP interface is standardized, but server quality varies by ecosystem.

## References

- [HYPOTHESIS.md](./HYPOTHESIS.md) — Full hypothesis and validation approach
- [SWE-bench](https://github.com/SWE-bench/SWE-bench)
- [LSP Specification](https://microsoft.github.io/language-server-protocol/)

## License

MIT
