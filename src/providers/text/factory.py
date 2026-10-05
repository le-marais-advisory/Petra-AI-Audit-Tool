from __future__ import annotations

from src.core.config import Settings
from src.providers.models import get_model
from src.providers.text.base import TextAnalysisProvider
from src.providers.text.claude import ClaudeTextAnalysisProvider


def build_text_provider(settings: Settings, model: str | None = None, cache_salt: str = "") -> TextAnalysisProvider:
    """A Claude text provider for ``model`` (default CLAUDE_TEXT_MODEL), shaped by what that model accepts
    (config/models.yaml): temperature only where it is allowed, the refusal fallback where it is offered,
    and token budgets clamped to the model's output ceiling."""
    if not settings.ANTHROPIC_API_KEY:
        raise ValueError("Anthropic API key is not configured. Text analysis was skipped.")
    model = model or settings.CLAUDE_TEXT_MODEL
    spec = get_model(model)
    ceiling = spec.max_output if spec else None
    return ClaudeTextAnalysisProvider(
        api_key=settings.ANTHROPIC_API_KEY,
        model_id=model,
        temperature=settings.CLAUDE_TEXT_TEMPERATURE if spec is None or spec.sampling else None,
        max_tokens=min(settings.CLAUDE_TEXT_MAX_TOKENS, ceiling or settings.CLAUDE_TEXT_MAX_TOKENS),
        structured_max_tokens=min(settings.CLAUDE_STRUCTURED_MAX_TOKENS, ceiling or settings.CLAUDE_STRUCTURED_MAX_TOKENS),
        refusal_fallback=bool(spec and spec.refusal_fallback),
        cache_salt=cache_salt,
    )
