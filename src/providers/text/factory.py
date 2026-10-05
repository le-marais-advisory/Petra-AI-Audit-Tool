from __future__ import annotations

from src.core.config import Settings
from src.providers.text.base import TextAnalysisProvider
from src.providers.text.claude import ClaudeTextAnalysisProvider
from src.providers.text.openai import OpenAITextAnalysisProvider


def build_text_provider(settings: Settings) -> TextAnalysisProvider:
    if settings.TEXT_PROVIDER == "openai":
        if not settings.OPENAI_API_KEY:
            raise ValueError("OpenAI API key is not configured. Text analysis was skipped.")
        return OpenAITextAnalysisProvider(
            api_key=settings.OPENAI_API_KEY,
            model_id=settings.OPENAI_TEXT_MODEL,
            temperature=settings.OPENAI_TEXT_TEMPERATURE,
            max_completion_tokens=settings.OPENAI_TEXT_MAX_COMPLETION_TOKENS,
        )

    if settings.TEXT_PROVIDER == "claude":
        if not settings.ANTHROPIC_API_KEY:
            raise ValueError("Anthropic API key is not configured. Text analysis was skipped.")
        return ClaudeTextAnalysisProvider(
            api_key=settings.ANTHROPIC_API_KEY,
            model_id=settings.CLAUDE_TEXT_MODEL,
            temperature=settings.CLAUDE_TEXT_TEMPERATURE,
            max_tokens=settings.CLAUDE_TEXT_MAX_TOKENS,
            structured_max_tokens=settings.CLAUDE_STRUCTURED_MAX_TOKENS,
        )

    raise ValueError(f"Unsupported text provider: {settings.TEXT_PROVIDER}")
