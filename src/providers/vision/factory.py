from __future__ import annotations

from src.core.config import AppYaml, Settings
from src.providers.models import get_model
from src.providers.vision.base import VisionProvider
from src.providers.vision.claude import ClaudeVisionProvider


def build_vision_provider(app_config: AppYaml, settings: Settings, model: str | None = None,
                          cache_salt: str = "") -> VisionProvider:
    """A Claude vision provider for ``model`` (default CLAUDE_VISION_MODEL, else CLAUDE_TEXT_MODEL), shaped
    by what that model accepts, as in build_text_provider."""
    if not settings.ANTHROPIC_API_KEY:
        raise ValueError("Anthropic API key is not configured. Vision analysis was skipped.")
    model = model or settings.CLAUDE_VISION_MODEL or settings.CLAUDE_TEXT_MODEL
    spec = get_model(model)
    return ClaudeVisionProvider(
        api_key=settings.ANTHROPIC_API_KEY,
        model_id=model,
        temperature=settings.CLAUDE_VISION_TEMPERATURE if spec is None or spec.sampling else None,
        max_tokens=min(settings.CLAUDE_VISION_MAX_TOKENS, spec.max_output if spec else settings.CLAUDE_VISION_MAX_TOKENS),
        max_concurrent=app_config.vision.global_max_concurrent,
        refusal_fallback=bool(spec and spec.refusal_fallback),
        cache_salt=cache_salt,
    )
