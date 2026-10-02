"""Assign each sheet a role (allocation, itd, summary, merge, ...).

``propose_roles`` is a name/header heuristic. ``assign_roles`` asks the LLM to confirm
or correct the proposal from the inventory in a single small call, since real
workbooks name their tabs inconsistently (e.g. a Merge tab named after the fund).
Hidden sheets are treated as legacy and never get a primary role.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Callable

from src.pipeline.workbook.inventory import SheetInventoryEntry, build_inventory
from src.pipeline.workbook.loader import WorkbookModel

logger = logging.getLogger("petra.pipeline")

ROLES = ("allocation", "itd", "summary", "merge", "mgmt_fee", "investor_data", "holiday_calendar", "other")
SINGLETON_ROLES = {"allocation", "itd", "summary", "mgmt_fee", "investor_data", "holiday_calendar"}


def _has(text: str, *needles: str) -> bool:
    return all(n.lower() in text for n in needles)


def _score(entry: SheetInventoryEntry) -> dict[str, int]:
    name = entry.name.lower().strip()
    text = entry.preview_text().lower()
    scores = {role: 0 for role in ROLES}
    # Name signals.
    if "allocation" in name and "investran" not in name:
        scores["allocation"] += 5
    if re.search(r"\bitd\b", name) or "inception" in name:
        scores["itd"] += 5
    if "summary" in name:
        scores["summary"] += 5
    if "merge" in name:
        scores["merge"] += 5
    if "holiday" in name:
        scores["holiday_calendar"] += 5
    if name in ("mf", "mgmt fee", "mgmt fees") or "mgmt fee" in name or "management fee" in name:
        scores["mgmt_fee"] += 5
    if "investor data" in name:
        scores["investor_data"] += 5
    # Header / content signals.
    if _has(text, "file name") and ("investor id" in text) and ("fund id" in text):
        scores["merge"] += 4
    linked = entry.link_share >= 0.5
    if _has(text, "investor id", "check") and linked and "file name" not in text:
        # A notice-data tab without file names: rows of links into the Allocation, an ID column and
        # a check column (the reference client's "<fund> Merge" tabs, one of them without "Merge").
        scores["merge"] += 4
    if _has(text, "investor name", "investor id", "fund id") and "file name" not in text:
        scores["investor_data"] += 4
    if _has(text, "investment contributions", "cost contributions") and "capital call #" in text:
        scores["itd"] += 4
    if _has(text, "prior capital contributions") and ("commitment %" in text or "% of fund" in text) \
            and "file name" not in text and not linked:
        scores["allocation"] += 4
    if ("total fund commitments" in text or "total commitments" in text) and (
            "current capital call:" in text or "current distribution:" in text):
        scores["summary"] += 4
    if "management fee calculation" in text or (_has(text, "affiliate?") and "mgmt fee" in text):
        scores["mgmt_fee"] += 4
    if _has(text, "holiday", "date") and entry.formula_count == 0 and len(entry.header_preview) <= 3:
        scores["holiday_calendar"] += 3
    return scores


def _hidden_merge(entry: SheetInventoryEntry, scores: dict[str, int]) -> bool:
    """A hidden sheet that is a Merge tab by name or by its headers (and nothing else)."""
    if entry.state == "veryHidden":
        return False
    best = max(scores.items(), key=lambda rs: rs[1])
    return scores["merge"] >= 3 and best[0] == "merge"


def propose_roles(model_or_inventory: WorkbookModel | list[SheetInventoryEntry]) -> dict[str, str]:
    inventory = build_inventory(model_or_inventory) if isinstance(model_or_inventory, WorkbookModel) \
        else model_or_inventory
    roles: dict[str, str] = {}
    best: dict[str, tuple[int, str]] = {}
    for entry in inventory:
        if entry.is_empty:
            roles[entry.name] = "other"
            continue
        scores = _score(entry)
        if entry.state != "visible":
            # Hidden sheets are legacy working sheets (FA calibration), except a Merge tab: clients
            # hide the notice-data tabs once the notices are generated, and they are still the
            # data the notices were built from.
            if _hidden_merge(entry, scores):
                roles[entry.name] = "merge"
            else:
                roles[entry.name] = "other"
            continue
        role, score = max(((r, s) for r, s in scores.items() if r != "other"), key=lambda rs: rs[1])
        if score < 3:
            roles[entry.name] = "other"
            continue
        roles[entry.name] = role
        if role in SINGLETON_ROLES:
            previous = best.get(role)
            if previous is None or score > previous[0]:
                if previous is not None:
                    roles[previous[1]] = "other"
                best[role] = (score, entry.name)
            else:
                roles[entry.name] = "other"
    return roles


RoleAssigner = Callable[[WorkbookModel, list[SheetInventoryEntry], dict[str, str]], dict[str, str]]

_ROLE_SYSTEM_PROMPT = """You classify the sheets of a private-fund capital-event workbook by role.
Roles:
- allocation: per-investor allocation of the current event (commitments, commitment %, component columns, roll-forward)
- itd: inception-to-date capital activity, one column block per past event with an X classification band
- summary: one-page event summary (total commitments, component totals, check cell)
- merge: per-investor mail-merge / notice data (investor IDs, fund IDs, file names); one per vehicle, often named after the vehicle without the word "Merge", and sometimes hidden once the notices are generated
- mgmt_fee: management fee calculation tab
- investor_data: investor register export from the document system (investor names and IDs)
- holiday_calendar: bank holiday list used to compute due dates
- other: anything else (notes, trackers, org charts, legacy or hidden working sheets)
At most one sheet each for allocation, itd, summary, mgmt_fee, investor_data and holiday_calendar.
Hidden sheets are legacy and must be 'other', except a hidden Merge tab, which keeps the role 'merge'. Return JSON only."""


def _role_schema(sheet_names: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "roles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sheet": {"type": "string", "enum": sheet_names},
                        "role": {"type": "string", "enum": list(ROLES)},
                    },
                    "required": ["sheet", "role"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["roles"],
        "additionalProperties": False,
    }


def llm_role_assigner(provider=None) -> RoleAssigner:
    def assign(model: WorkbookModel, inventory: list[SheetInventoryEntry], proposed: dict[str, str]) -> dict[str, str]:
        from src.core.config import get_settings
        from src.providers.text.factory import build_text_provider

        active = provider or build_text_provider(get_settings())
        lines = []
        for entry in inventory:
            preview = " | ".join(" / ".join(row[:10]) for row in entry.header_preview[:8])
            lines.append(
                f"- sheet {json.dumps(entry.name)} state={entry.state} range={entry.dimensions} "
                f"formulas={entry.formula_count} proposed={proposed.get(entry.name, 'other')}\n  preview: {preview[:600]}"
            )
        user = "Confirm or correct the proposed role for every sheet.\n\n" + "\n".join(lines)
        names = [e.name for e in inventory]
        result = active.complete_structured(_ROLE_SYSTEM_PROMPT, user, _role_schema(names), name="sheet_roles")
        assigned = {item["sheet"]: item["role"] for item in result.get("roles", []) if item.get("sheet") in names}
        merged = {name: assigned.get(name, proposed.get(name, "other")) for name in names}
        for entry in inventory:
            if entry.state != "visible" and merged.get(entry.name) != "merge":
                merged[entry.name] = "other"
        return _enforce_singletons(merged, proposed)

    return assign


def _enforce_singletons(roles: dict[str, str], proposed: dict[str, str]) -> dict[str, str]:
    for role in SINGLETON_ROLES:
        holders = [name for name, r in roles.items() if r == role]
        if len(holders) > 1:
            keep = next((n for n in holders if proposed.get(n) == role), holders[0])
            for name in holders:
                if name != keep:
                    roles[name] = "other"
    return roles


def assign_roles(model: WorkbookModel, assigner: RoleAssigner | None = None) -> dict[str, str]:
    inventory = build_inventory(model)
    proposed = propose_roles(inventory)
    assigner = assigner or llm_role_assigner()
    try:
        return assigner(model, inventory, proposed)
    except Exception:
        logger.exception("Role confirmation failed; falling back to the heuristic proposal")
        return proposed
