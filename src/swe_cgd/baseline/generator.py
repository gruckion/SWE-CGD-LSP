"""Baseline prediction generator for SWE-bench instances."""

import json
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterator, Optional

from datasets import load_dataset
from litellm import completion
from tenacity import retry, stop_after_attempt, wait_exponential

from ..utils.config import Config
from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class CostMetrics:
    """Cost metrics for a single LLM call or aggregated operation."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    wall_time_seconds: float = 0.0
    llm_calls: int = 0
    failed_calls: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CostMetrics":
        return cls(
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            total_tokens=data.get("total_tokens", 0),
            wall_time_seconds=data.get("wall_time_seconds", 0.0),
            llm_calls=data.get("llm_calls", 0),
            failed_calls=data.get("failed_calls", 0),
        )

    def __add__(self, other: "CostMetrics") -> "CostMetrics":
        """Combine two CostMetrics objects."""
        return CostMetrics(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            wall_time_seconds=self.wall_time_seconds + other.wall_time_seconds,
            llm_calls=self.llm_calls + other.llm_calls,
            failed_calls=self.failed_calls + other.failed_calls,
        )


@dataclass
class Prediction:
    """A single SWE-bench prediction with cost tracking."""

    instance_id: str
    model_name_or_path: str
    model_patch: str
    cost_metrics: Optional[CostMetrics] = None
    # Track repair iterations for cleanup predictions
    repair_iterations: int = 0
    # Track if this prediction had diagnostic issues
    had_diagnostic_issues: bool = False

    def to_dict(self) -> dict:
        result = {
            "instance_id": self.instance_id,
            "model_name_or_path": self.model_name_or_path,
            "model_patch": self.model_patch,
            "repair_iterations": self.repair_iterations,
            "had_diagnostic_issues": self.had_diagnostic_issues,
        }
        if self.cost_metrics:
            result["cost_metrics"] = self.cost_metrics.to_dict()
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict) -> "Prediction":
        cost_metrics = None
        if "cost_metrics" in data:
            cost_metrics = CostMetrics.from_dict(data["cost_metrics"])
        return cls(
            instance_id=data["instance_id"],
            model_name_or_path=data["model_name_or_path"],
            model_patch=data["model_patch"],
            cost_metrics=cost_metrics,
            repair_iterations=data.get("repair_iterations", 0),
            had_diagnostic_issues=data.get("had_diagnostic_issues", False),
        )


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

        yielded_count = 0
        for instance in self._dataset:
            # Filter by instance_ids if specified
            if self.config.instance_ids:
                if instance["instance_id"] not in self.config.instance_ids:
                    continue

            # Limit by max_instances (count of yielded items, not dataset index)
            if self.config.max_instances and yielded_count >= self.config.max_instances:
                break

            yielded_count += 1
            yield instance

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
    )
    def _generate_patch_with_metrics(self, instance: dict) -> tuple[str, CostMetrics]:
        """Generate a patch for a single instance, returning patch and cost metrics."""
        hints = instance.get("hints_text", "") or "No hints available."

        user_message = BASELINE_USER_TEMPLATE.format(
            repo=instance["repo"],
            problem_statement=instance["problem_statement"],
            hints_text=hints,
        )

        logger.debug(f"Generating patch for {instance['instance_id']}")

        start_time = time.time()
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
        wall_time = time.time() - start_time

        # Extract token usage from response
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

        # Clean up common formatting issues
        patch = self._clean_patch(patch)

        return patch, metrics

    def generate_patch(self, instance: dict) -> str:
        """Generate a patch for a single instance (legacy interface)."""
        patch, _ = self._generate_patch_with_metrics(instance)
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
        """Generate a prediction for a single instance with cost tracking."""
        patch, metrics = self._generate_patch_with_metrics(instance)

        return Prediction(
            instance_id=instance["instance_id"],
            model_name_or_path=self.config.llm.model,
            model_patch=patch,
            cost_metrics=metrics,
        )

    def generate_all(
        self,
        output_path: Optional[Path] = None,
    ) -> list[Prediction]:
        """Generate predictions for all configured instances with cost tracking."""
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

                    if prediction.cost_metrics:
                        logger.info(
                            f"Generated patch for {instance_id} "
                            f"(tokens: {prediction.cost_metrics.total_tokens}, "
                            f"time: {prediction.cost_metrics.wall_time_seconds:.2f}s)"
                        )
                    else:
                        logger.info(f"Generated patch for {instance_id}")

                except Exception as e:
                    logger.error(f"Failed to generate patch for {instance_id}: {e}")
                    # Write empty patch on failure with failed_calls metric
                    prediction = Prediction(
                        instance_id=instance_id,
                        model_name_or_path=self.config.llm.model,
                        model_patch="",
                        cost_metrics=CostMetrics(failed_calls=1),
                    )
                    predictions.append(prediction)
                    f.write(prediction.to_json() + "\n")
                    f.flush()

        # Log aggregate cost metrics
        total_metrics = CostMetrics()
        for pred in predictions:
            if pred.cost_metrics:
                total_metrics = total_metrics + pred.cost_metrics

        logger.info(
            f"Generated {len(predictions)} predictions -> {output_path}\n"
            f"  Total tokens: {total_metrics.total_tokens}\n"
            f"  Total time: {total_metrics.wall_time_seconds:.2f}s\n"
            f"  Failed calls: {total_metrics.failed_calls}"
        )
        return predictions
