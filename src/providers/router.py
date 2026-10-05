"""Which model and effort each LLM call uses, and the providers that send them.

Every call has a purpose: a rule call (``text_rule``, ``vision_rule``, ``hybrid_rule``) or a workbook
stage (``layout``, ``roles``). Its model and effort are resolved field by field, first match wins:

  1. the comparison variant's override for this rule        (RoutingOverrides.rule_overrides)
  2. the rule's own ``model`` / ``effort`` fields            (unless the variant ignores them)
  3. the comparison variant's setting for the purpose        (RoutingOverrides.stages)
  4. the comparison variant's defaults                       (RoutingOverrides.defaults)
  5. the settings default for the purpose                    (CLAUDE_TEXT_MODEL, TEXT_RULE_EFFORT, ...)

Effort is dropped for models that do not accept it (config/models.yaml). Production builds a router
from settings; the comparison tool (src/evaluation/llm_compare) passes a variant.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Literal

from src.core.config import AppYaml, Settings, get_settings, load_app_yaml
from src.providers.models import get_model, normalize_effort

Purpose = Literal["text_rule", "vision_rule", "hybrid_rule", "layout", "roles"]
PURPOSES: tuple[str, ...] = ("text_rule", "vision_rule", "hybrid_rule", "layout", "roles")


@dataclass(frozen=True)
class LlmTarget:
    model: str
    effort: str | None = None  # None: the model's own default


@dataclass(frozen=True)
class RoutingOverrides:
    """A comparison variant's departures from the defaults. Each mapping holds ``model`` and/or ``effort``."""

    defaults: dict[str, Any] = field(default_factory=dict)
    stages: dict[str, dict[str, Any]] = field(default_factory=dict)
    rule_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    ignore_rule_overrides: bool = False
    prompt_cache: bool | None = None  # None: app.yaml pipeline.prompt_cache


def _setting_defaults(settings: Settings, purpose: str) -> dict[str, Any]:
    text = settings.CLAUDE_TEXT_MODEL
    return {
        "text_rule": {"model": text, "effort": settings.TEXT_RULE_EFFORT},
        "hybrid_rule": {"model": text, "effort": settings.TEXT_RULE_EFFORT},
        "vision_rule": {"model": settings.CLAUDE_VISION_MODEL or text, "effort": settings.VISION_RULE_EFFORT},
        "layout": {"model": text, "effort": settings.LAYOUT_MAPPING_EFFORT},
        "roles": {"model": text, "effort": settings.ROLE_ASSIGNMENT_EFFORT},
    }[purpose]


def _pick(name: str, layers: list[dict[str, Any]]) -> Any:
    for layer in layers:
        if name in layer and layer[name] not in (None, ""):
            return layer[name]
    return None


class LlmRouter:
    def __init__(self, settings: Settings | None = None, app_config: AppYaml | None = None,
                 overrides: RoutingOverrides | None = None, cache_salt: str = "") -> None:
        self.settings = settings or get_settings()
        self._app_config = app_config
        self.overrides = overrides or RoutingOverrides()
        self.cache_salt = cache_salt
        self._text: dict[str, Any] = {}
        self._vision: dict[str, Any] = {}
        self._lock = threading.Lock()

    @property
    def app_config(self) -> AppYaml:
        if self._app_config is None:
            self._app_config = load_app_yaml()
        return self._app_config

    # -- targets -------------------------------------------------------------------------------

    def _resolve(self, purpose: str, rule: dict | None) -> LlmTarget:
        o = self.overrides
        layers: list[dict[str, Any]] = []
        if rule is not None:
            layers.append(o.rule_overrides.get(rule.get("id", ""), {}))
            if not o.ignore_rule_overrides:
                layers.append({"model": rule.get("model"), "effort": rule.get("effort")})
        layers += [o.stages.get(purpose, {}), o.defaults, _setting_defaults(self.settings, purpose)]
        model = _pick("model", layers)
        effort = normalize_effort(_pick("effort", layers))
        spec = get_model(model)
        if spec is not None and not spec.effort:
            effort = None
        return LlmTarget(model=model, effort=effort)

    def rule_target(self, rule: dict, purpose: str) -> LlmTarget:
        return self._resolve(purpose, rule)

    def stage_target(self, purpose: str) -> LlmTarget:
        return self._resolve(purpose, None)

    def prompt_cache(self, configured: bool) -> bool:
        return configured if self.overrides.prompt_cache is None else self.overrides.prompt_cache

    # -- providers -----------------------------------------------------------------------------

    def text_provider(self, model: str):
        """One provider per model, built on first use. Raises ValueError when no API key is set."""
        with self._lock:
            if model not in self._text:
                from src.providers.text.factory import build_text_provider

                self._text[model] = build_text_provider(self.settings, model=model, cache_salt=self.cache_salt)
            return self._text[model]

    def vision_provider(self, model: str):
        with self._lock:
            if model not in self._vision:
                from src.providers.vision.factory import build_vision_provider

                self._vision[model] = build_vision_provider(self.app_config, self.settings, model=model,
                                                            cache_salt=self.cache_salt)
            return self._vision[model]


class FixedProviderRouter(LlmRouter):
    """Routes every call to injected providers (test fakes) while still resolving targets, so the
    effort and model a call would use can be asserted without an API key."""

    def __init__(self, text_provider=None, vision_provider=None, settings: Settings | None = None,
                 overrides: RoutingOverrides | None = None) -> None:
        super().__init__(settings=settings, overrides=overrides)
        self._fixed_text = text_provider
        self._fixed_vision = vision_provider

    def text_provider(self, model: str):
        if self._fixed_text is None:
            return super().text_provider(model)
        return self._fixed_text

    def vision_provider(self, model: str):
        if self._fixed_vision is None:
            return super().vision_provider(model)
        return self._fixed_vision
