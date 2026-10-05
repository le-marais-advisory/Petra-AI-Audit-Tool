"""LLM layout mapping: skeleton in, validated SheetLayout out.

One structured-output call per relevant sheet. The result is checked with
``validate_layout``; on issues the mapper re-prompts once with the validator's
findings. A truncated answer (the model spent its token budget reasoning) is
retried one effort level lower and does not use up a validation attempt.
Accepted layouts are cached by (workbook SHA-256, sheet, role).
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from src.core.llm_usage import ContextThreadPoolExecutor
from src.pipeline.workbook.layout import from_llm_output, layout_json_schema, parse_layout
from src.pipeline.workbook.layout_validator import LayoutIssue, validate_layout
from src.pipeline.workbook.loader import WorkbookModel
from src.pipeline.workbook.skeleton import build_skeleton
from src.providers.errors import TruncatedResponseError

logger = logging.getLogger("petra.pipeline")

_PROMPT_PATH = Path(__file__).resolve().parents[3] / "config" / "workbook_layout_system_prompt.md"
_CACHE: dict[tuple[str, str, str], BaseModel] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_LIMIT = 256


# None is the model default, which is high on the current Claude models.
_LOWER_EFFORT: dict[str | None, str | None] = {None: "medium", "high": "medium", "medium": "low", "low": None}


def _system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _workbook_hash(model: WorkbookModel) -> str:
    try:
        return hashlib.sha256(model.path.read_bytes()).hexdigest()
    except OSError:
        return model.file_name


def _user_prompt(model: WorkbookModel, sheet_name: str, role: str, event_type: str | None) -> str:
    event = f" The user says this workbook is for a {event_type.replace('_', ' ')}." if event_type else ""
    return (
        f"Map the layout of sheet {json.dumps(sheet_name)}, which has the role '{role}'.{event}\n"
        f"Set role to '{role}' and sheet to {json.dumps(sheet_name)}.\n\n"
        f"{build_skeleton(model.sheet(sheet_name))}"
    )


def _retry_prompt(first_prompt: str, previous: dict[str, Any] | None, problems: list[str]) -> str:
    return (
        f"{first_prompt}\n\nYour previous answer was:\n{json.dumps(previous, indent=1) if previous else '(invalid)'}\n\n"
        "A validator compared it with the actual cells and reported:\n"
        + "\n".join(f"- {p}" for p in problems)
        + "\nReturn a corrected layout."
    )


def _drop_none(raw: Any) -> Any:
    """Remove None values so model defaults (empty lists, empty sub-objects) apply."""
    if isinstance(raw, dict):
        return {k: _drop_none(v) for k, v in raw.items() if v is not None}
    if isinstance(raw, list):
        return [_drop_none(v) for v in raw]
    return raw


def map_sheet_layout(
    model: WorkbookModel,
    sheet_name: str,
    role: str,
    provider=None,
    event_type: str | None = None,
    max_attempts: int = 2,
    use_cache: bool = True,
    effort: str | None = None,
) -> tuple[BaseModel | None, list[LayoutIssue]]:
    if provider is None or effort is None:
        from src.core.config import get_settings

        settings = get_settings()
        if effort is None:
            effort = settings.LAYOUT_MAPPING_EFFORT or None
        if provider is None:
            from src.providers.text.factory import build_text_provider

            provider = build_text_provider(settings)
    effort = effort or None  # "" is the model default
    # Different models or efforts can map a sheet differently, so they never share a cached layout.
    key = (_workbook_hash(model), sheet_name, role, getattr(provider, "model", None), effort)
    if use_cache:
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
        if cached is not None:
            return cached, []
    schema = layout_json_schema(role)
    system = _system_prompt()
    first = _user_prompt(model, sheet_name, role, event_type)
    prompt = first
    issues: list[LayoutIssue] = []
    attempt = 0
    while attempt < max_attempts:
        try:
            raw = provider.complete_structured(system, prompt, schema, name=f"{role}_layout", effort=effort)
        except TruncatedResponseError as exc:
            lower = _LOWER_EFFORT.get(effort)
            if lower is None:
                raise
            logger.warning("Layout for %s truncated at effort=%s, retrying at %s: %s",
                           sheet_name, effort or "default", lower, exc)
            effort = lower
            continue
        attempt += 1
        raw = {**_drop_none(from_llm_output(raw)), "role": role, "sheet": sheet_name}
        try:
            layout = parse_layout(raw)
        except ValidationError as exc:
            issues = [LayoutIssue("schema_error", str(exc)[:500], sheet_name)]
            prompt = _retry_prompt(first, raw, [issues[0].message])
            continue
        issues = validate_layout(model, layout)
        if not issues:
            if use_cache:
                with _CACHE_LOCK:
                    if len(_CACHE) >= _CACHE_LIMIT:
                        _CACHE.pop(next(iter(_CACHE)))
                    _CACHE[key] = layout
            return layout, []
        logger.info("Layout for %s rejected (attempt %d): %s", sheet_name, attempt, [i.code for i in issues])
        prompt = _retry_prompt(first, layout.model_dump(mode="json"), [f"{i.code}: {i.message}" for i in issues])
    return None, issues


def map_layouts_with_issues(
    model: WorkbookModel,
    roles: dict[str, str],
    provider=None,
    event_type: str | None = None,
    max_workers: int = 6,
) -> tuple[dict[str, BaseModel], dict[str, list[LayoutIssue]]]:
    mappable = {s: r for s, r in roles.items() if r != "other"}
    layouts: dict[str, BaseModel] = {}
    failures: dict[str, list[LayoutIssue]] = {}
    with ContextThreadPoolExecutor(max_workers=max(1, min(max_workers, len(mappable) or 1))) as pool:
        futures = {s: pool.submit(map_sheet_layout, model, s, r, provider, event_type) for s, r in mappable.items()}
        for sheet, future in futures.items():
            try:
                layout, issues = future.result()
            except Exception as exc:
                logger.exception("Layout mapping failed for %s", sheet)
                layout, issues = None, [LayoutIssue("mapping_error", f"{type(exc).__name__}: {exc}", sheet)]
            if layout is not None:
                layouts[sheet] = layout
            else:
                failures[sheet] = issues
    return layouts, failures


def map_layouts(model: WorkbookModel, roles: dict[str, str], provider=None,
                event_type: str | None = None) -> dict[str, BaseModel]:
    layouts, _ = map_layouts_with_issues(model, roles, provider=provider, event_type=event_type)
    return layouts
