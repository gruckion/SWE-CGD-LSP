"""One-shot cleanup: repair patches using diagnostic feedback."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from litellm import completion
from tenacity import retry, stop_after_attempt, wait_exponential

from ..baseline.generator import Prediction
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
    improvement_summary: str


class PatchCleaner:
    """Cleans up patches using diagnostic feedback."""

    def __init__(self, config: Config):
        self.config = config

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
    )
    def clean_patch(
        self,
        instance: dict,
        original_patch: str,
        diagnostics: DiagnosticResult,
    ) -> str:
        """Generate a cleaned patch based on diagnostic feedback."""

        if not diagnostics.has_issues:
            # No issues to fix, return original
            return original_patch

        diagnostics_context = diagnostics.to_llm_context()

        user_message = CLEANUP_USER_TEMPLATE.format(
            repo=instance["repo"],
            problem_statement=instance["problem_statement"],
            original_patch=original_patch,
            diagnostics=diagnostics_context,
        )

        logger.debug(f"Cleaning patch for {instance['instance_id']}")

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

        patch = response.choices[0].message.content.strip()
        patch = self._clean_patch_format(patch)

        return patch

    def _clean_patch_format(self, patch: str) -> str:
        """Clean up common patch formatting issues."""
        # Remove markdown code blocks if present
        if patch.startswith("```"):
            lines = patch.split("\n")
            start_idx = 1 if lines[0].startswith("```") else 0
            end_idx = len(lines)
            for i in range(len(lines) - 1, -1, -1):
                if lines[i].strip() == "```":
                    end_idx = i
                    break
            patch = "\n".join(lines[start_idx:end_idx])

        # Ensure patch ends with newline
        if patch and not patch.endswith("\n"):
            patch += "\n"

        return patch

    def process_prediction(
        self,
        instance: dict,
        prediction: Prediction,
        diagnostics: DiagnosticResult,
    ) -> Prediction:
        """Process a single prediction with cleanup if needed."""

        if not diagnostics.has_issues:
            logger.info(f"{instance['instance_id']}: No issues, keeping original patch")
            return prediction

        logger.info(
            f"{instance['instance_id']}: Found {diagnostics.total_errors} errors, cleaning..."
        )

        try:
            cleaned_patch = self.clean_patch(instance, prediction.model_patch, diagnostics)

            return Prediction(
                instance_id=prediction.instance_id,
                model_name_or_path=f"{prediction.model_name_or_path}_cleaned",
                model_patch=cleaned_patch,
            )

        except Exception as e:
            logger.error(f"Cleanup failed for {instance['instance_id']}: {e}")
            # Return original on failure
            return prediction


# Control experiment: generate a second independent sample instead of cleanup
RESAMPLE_SYSTEM_PROMPT = """You are an expert software engineer. Your task is to fix the issue described below by generating a patch.

IMPORTANT INSTRUCTIONS:
1. Analyze the issue carefully
2. Generate a unified diff patch that fixes the issue
3. Only output the patch itself - no explanations, no markdown code blocks
4. The patch should be in standard unified diff format
5. Make minimal changes - only fix what's necessary

Output ONLY the unified diff patch, starting with `diff --git` or `--- a/`."""


RESAMPLE_USER_TEMPLATE = """## Repository Information
Repository: {repo}

## Issue Description
{problem_statement}

## Hints (if available)
{hints_text}

Generate a patch to fix this issue. Think carefully and produce your best solution.
Output ONLY the unified diff."""


class ControlResampler:
    """Control experiment: generate independent second sample (no diagnostics)."""

    def __init__(self, config: Config):
        self.config = config

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
    )
    def resample(self, instance: dict) -> str:
        """Generate an independent second patch attempt."""
        hints = instance.get("hints_text", "") or "No hints available."

        user_message = RESAMPLE_USER_TEMPLATE.format(
            repo=instance["repo"],
            problem_statement=instance["problem_statement"],
            hints_text=hints,
        )

        # Use slightly higher temperature for diversity
        response = completion(
            model=self.config.llm.model,
            messages=[
                {"role": "system", "content": RESAMPLE_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            max_tokens=self.config.llm.max_tokens,
            temperature=0.3,  # Slightly higher for diversity
            api_key=self.config.llm.api_key,
        )

        patch = response.choices[0].message.content.strip()
        return self._clean_patch_format(patch)

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
