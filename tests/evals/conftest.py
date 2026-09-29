"""Evals run live LLM calls. They are excluded by default (pytest.ini) and run with:

    pytest tests/evals -m eval

They require a .env with the configured provider's API key.
"""
from __future__ import annotations

import os

import pytest


def _has_provider_key() -> bool:
    try:
        from src.core.config import get_settings

        settings = get_settings()
    except Exception:
        return False
    keys = [getattr(settings, name, None) for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")]
    return any(keys) or any(os.getenv(n) for n in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if _has_provider_key():
        return
    skip = pytest.mark.skip(reason="evals need an LLM provider API key (.env)")
    for item in items:
        if "eval" in item.keywords:
            item.add_marker(skip)
