"""The Claude model registry (config/models.yaml): which models may be used, what each accepts, and what
each costs."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "config" / "models.yaml"

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class ModelSpec:
    id: str
    input: float
    output: float
    cache_read: float
    cache_write_5m: float
    effort: bool = True
    sampling: bool = False
    refusal_fallback: bool = False
    max_output: int = 128000

    def cost(self, uncached_input: int, cache_read: int, cache_write: int, output: int) -> float:
        """USD for one call's token counts."""
        return (uncached_input * self.input + cache_read * self.cache_read + cache_write * self.cache_write_5m
                + output * self.output) / 1_000_000


@lru_cache(maxsize=1)
def model_registry() -> dict[str, ModelSpec]:
    data = yaml.safe_load(_REGISTRY_PATH.read_text(encoding="utf-8")) or {}
    return {model_id: ModelSpec(id=model_id, **spec) for model_id, spec in (data.get("models") or {}).items()}


_DATED = re.compile(r"^(?P<alias>.+)-\d{8}$")


def get_model(model_id: str | None) -> ModelSpec | None:
    """The registry entry for ``model_id``. Responses can name a dated snapshot of a listed alias
    (claude-haiku-4-5-20251001 for claude-haiku-4-5), which resolves to the alias."""
    if not model_id:
        return None
    registry = model_registry()
    if model_id in registry:
        return registry[model_id]
    dated = _DATED.match(model_id)
    return registry.get(dated.group("alias")) if dated else None


def normalize_effort(effort: str | None) -> str | None:
    """None for "use the model default" (unset or empty), else a validated level."""
    if effort is None or not str(effort).strip():
        return None
    level = str(effort).strip().lower()
    if level not in EFFORT_LEVELS:
        raise ValueError(f"unknown effort {effort!r} (expected one of {', '.join(EFFORT_LEVELS)})")
    return level
