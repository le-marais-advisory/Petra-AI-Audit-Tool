from __future__ import annotations

from src.core.config import AppYaml, Settings
from src.providers.vision.base import VisionProvider
from src.providers.vision.claude import ClaudeVisionProvider
from src.providers.vision.openai import OpenAIVisionProvider


def build_vision_provider(app_config: AppYaml, settings: Settings) -> VisionProvider:
    if settings.VISION_PROVIDER == "openai":
        if not settings.OPENAI_API_KEY:
            raise ValueError("OpenAI API key is not configured. Vision analysis was skipped.")
        return OpenAIVisionProvider(
            api_key=settings.OPENAI_API_KEY,
            model_id=settings.OPENAI_VISION_MODEL or app_config.vision.model_id,
            temperature=app_config.vision.temperature,
            seed=app_config.vision.seed,
            max_completion_tokens=app_config.vision.max_completion_tokens,
            image_detail=app_config.vision.image_detail,
            max_concurrent=app_config.vision.global_max_concurrent,
        )

    if settings.VISION_PROVIDER == "claude":
        if not settings.ANTHROPIC_API_KEY:
            raise ValueError("Anthropic API key is not configured. Vision analysis was skipped.")
        return ClaudeVisionProvider(
            api_key=settings.ANTHROPIC_API_KEY,
            model_id=settings.CLAUDE_VISION_MODEL or settings.CLAUDE_TEXT_MODEL,
            # Only sent when explicitly configured — Claude Sonnet 5 rejects a
            # non-default temperature with a 400, so don't inherit the OpenAI
            # default from app.yaml.
            temperature=settings.CLAUDE_VISION_TEMPERATURE,
            max_tokens=settings.CLAUDE_VISION_MAX_TOKENS,
            max_concurrent=app_config.vision.global_max_concurrent,
        )

    raise ValueError(f"Unsupported vision provider: {settings.VISION_PROVIDER}")
