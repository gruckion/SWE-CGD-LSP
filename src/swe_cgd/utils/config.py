"""Configuration management for SWE-CGD."""

import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field
from dotenv import load_dotenv


class LLMConfig(BaseModel):
    """LLM configuration."""

    model: str = Field(default="claude-sonnet-4-20250514", description="Model name for LiteLLM")
    api_key: Optional[str] = Field(default=None, description="API key (or use env var)")
    max_tokens: int = Field(default=8192, description="Max tokens for generation")
    temperature: float = Field(default=0.0, description="Sampling temperature")


class DiagnosticsConfig(BaseModel):
    """Diagnostics configuration."""

    use_pyright: bool = Field(default=True, description="Run pyright for type checking")
    use_compileall: bool = Field(default=True, description="Run compileall for syntax check")
    pyright_level: str = Field(default="basic", description="Pyright type checking level")
    timeout_seconds: int = Field(default=60, description="Timeout for diagnostics")


class EvaluationConfig(BaseModel):
    """Evaluation configuration."""

    dataset_name: str = Field(
        default="princeton-nlp/SWE-bench_Lite", description="HuggingFace dataset name"
    )
    max_workers: int = Field(default=4, description="Parallel workers for evaluation")
    timeout_seconds: int = Field(default=1800, description="Timeout per instance")


class Config(BaseModel):
    """Main configuration."""

    project_root: Path = Field(default_factory=lambda: Path.cwd())
    output_dir: Path = Field(default_factory=lambda: Path.cwd() / "output")
    preds_dir: Path = Field(default_factory=lambda: Path.cwd() / "preds")
    logs_dir: Path = Field(default_factory=lambda: Path.cwd() / "logs")

    llm: LLMConfig = Field(default_factory=LLMConfig)
    diagnostics: DiagnosticsConfig = Field(default_factory=DiagnosticsConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)

    # Instance selection
    instance_ids: Optional[list[str]] = Field(
        default=None, description="Specific instance IDs to process (None = all)"
    )
    max_instances: Optional[int] = Field(
        default=None, description="Max instances to process (None = all)"
    )

    def ensure_dirs(self) -> None:
        """Create necessary directories."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.preds_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)


def load_config(config_path: Optional[Path] = None) -> Config:
    """Load configuration from environment and optional config file."""
    load_dotenv()

    config = Config()

    # Override from environment
    if api_key := os.getenv("ANTHROPIC_API_KEY"):
        config.llm.api_key = api_key
    elif api_key := os.getenv("OPENAI_API_KEY"):
        config.llm.api_key = api_key

    if model := os.getenv("SWE_CGD_MODEL"):
        config.llm.model = model

    if dataset := os.getenv("SWE_CGD_DATASET"):
        config.evaluation.dataset_name = dataset

    config.ensure_dirs()
    return config
