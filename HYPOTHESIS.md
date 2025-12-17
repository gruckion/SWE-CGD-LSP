# SWE-CGD-LSP: Hypothesis & Validation Approach

## Core Hypothesis

**Agentic editing pipelines waste budget repairing predictable, workspace-detectable semantic breakage after the model has already conditioned on it.**

### Primary Hypothesis (Economic + Causal)

A material share of failures and cost blow-ups in instruction-based code editing are driven by early, non-transient "workspace consistency" violations:

- Undefined symbols
- Signature mismatches
- Type errors
- Wrong imports
- Wrong member access

These violations are **detectable via LSP/static tooling** in the user's actual workspace.

If these violations are prevented from reaching the outer agent loop (or repaired before the agent continues), then:

1. **pass@budget increases** — fewer runs die in repair loops before timeout
2. **cost per solve decreases** — fewer tool calls, fewer iterations, less context churn

...even if the proxy spends extra tokens on hidden remediation.

### Churn Hypothesis

Once a non-transient semantic violation exists in the workspace, agentic systems tend to:

- Expand scope
- Add messages and tool output
- Accumulate misleading intermediate state

This makes later steps less reliable. The repair narrative itself pollutes context and degrades downstream decisions.

## Why Pass Rate Might Move (Under Realistic Conditions)

The proxy doesn't make the model smarter. It stops it wasting budget on predictable breakage.

1. **Budgets are real** — SWE-bench agents have max steps, max tokens, timeouts. A run that "would eventually fix it" still fails by budget exhaustion.

2. **Context pollution changes downstream decisions** — Diagnostic dumps, partial patches, contradictory hypotheses push the model into worse action selection later.

3. **Tool reliability is not 100%** — Every additional tool call is another chance for flaky tests, environment quirks, rate limits. Fewer calls = higher mechanical reliability.

4. **Agent's own checks may be narrower** — The proxy might run broader/earlier checks (multi-file, dependency-aware) that catch issues the agent never noticed in time.

## Two Outcomes to Measure

### Hypothesis A: Cost Reduction Only (Weaker, Still Valuable)

- Pass rate stays ~flat
- Cost per solve drops significantly
- Strong product wedge if delta is large and consistent

### Hypothesis B: Cost Reduction + Pass@Budget Improvement (Stronger)

- Pass rate improves under fixed budgets/timeouts
- Cost per solve also drops
- The more defensible story

## Validation Approach (Minimal Effort, Go/No-Go Signal)

### 1. Baseline Forensics

**Question:** Is "early LSP error" predictive of failure/cost?

**Method:**
- Run baseline agent on 100-200 SWE-bench tasks
- Log: when first LSP Error appears, whether it persists, total tool calls, solved vs failed

**Compute:**
- **Predictiveness:** P(fail | early LSP error) vs P(fail | no early LSP error)
- **Churn delta:** Average extra tool calls/tokens after first LSP error
- **Ceiling:** Fraction of failures that had an early, persistent LSP Error

This tells you whether the intervention target is (a) common, (b) costly, and (c) plausibly fixable headroom.

### 2. Counterfactual One-Shot Cleanup

**Question:** Does fixing LSP errors actually rescue outcomes?

**Method:**
1. Let baseline agent produce its patch
2. Apply patch in the workspace
3. Run LSP diagnostics
4. If there are Error diagnostics, one follow-up call: "Produce a minimal change to eliminate these diagnostics; do not change behavior."
5. Re-run LSP + tests

**Interpretation:**
- If pass rate barely moves → thesis weakens
- If pass rate moves materially → real headroom exists before any "true CGD" work

### 3. Lazy Proxy POC

**Question:** Does preventing broken intermediate states from entering the agent loop reduce churn and increase solve rate?

**Method:**
- Buffer model output (or tool payload) fully
- Apply it
- Run LSP
- If violations exceed threshold, trigger hidden repair and retry until clean (or budget exhausted)
- Only then return output to outer agent

**What this avoids:**
- Token-level constrained decoding
- Statement-boundary parsing
- Transient/non-transient sophistication

**What this tests:** The core value proposition directly.

## Key Metrics

### Headline Metric

**Cost per solved task** = (tokens + tool time + wall time proxy overhead) / #solved

### Secondary Metrics

- pass@1 / pass@budget
- Tool-call count and failed-tool-call count
- Variance / tail risk (frequency of token blow-ups)

## Eval Regimes

1. **Unbounded / generous budget regime**
   - Expect: pass rate may be similar
   - Measure: cost per solve, tool calls, variance

2. **Fixed-budget regime** (the one that matters economically)
   - Cap max tool calls / max wall time / max tokens
   - Expect: if proxy provides real value, pass@budget increases because fewer runs die in repair loops

## Scope Notes

### Claude Code Specifics

For Claude Code, the focus is on tool_use/tool_result correctness and file-edit tool schemas, not unified diffs:

- Anthropic tool use requires strict tool_use ↔ tool_result pairing and ordering
- Claude Code's workflow is heavily tool-centric (Read/Edit/Write/etc.)
- Hooks exist to observe tool lifecycle events

For a Claude Code-first wedge, avoid diff-grammar work and focus on **workspace consistency gates** around file edits + commands.

### Language/Tooling Reality

- "Any language" is true at the LSP interface level, but LSP server quality varies by ecosystem
- "Any IDE/CLI" is true as HTTP proxy integration, but tool schemas differ (Anthropic vs OpenAI vs others)

## Decision Gate

If baseline forensics + one-shot cleanup cannot show:

1. Early LSP Error is common in failures, AND
2. Eliminating it often rescues the run or reduces churn

...then the "proxy that prevents semantic breakage" thesis is not strong enough to justify deeper engineering.
