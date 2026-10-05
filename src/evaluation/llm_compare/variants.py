"""Comparison variants: named departures from the default models and effort levels.

    baseline: current
    variants:
      current:
        description: Production defaults
      sonnet-5-5-medium:
        defaults: {effort: medium}                   # every purpose
        stages:                                      # one purpose: text_rule | vision_rule | hybrid_rule
          layout: {effort: medium}                   #              | layout | roles
        rule_overrides:                              # one rule; beats the rule's own model/effort fields
          SOI-PERCENTAGE-TIE: {effort: high}
        ignore_rule_overrides: false                 # true: ignore the model/effort fields in the rule files
        prompt_cache: true                           # false: send no cache markers (measures what caching saves)

Unset fields fall through to the settings defaults, as in production (see src/providers/router.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.providers.models import get_model, model_registry, normalize_effort
from src.providers.router import PURPOSES, RoutingOverrides

_KEYS = {"description", "defaults", "stages", "rule_overrides", "ignore_rule_overrides", "prompt_cache"}


@dataclass(frozen=True)
class Variant:
    name: str
    description: str = ""
    overrides: RoutingOverrides = field(default_factory=RoutingOverrides)

    def to_dict(self) -> dict[str, Any]:
        o = self.overrides
        return {"name": self.name, "description": self.description, "defaults": o.defaults, "stages": o.stages,
                "rule_overrides": o.rule_overrides, "ignore_rule_overrides": o.ignore_rule_overrides,
                "prompt_cache": o.prompt_cache}


def _target_problems(where: str, target: Any) -> list[str]:
    if not isinstance(target, dict):
        return [f"{where}: expected a mapping with model and/or effort"]
    problems = [f"{where}: unknown key {k!r}" for k in target if k not in ("model", "effort")]
    model = target.get("model")
    if model is not None and get_model(model) is None:
        problems.append(f"{where}: model {model!r} is not in config/models.yaml "
                        f"(known: {', '.join(sorted(model_registry()))})")
    try:
        effort = normalize_effort(target.get("effort"))
    except ValueError as exc:
        problems.append(f"{where}: {exc}")
        effort = None
    if effort and model and get_model(model) and not get_model(model).effort:
        problems.append(f"{where}: model {model!r} does not accept an effort setting")
    return problems


def parse_variant(name: str, raw: dict[str, Any] | None) -> Variant:
    raw = raw or {}
    problems = [f"{name}: unknown key {k!r}" for k in raw if k not in _KEYS]
    problems += _target_problems(f"{name}.defaults", raw.get("defaults") or {})
    stages = raw.get("stages") or {}
    for purpose, target in stages.items():
        if purpose not in PURPOSES:
            problems.append(f"{name}.stages: unknown purpose {purpose!r} (expected one of {', '.join(PURPOSES)})")
        problems += _target_problems(f"{name}.stages.{purpose}", target)
    rule_overrides = raw.get("rule_overrides") or {}
    for rule_id, target in rule_overrides.items():
        problems += _target_problems(f"{name}.rule_overrides.{rule_id}", target)
    if problems:
        raise ValueError("Invalid variant definition:\n  " + "\n  ".join(problems))
    return Variant(name=name, description=str(raw.get("description") or ""), overrides=RoutingOverrides(
        defaults=dict(raw.get("defaults") or {}), stages={k: dict(v) for k, v in stages.items()},
        rule_overrides={k: dict(v) for k, v in rule_overrides.items()},
        ignore_rule_overrides=bool(raw.get("ignore_rule_overrides", False)), prompt_cache=raw.get("prompt_cache"),
    ))


def load_variants(path: str | Path) -> tuple[str, dict[str, Variant]]:
    """(baseline variant name, variants by name) from a variants YAML."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    variants = {name: parse_variant(name, raw) for name, raw in (data.get("variants") or {}).items()}
    if not variants:
        raise ValueError(f"{path}: no variants defined")
    baseline = data.get("baseline") or next(iter(variants))
    if baseline not in variants:
        raise ValueError(f"{path}: baseline {baseline!r} is not one of the variants")
    return baseline, variants
