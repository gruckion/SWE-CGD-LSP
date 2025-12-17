"""Diagnostic-guided cleanup: repair patches using LSP feedback with repair loop."""

import time
from dataclasses import dataclass
from typing import Optional

from litellm import completion
from tenacity import retry, stop_after_attempt, wait_exponential

from ..baseline.generator import Prediction, CostMetrics
from ..diagnostics.runner import DiagnosticResult
from ..utils.config import Config
from ..utils.logging import get_logger

logger = get_logger(__name__)


CLEANUP_SYSTEM_PROMPT = """You are an expert software engineer fixing a patch that has diagnostic issues.

Your task:
1. Review the original issue and the attempted patch
2. Analyze the diagnostic errors (syntax errors, type errors, undefined names, etc.)
3. Generate a CORRECTED patch that:
   - Fixes the original issue
   - Resolves ALL the diagnostic errors
   - Makes minimal additional changes

IMPORTANT INSTRUCTIONS:
- Output ONLY the corrected unified diff patch
- No explanations, no markdown code blocks
- Start with `diff --git` or `--- a/`
- Make sure variable names, function signatures, and imports are correct
- If a symbol is undefined, make sure to import it or define it properly"""


CLEANUP_USER_TEMPLATE = """## Original Issue
Repository: {repo}

{problem_statement}

## Original Patch (has issues)
```diff
{original_patch}
```

## Diagnostic Issues Found
{diagnostics}

## Your Task
Generate a CORRECTED patch that:
1. Still fixes the original issue
2. Resolves all the diagnostic errors listed above

Output ONLY the corrected unified diff patch."""


@dataclass
class CleanupResult:
    """Result of a cleanup attempt."""

    instance_id: str
    original_patch: str
    cleaned_patch: str
    diagnostics: DiagnosticResult
    repair_iterations: int
    total_cost: CostMetrics
    final_has_issues: bool


