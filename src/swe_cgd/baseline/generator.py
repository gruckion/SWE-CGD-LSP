"""Baseline prediction generator for SWE-bench instances."""

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterator, Optional

from datasets import load_dataset
from litellm import completion
from tenacity import retry, stop_after_attempt, wait_exponential

from ..utils.config import Config
from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class Prediction:
    """A single SWE-bench prediction."""

    instance_id: str
    model_name_or_path: str
    model_patch: str

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


BASELINE_SYSTEM_PROMPT = """You are an expert software engineer. Your task is to fix the issue described below by generating a patch.

IMPORTANT INSTRUCTIONS:
1. Analyze the issue carefully
2. Generate a unified diff patch that fixes the issue
3. Only output the patch itself - no explanations, no markdown code blocks
4. The patch should be in standard unified diff format (like output from `git diff`)
5. Make minimal changes - only fix what's necessary
6. Do not add unnecessary comments or documentation changes

Output ONLY the unified diff patch, starting with `diff --git` or `--- a/`."""


BASELINE_USER_TEMPLATE = """## Repository Information
Repository: {repo}

## Issue Description
{problem_statement}

## Hints (if available)
{hints_text}

Generate the patch to fix this issue. Output ONLY the unified diff."""


class BaselineGenerator:
    """Generates baseline predictions for SWE-bench instances."""

    def __init__(self, config: Config):
        self.config = config
        self._dataset = None

    def load_dataset(self) -> None:
        """Load the SWE-bench dataset."""
        logger.info(f"Loading dataset: {self.config.evaluation.dataset_name}")
        self._dataset = load_dataset(
            self.config.evaluation.dataset_name,
            split="test",
        )
        logger.info(f"Loaded {len(self._dataset)} instances")

    def get_instances(self) -> Iterator[dict]:
        """Iterate over instances to process."""
        if self._dataset is None:
            self.load_dataset()

        for idx, instance in enumerate(self._dataset):
            # Filter by instance_ids if specified
            if self.config.instance_ids:
                if instance["instance_id"] not in self.config.instance_ids:
                    continue

            # Limit by max_instances if specified
            if self.config.max_instances and idx >= self.config.max_instances:
                break

            yield instance

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
    )
    def generate_patch(self, instance: dict) -> str:
        """Generate a patch for a single instance."""
        hints = instance.get("hints_text", "") or "No hints available."

        user_message = BASELINE_USER_TEMPLATE.format(
            repo=instance["repo"],
            problem_statement=instance["problem_statement"],
            hints_text=hints,
        )

        logger.debug(f"Generating patch for {instance['instance_id']}")

        response = completion(
            model=self.config.llm.model,
            messages=[
                {"role": "system", "content": BASELINE_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            max_tokens=self.config.llm.max_tokens,
            temperature=self.config.llm.temperature,
            api_key=self.config.llm.api_key,
        )

        patch = response.choices[0].message.content.strip()

        # Clean up common formatting issues
        patch = self._clean_patch(patch)

        return patch

    def _clean_patch(self, patch: str) -> str:
        """Clean up common patch formatting issues."""
        # Remove markdown code blocks if present
        if patch.startswith("```"):
            lines = patch.split("\n")
            # Find start and end of code block
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

    def generate_prediction(self, instance: dict) -> Prediction:
        """Generate a prediction for a single instance."""
        patch = self.generate_patch(instance)

        return Prediction(
            instance_id=instance["instance_id"],
            model_name_or_path=self.config.llm.model,
            model_patch=patch,
        )

    def generate_all(
        self,
        output_path: Optional[Path] = None,
    ) -> list[Prediction]:
        """Generate predictions for all configured instances."""
        predictions = []

        if output_path is None:
            output_path = self.config.preds_dir / "baseline.jsonl"

        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w") as f:
            for instance in self.get_instances():
                instance_id = instance["instance_id"]
                logger.info(f"Processing: {instance_id}")

                try:
                    prediction = self.generate_prediction(instance)
                    predictions.append(prediction)

                    # Write incrementally
                    f.write(prediction.to_json() + "\n")
                    f.flush()

                    logger.info(f"Generated patch for {instance_id}")

                except Exception as e:
                    logger.error(f"Failed to generate patch for {instance_id}: {e}")
                    # Write empty patch on failure
                    prediction = Prediction(
                        instance_id=instance_id,
                        model_name_or_path=self.config.llm.model,
                        model_patch="",
                    )
                    predictions.append(prediction)
                    f.write(prediction.to_json() + "\n")
                    f.flush()

        logger.info(f"Generated {len(predictions)} predictions -> {output_path}")
        return predictions
