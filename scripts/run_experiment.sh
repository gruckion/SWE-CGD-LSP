#!/bin/bash
# Run a complete CGD validation experiment

set -e

# Configuration
DATASET="${DATASET:-princeton-nlp/SWE-bench_Lite}"
MODEL="${MODEL:-claude-sonnet-4-20250514}"
MAX_INSTANCES="${MAX_INSTANCES:-20}"
MAX_WORKERS="${MAX_WORKERS:-4}"
RUN_ID="${RUN_ID:-cgd_$(date +%Y%m%d_%H%M%S)}"

echo "=== CGD Validation Experiment ==="
echo "Dataset: $DATASET"
echo "Model: $MODEL"
echo "Max Instances: $MAX_INSTANCES"
echo "Workers: $MAX_WORKERS"
echo "Run ID: $RUN_ID"
echo ""

# Activate virtual environment
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Check API key
if [ -z "$ANTHROPIC_API_KEY" ] && [ -z "$OPENAI_API_KEY" ]; then
    echo "ERROR: No API key found. Set ANTHROPIC_API_KEY or OPENAI_API_KEY"
    exit 1
fi

# Create output directories
mkdir -p preds output logs

# Step 1: Generate baseline predictions
echo ""
echo "=== Step 1: Generating baseline predictions ==="
swe-cgd baseline \
    --output "preds/${RUN_ID}_baseline.jsonl" \
    --model "$MODEL" \
    --dataset "$DATASET" \
    --max "$MAX_INSTANCES"

BASELINE_PREDS="preds/${RUN_ID}_baseline.jsonl"
echo "Baseline predictions: $BASELINE_PREDS"

# Step 2: Evaluate baseline (SWE-bench)
echo ""
echo "=== Step 2: Evaluating baseline predictions ==="
python -m swebench.harness.run_evaluation \
    --predictions_path "$BASELINE_PREDS" \
    --dataset_name "$DATASET" \
    --max_workers "$MAX_WORKERS" \
    --run_id "${RUN_ID}_baseline" \
    || echo "Baseline evaluation completed (check logs for errors)"

# Step 3: Collect diagnostics
echo ""
echo "=== Step 3: Collecting diagnostics ==="
# Run real Docker diagnostics inside SWE-bench containers
python -c "
from pathlib import Path
from swe_cgd.diagnostics.docker_runner import run_diagnostics_batch

preds_file = Path('$BASELINE_PREDS')
diag_file = Path('output/${RUN_ID}_diagnostics.jsonl')

print(f'Running Docker diagnostics on {preds_file}...')
run_diagnostics_batch(preds_file, diag_file, timeout=120)
print(f'Diagnostics saved to: {diag_file}')
"

DIAGNOSTICS_FILE="output/${RUN_ID}_diagnostics.jsonl"

# Step 4: Generate cleanup predictions
echo ""
echo "=== Step 4: Generating cleanup predictions ==="
swe-cgd cleanup \
    "$BASELINE_PREDS" \
    "$DIAGNOSTICS_FILE" \
    --output "preds/${RUN_ID}_cleanup.jsonl" \
    --model "$MODEL" \
    --dataset "$DATASET"

CLEANUP_PREDS="preds/${RUN_ID}_cleanup.jsonl"
echo "Cleanup predictions: $CLEANUP_PREDS"

# Step 5: Evaluate cleanup
echo ""
echo "=== Step 5: Evaluating cleanup predictions ==="
python -m swebench.harness.run_evaluation \
    --predictions_path "$CLEANUP_PREDS" \
    --dataset_name "$DATASET" \
    --max_workers "$MAX_WORKERS" \
    --run_id "${RUN_ID}_cleanup" \
    || echo "Cleanup evaluation completed (check logs for errors)"

# Step 6: Compare results
echo ""
echo "=== Step 6: Comparing results ==="
BASELINE_RESULTS="evaluation_results/${RUN_ID}_baseline/results.json"
CLEANUP_RESULTS="evaluation_results/${RUN_ID}_cleanup/results.json"

if [ -f "$BASELINE_RESULTS" ] && [ -f "$CLEANUP_RESULTS" ]; then
    swe-cgd compare \
        "$BASELINE_RESULTS" \
        "$CLEANUP_RESULTS" \
        --baseline-name "baseline" \
        --treatment-name "cleanup" \
        --output "output/${RUN_ID}_comparison.json"
else
    echo "Results files not found, skipping comparison"
fi

# Step 7: Generate forensics report
echo ""
echo "=== Step 7: Generating forensics report ==="
if [ -f "$BASELINE_RESULTS" ]; then
    swe-cgd forensics \
        "${RUN_ID}_baseline" \
        --logs-dir logs \
        --results "$BASELINE_RESULTS" \
        --diagnostics "$DIAGNOSTICS_FILE" \
        --output "output/${RUN_ID}_forensics.json"
else
    echo "Baseline results not found, skipping forensics"
fi

echo ""
echo "=== Experiment Complete ==="
echo "Run ID: $RUN_ID"
echo ""
echo "Output files:"
echo "  Baseline predictions: $BASELINE_PREDS"
echo "  Cleanup predictions: $CLEANUP_PREDS"
echo "  Diagnostics: $DIAGNOSTICS_FILE"
echo ""
echo "Results (if evaluation succeeded):"
echo "  Baseline: $BASELINE_RESULTS"
echo "  Cleanup: $CLEANUP_RESULTS"
echo "  Comparison: output/${RUN_ID}_comparison.json"
echo "  Forensics: output/${RUN_ID}_forensics.json"