class PatchCleaner:
    """Cleans up patches using diagnostic feedback with iterative repair."""

    def __init__(
        self,
        config: Config,
        max_repair_iterations: int = 3,
        max_repair_tokens: int = 50000,
    ):
        self.config = config
        self.max_repair_iterations = max_repair_iterations
        self.max_repair_tokens = max_repair_tokens

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
    )
    def _single_cleanup_call(
        self,
        instance: dict,
        current_patch: str,
        diagnostics: DiagnosticResult,
    ) -> tuple[str, CostMetrics]:
        """Make a single cleanup LLM call, returning patch and metrics."""

        diagnostics_context = diagnostics.to_llm_context()

        user_message = CLEANUP_USER_TEMPLATE.format(
            repo=instance["repo"],
            problem_statement=instance["problem_statement"],
            original_patch=current_patch,
            diagnostics=diagnostics_context,
        )

        start_time = time.time()
        response = completion(
            model=self.config.llm.model,
            messages=[
                {"role": "system", "content": CLEANUP_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            max_tokens=self.config.llm.max_tokens,
            temperature=self.config.llm.temperature,
            api_key=self.config.llm.api_key,
        )
        wall_time = time.time() - start_time

        # Extract token usage
        usage = getattr(response, "usage", None)
        metrics = CostMetrics(
            input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            total_tokens=getattr(usage, "total_tokens", 0) if usage else 0,
            wall_time_seconds=wall_time,
            llm_calls=1,
            failed_calls=0,
        )

        patch = response.choices[0].message.content.strip()
        patch = self._clean_patch_format(patch)

        return patch, metrics

    def _clean_patch_format(self, patch: str) -> str:
        """Clean up common patch formatting issues."""
        if patch.startswith("```"):
            lines = patch.split("\n")
            start_idx = 1 if lines[0].startswith("```") else 0
            end_idx = len(lines)
            for i in range(len(lines) - 1, -1, -1):
                if lines[i].strip() == "```":
                    end_idx = i
                    break
            patch = "\n".join(lines[start_idx:end_idx])

        if patch and not patch.endswith("\n"):
            patch += "\n"

        return patch

    def clean_patch_with_loop(
        self,
        instance: dict,
        original_patch: str,
        initial_diagnostics: DiagnosticResult,
        run_diagnostics_fn: Optional[callable] = None,
    ) -> CleanupResult:
        """
        Clean a patch using iterative repair loop.

        Continues repairing until:
        - No more diagnostic issues, OR
        - Max iterations reached, OR
        - Token budget exhausted

        Args:
            instance: The SWE-bench instance dict
            original_patch: The patch to repair
            initial_diagnostics: Initial diagnostic results
            run_diagnostics_fn: Optional function to re-run diagnostics after each repair.
                               Signature: (instance_id: str, patch: str) -> DiagnosticResult
                               If not provided, only does one-shot repair.

        Returns:
            CleanupResult with final patch and metrics
        """
        total_cost = CostMetrics()
        current_patch = original_patch
        current_diagnostics = initial_diagnostics
        iterations = 0

        while iterations < self.max_repair_iterations:
            # Check if we still have issues to fix
            if not current_diagnostics.has_issues:
                logger.info(
                    f"{instance['instance_id']}: No issues after {iterations} iterations"
                )
                break

            # Check token budget
            if total_cost.total_tokens >= self.max_repair_tokens:
                logger.warning(
                    f"{instance['instance_id']}: Token budget exhausted "
                    f"({total_cost.total_tokens} >= {self.max_repair_tokens})"
                )
                break

            iterations += 1
            logger.info(
                f"{instance['instance_id']}: Repair iteration {iterations}, "
                f"errors: {current_diagnostics.total_errors}"
            )

            try:
                # Make cleanup call
                new_patch, call_metrics = self._single_cleanup_call(
                    instance, current_patch, current_diagnostics
                )
                total_cost = total_cost + call_metrics
                current_patch = new_patch

                # If we have a diagnostics function, re-run diagnostics
                if run_diagnostics_fn:
                    current_diagnostics = run_diagnostics_fn(
                        instance["instance_id"], new_patch
                    )
                else:
                    # Without re-running diagnostics, we can only do one iteration
                    break

            except Exception as e:
                logger.error(
                    f"{instance['instance_id']}: Repair iteration {iterations} failed: {e}"
                )
                total_cost = total_cost + CostMetrics(failed_calls=1)
                break

        return CleanupResult(
            instance_id=instance["instance_id"],
            original_patch=original_patch,
            cleaned_patch=current_patch,
            diagnostics=initial_diagnostics,
            repair_iterations=iterations,
            total_cost=total_cost,
            final_has_issues=current_diagnostics.has_issues if current_diagnostics else True,
        )

    def clean_patch(
        self,
        instance: dict,
        original_patch: str,
        diagnostics: DiagnosticResult,
    ) -> str:
        """Generate a cleaned patch based on diagnostic feedback (legacy one-shot interface)."""
        if not diagnostics.has_issues:
            return original_patch

        result = self.clean_patch_with_loop(instance, original_patch, diagnostics)
        return result.cleaned_patch

    def process_prediction(
        self,
        instance: dict,
        prediction: Prediction,
        diagnostics: DiagnosticResult,
        run_diagnostics_fn: Optional[callable] = None,
    ) -> Prediction:
        """Process a single prediction with cleanup if needed."""

        if not diagnostics.has_issues:
            logger.info(f"{instance['instance_id']}: No issues, keeping original patch")
            return Prediction(
                instance_id=prediction.instance_id,
                model_name_or_path=prediction.model_name_or_path,
                model_patch=prediction.model_patch,
                cost_metrics=prediction.cost_metrics,
                repair_iterations=0,
                had_diagnostic_issues=False,
            )

        logger.info(
            f"{instance['instance_id']}: Found {diagnostics.total_errors} errors, cleaning..."
        )

        try:
            result = self.clean_patch_with_loop(
                instance,
                prediction.model_patch,
                diagnostics,
                run_diagnostics_fn,
            )

            # Combine original cost with cleanup cost
            total_cost = prediction.cost_metrics + result.total_cost if prediction.cost_metrics else result.total_cost

            return Prediction(
                instance_id=prediction.instance_id,
                model_name_or_path=f"{prediction.model_name_or_path}_cleaned",
                model_patch=result.cleaned_patch,
                cost_metrics=total_cost,
                repair_iterations=result.repair_iterations,
                had_diagnostic_issues=True,
            )

        except Exception as e:
            logger.error(f"Cleanup failed for {instance['instance_id']}: {e}")
            # Return original on failure, but mark it had issues
            return Prediction(
                instance_id=prediction.instance_id,
                model_name_or_path=prediction.model_name_or_path,
                model_patch=prediction.model_patch,
                cost_metrics=prediction.cost_metrics,
                repair_iterations=0,
                had_diagnostic_issues=True,
            )
