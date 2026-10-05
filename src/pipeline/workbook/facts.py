"""Facts pre-pass for hybrid (LLM-judged) capital-event rules.

The LLM judges; code does the reading and arithmetic. For each hybrid rule this
module computes the inventories, sums and cross-references the judgment needs, and
renders them as a COMPUTED FACTS block that goes into the prompt ahead of the sheet
skeletons.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any, Callable

from src.pipeline.workbook.cells import is_text, to_date
from src.pipeline.workbook.checks._common import event_number, event_numbers, quarter_key, quarter_range
from src.pipeline.workbook.extract import WorkbookData
from src.pipeline.workbook.keys import Matcher
from src.pipeline.workbook.loader import WorkbookModel

ZERO = Decimal("0")
_PLACEHOLDER_RE = re.compile(r"\{\{?[^{}]+\}\}?|\[[^\[\]]+\]|\bX{3,}(?:\.X+)?\b|\bXXX\.XX\b")
_TBD_RE = re.compile(r"\bTBD\b|\bTBC\b", re.I)
_DATE_IN_LABEL = re.compile(r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})")

FactsBuilder = Callable[[WorkbookModel, WorkbookData, dict], dict[str, Any]]
_BUILDERS: dict[str, FactsBuilder] = {}


def _facts(rule_id: str):
    def register(fn: FactsBuilder) -> FactsBuilder:
        _BUILDERS[rule_id] = fn
        return fn

    return register


def _num(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _roles(data: WorkbookData) -> dict[str, str]:
    return {name: getattr(layout, "role", "other") for name, layout in data.layouts.items()}


def build_facts(rule_id: str, model: WorkbookModel, data: WorkbookData, options: dict | None = None) -> dict[str, Any]:
    builder = _BUILDERS.get(rule_id)
    base = {"event_type": (options or {}).get("event_type"), "processed_sheets": data.processed_sheets}
    if builder is None:
        return base
    return {**base, **builder(model, data, options or {})}


def render_facts(rule_id: str, facts: dict[str, Any]) -> str:
    return (
        "COMPUTED FACTS (derived by code from the workbook's stored values and formulas; treat them as accurate and "
        f"base the verdict on them, using the sheet excerpts above only for context) for {rule_id}:\n"
        + json.dumps(facts, indent=1, default=str)
        + "\n"
    )


def _current_event_references(data: WorkbookData) -> list[str]:
    """Ways a document name may refer to the current event: its label, due date, summary title."""
    refs: list[str] = []
    alloc = data.allocation
    if alloc is not None:
        if alloc.event.label:
            refs.append(alloc.event.label)
        due = alloc.event.due_date
        if due is not None:
            refs += [f"{due:%B} {due.day}, {due.year}", due.isoformat(), f"{due:%m.%d.%Y}", f"{due.month}.{due.day}.{due:%y}"]
    if data.summary is not None and data.summary.title:
        refs.append(data.summary.title)
    return list(dict.fromkeys(refs))


_EXTERNAL_REF_RE = re.compile(r"(?:'((?:[^']|'')+)'|([A-Za-z_][A-Za-z0-9_.]*))!(\$?[A-Z]{1,3}\$?\d*(?::\$?[A-Z]{1,3}\$?\d*)?)")


def _external_refs(formula: str):
    for quoted, bare, target in _EXTERNAL_REF_RE.findall(formula or ""):
        yield (quoted.replace("''", "'") if quoted else bare), target.replace("$", "")


def _referenced_cells(model: WorkbookModel, data: WorkbookData) -> set[tuple[str, str]]:
    """(sheet, cell) targets of single-cell references from the processed sheets."""
    targets: set[tuple[str, str]] = set()
    for name in data.processed_sheets:
        for cell in model.sheet(name).cells.values():
            for sheet, target in _external_refs(cell.formula):
                if ":" not in target and re.fullmatch(r"[A-Z]{1,3}\d+", target):
                    targets.add((sheet, target))
    return targets


def _column_header(model: WorkbookModel, sheet_name: str, column: str, below_row: int | None = None) -> str | None:
    """The nearest text above ``below_row`` in ``column`` (or the first text in the column)."""
    if not model.has_sheet(sheet_name):
        return None
    cells = [c for c in model.sheet(sheet_name).column_cells(column) if is_text(c.value)]
    if below_row is not None:
        cells = [c for c in cells if c.row < below_row]
        return str(cells[-1].value).strip() if cells else None
    return str(cells[0].value).strip() if cells else None


def _link_pattern_exceptions(alloc) -> list[dict[str, Any]]:
    """Investor rows whose links to other tabs differ from the rest of their column.

    Within one Allocation column every investor row should read the same columns of the
    target tab (e.g. SUMIFS over the fee column, keyed by the investor). A row that reads
    another column is a broken pull even when its cached value happens to be right.
    """
    # Patterns are compared within one vehicle block: each vehicle may read its own support tabs.
    by_column: dict[tuple[str, str], dict[int, frozenset]] = {}
    vehicle_of_row = {inv.row: v.name for v in alloc.vehicles for inv in v.investors}
    for cell in alloc.sheet.cells.values():
        if cell.row not in vehicle_of_row:
            continue
        targets = frozenset((sheet, re.match(r"[A-Z]+", target).group()) for sheet, target in _external_refs(cell.formula)
                            if sheet != alloc.sheet.name and re.match(r"[A-Z]+", target))
        if targets:
            key = (vehicle_of_row[cell.row], re.match(r"[A-Z]+", cell.coord).group())
            by_column.setdefault(key, {})[cell.row] = targets
    exceptions = []
    for (_, column), per_row in sorted(by_column.items()):
        if len(per_row) < 3:
            continue
        counts: dict[frozenset, int] = {}
        for targets in per_row.values():
            counts[targets] = counts.get(targets, 0) + 1
        usual, n = max(counts.items(), key=lambda kv: kv[1])
        if n * 2 <= len(per_row):
            continue  # no clear pattern to compare against
        fmt = lambda ts: sorted(f"{s}!{c}" for s, c in ts)
        for row, targets in sorted(per_row.items()):
            if targets != usual:
                exceptions.append({"cell": f"{column}{row}", "reads": fmt(targets), "column_usually_reads": fmt(usual),
                                   "value": _num(alloc.sheet.value(f"{column}{row}"))
                                   if isinstance(alloc.sheet.value(f"{column}{row}"), (int, float, Decimal)) else None})
    return exceptions


@_facts("CE-ALLOC-REFERENCE-INTEGRITY")
def _reference_integrity(model, data, options):
    """Every reference from the Allocation sheet to another tab, with what it points at."""
    alloc = data.allocation
    if alloc is None:
        return {}
    # An event may bill several fee periods at once (e.g. 3Q and 4Q in one call, or a header such as
    # "Q3 2025 - Q3 2026 Mgmt Fees" covering five quarters).
    current_periods = sorted({p for c in alloc.active_components if c.component_type == "mgmt_fee"
                              for p in quarter_range(c.header)})
    roll_forward = {col: name for name, col in alloc.layout.roll_forward.model_dump().items() if col}
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for cell in alloc.sheet.cells.values():
        for sheet_name, target in _external_refs(cell.formula):
            if sheet_name == alloc.sheet.name:
                continue
            column = re.match(r"[A-Z]+", target).group() if re.match(r"[A-Z]+", target) else target
            key = (sheet_name, target if re.fullmatch(r"[A-Z]{1,3}\d+", target) else column)
            entry = grouped.setdefault(key, {"target_sheet": sheet_name, "target_cell": key[1], "source_cells": []})
            entry["source_cells"].append(cell.coord)
    references, mismatches = [], []
    for (sheet_name, target), entry in grouped.items():
        if not model.has_sheet(sheet_name):
            entry["problem"] = "target sheet does not exist"
            references.append(entry)
            continue
        target_sheet = model.sheet(sheet_name)
        single = re.fullmatch(r"([A-Z]{1,3})(\d+)", target)
        column = single.group(1) if single else target
        row = int(single.group(2)) if single else None
        entry["target_value"] = target_sheet.value(target) if single else None
        entry["target_row_labels"] = ([str(c.value) for c in target_sheet.row_cells(row) if is_text(c.value)][:6]
                                      if row else [])
        entry["target_column_header"] = _column_header(model, sheet_name, column, row)
        source_columns = {re.match(r"[A-Z]+", c).group() for c in entry["source_cells"]}
        if not single:
            entry["kind"] = "lookup"  # whole-column range, e.g. INDEX/MATCH keyed by the investor
        elif source_columns & roll_forward.keys():
            entry["kind"] = "roll_forward"
        else:
            entry["kind"] = "figure"
        rf = sorted({roll_forward[c] for c in source_columns if c in roll_forward})
        if rf:
            entry["source_roll_forward_columns"] = rf
        entry["source_cells"] = sorted(entry["source_cells"])[:6] + (
            [f"... {len(entry['source_cells']) - 6} more"] if len(entry["source_cells"]) > 6 else [])
        period = quarter_key(entry["target_column_header"])
        for source_column in sorted(source_columns):
            # A fee column's own header names its period; a link into another period's column is wrong even
            # when that period is also billed in this event (e.g. the 4Q driver reading the 3Q total).
            source_periods = quarter_range(alloc.sheet.value(f"{source_column}{alloc.layout.header_row}"))
            source_period = source_periods[0] if len(source_periods) == 1 else None
            if period and ((current_periods and period not in current_periods)
                           or (source_periods and period not in source_periods)):
                stale = {"source_column": source_column, "source_period": source_period,
                         "target_sheet": sheet_name, "target_column": column, "period": period,
                         "current_periods": current_periods}
                if stale not in mismatches:  # a driver cell and its per-LP column point at the same stale column
                    mismatches.append(stale)
        references.append(entry)
    return {
        "current_event_label": alloc.event.label,
        "current_fee_periods": current_periods,
        "link_pattern_exceptions": _link_pattern_exceptions(alloc),
        "references": sorted(references, key=lambda r: (r["target_sheet"], r["target_cell"])),
        "period_mismatches": mismatches,
        "note": "kind 'lookup' is a per-investor lookup over a whole column (the matched row is the investor's "
                "own row). kind 'roll_forward' feeds an Allocation roll-forward column (prior contributions, prior "
                "recallable, commitment), which is expected to read the cumulative inception-to-date totals of "
                "prior events, so a cumulative ITD total there is correct, not a prior-period figure. Only kind "
                "'figure' references must point at the current event's own figures. period_mismatches (including "
                "a column whose header names one period linking to another period's column) and "
                "link_pattern_exceptions (an investor row reading different target columns than the rest of its "
                "column) are computed by code; each one is a FAIL even when the cached value happens to be right.",
    }


# --- structure ---------------------------------------------------------------------------------


@_facts("CE-WB-SHEETS-PRESENT")
def _sheets_present(model, data, options):
    roles = _roles(data)
    fee_component = bool(data.allocation and any(c.component_type == "mgmt_fee" for c in data.allocation.active_components))
    return {
        "sheets": [{"name": s.name, "state": s.state, "used_range": s.dimensions,
                    "formulas": sum(1 for c in s.cells.values() if c.formula)} for s in model.sheets],
        "roles_found": roles,
        "missing_required_roles": [r for r in ("allocation", "itd", "summary", "merge") if r not in roles.values()],
        "management_fee_component_allocated": fee_component,
        "fee_tab_present": "mgmt_fee" in roles.values() or any("fee" in s.name.lower() for s in model.sheets),
        "investor_data_tab_present": "investor_data" in roles.values(),
        "vehicle_count": len(data.allocation.vehicles) if data.allocation else None,
        "merge_tab_count": len(data.merges),
    }


def _inactive_merge_row(row) -> bool:
    """FA calibration: no commitment and nothing in this event (e.g. a transferred-out LP)."""
    return not (row.commitment or ZERO) and not (row.event_total or ZERO) and not any(row.amounts.values())


@_facts("CE-WB-MERGE-TABS")
def _merge_tabs(model, data, options):
    label = data.allocation.event.label if data.allocation else None
    tabs = []
    for merge in data.merges:
        rows = [r for r in merge.rows if not _inactive_merge_row(r)]
        names = [r.file_name for r in rows if is_text(r.file_name)]
        event_refs = _current_event_references(data)
        tabs.append({
            "sheet": merge.sheet.name,
            "vehicle": merge.vehicle,
            "investor_rows": len(rows),
            "inactive_investors_ignored": [r.name for r in merge.rows if _inactive_merge_row(r)],
            "has_file_name_column": merge.layout.columns.file_name is not None,
            "rows_missing_investor_id": [r.name for r in rows if r.investor_id in (None, "")],
            "rows_missing_fund_id": [r.name for r in rows if r.fund_id in (None, "")],
            "file_names_sample": names[:5],
            "file_names_not_starting_with_own_ids": [
                r.file_name for r in rows if is_text(r.file_name)
                and not str(r.file_name).startswith(f"{r.fund_id}_{r.investor_id}_")][:10],
            "file_names_without_current_event_reference": [
                n for n in names if event_refs and not any(ref.lower() in str(n).lower() for ref in event_refs)][:10],
        })
    return {
        "vehicles_on_allocation": [v.name for v in data.allocation.vehicles] if data.allocation else [],
        "current_event_label": label,
        "current_event_references": _current_event_references(data),
        "merge_tabs": tabs,
        "note": "DX Fund ID / DX Investor ID are the Fund ID / Investor ID. A file name must start with the row's own "
                "Fund ID and Investor ID and reference the current event (its label or due date). Inactive investors "
                "(no commitment and nothing in this event, e.g. transferred out) are excluded from every list above "
                "and must not affect the verdict, even if their rows show errors such as #N/A.",
    }


@_facts("CE-WB-NO-PLACEHOLDERS")
def _placeholders(model, data, options):
    placeholders, tbd = [], []
    referenced = _referenced_cells(model, data)
    for name in data.scanned_sheets:
        sheet = model.sheet(name)
        for cell in sheet.cells.values():
            if not isinstance(cell.value, str):
                continue
            text = cell.value.strip()
            if _PLACEHOLDER_RE.search(text):
                placeholders.append({"sheet": name, "cell": cell.coord, "text": text[:120]})
            if _TBD_RE.search(text):
                row_labels = [str(c.value) for c in sheet.row_cells(cell.row) if isinstance(c.value, str)][:6]
                tbd.append({"sheet": name, "cell": cell.coord, "text": text[:120], "row_labels": row_labels,
                            "referenced_by_event_formulas": (name, cell.coord) in referenced})
    placeholder_tabs = [s.name for s in model.sheets if re.fullmatch(r"\s*\{[^}]*\}\s*", s.name)]
    return {"placeholders": placeholders, "tbd_cells": tbd, "placeholder_sheet_names": placeholder_tabs,
            "scanned_sheets": data.scanned_sheets,
            "note": "A 'TBD' on a status or date field that no event formula references (e.g. a wire date awaiting "
                    "cash movement) is legitimately pending, not a placeholder."}


# --- allocation -------------------------------------------------------------------------------


@_facts("CE-ALLOC-FEE-TIERS")
def _fee_tiers(model, data, options):
    alloc = data.allocation
    if alloc is None:
        return {}
    sheet = alloc.sheet
    col = alloc.layout.columns.investor
    tier_headers = []
    for vehicle in alloc.layout.vehicles:
        for row in range(vehicle.investor_rows[0] - 2, vehicle.investor_rows[-1] + 1):
            text = sheet.value(f"{col}{row}")
            if is_text(text) and ("fee" in text.lower() or "affiliate" in text.lower()) and text.strip().endswith(":"):
                tier_headers.append({"row": row, "text": text.strip()})
    rate_col = alloc.layout.columns.mgmt_fee_rate
    fee = data.mgmt_fee
    return {
        "allocation_has_mgmt_fee_rate_column": rate_col is not None,
        "tier_header_rows": tier_headers,
        "inline_rates": [{"investor": i.name, "rate": sheet.value(f"{rate_col}{i.row}"), "is_gp": i.is_gp,
                          "affiliate": i.affiliate} for i in alloc.investors] if rate_col else [],
        "fee_tab_rates": [_num(r) for r in fee.rates] if fee else [],
        "fee_tab_affiliate_flags": {r.name: r.affiliate_flag for r in fee.rows} if fee else {},
    }


def _is_affiliate(inv, fee_flags) -> bool:
    return bool(inv.affiliate) or str(fee_flags.get(inv.name, "")).strip().upper() == "Y"


def _default_zero(comp, inv, fee_flags) -> bool:
    """Whether the rule's default participation explains a $0 (GP/affiliates pay no fee, etc.)."""
    if inv.is_gp and comp.component_type in ("mgmt_fee", "org_expense", "placement_fee"):
        return True
    if comp.component_type == "mgmt_fee" and _is_affiliate(inv, fee_flags):
        return True
    if comp.side == "distribution" and not (inv.distribution_basis or ZERO):
        return True  # never funded: nothing to return or share
    return False


@_facts("CE-ALLOC-COMPONENT-PARTICIPATION")
def _participation(model, data, options):
    alloc = data.allocation
    if alloc is None:
        return {}
    fee_flags = {r.name: r.affiliate_flag for r in data.mgmt_fee.rows} if data.mgmt_fee else {}
    components = []
    for comp in alloc.active_components:
        amounts = [(i, i.amounts.get(comp.column, ZERO)) for i in alloc.investors]
        zeros = [(i, a) for i, a in amounts if not a and i.commitment] if any(a for _, a in amounts) else []
        components.append({
            "column": comp.column,
            "header": comp.header,
            "component_type": comp.component_type,
            "participating_investors": sum(1 for _, a in amounts if a),
            "gp_amounts": {i.name: _num(a) for i, a in amounts if i.is_gp},
            "affiliate_amounts": {i.name: _num(a) for i, a in amounts
                                  if i.affiliate or str(fee_flags.get(i.name, "")).upper() == "Y"},
            "zero_explained_by_default_rules": [i.name for i, a in zeros if _default_zero(comp, i, fee_flags)],
            "zero_without_default_explanation": [i.name for i, a in zeros if not _default_zero(comp, i, fee_flags)],
        })
    return {
        "components": components,
        "affiliates_per_allocation_sheet": [i.name for i in alloc.investors if i.affiliate],
        "affiliates_per_fee_tab": [n for n, f in fee_flags.items() if str(f).strip().upper() == "Y"],
    }


def _event_labels(model: WorkbookModel) -> list[str]:
    return [str(c.value) for s in model.sheets for c in s.cells.values()
            if isinstance(c.value, str) and re.search(r"(capital call|distribution)\s*#\s*\d+", c.value, re.I)]


@_facts("CE-ALLOC-STALE-COMPONENTS")
def _stale(model, data, options):
    alloc = data.allocation
    if alloc is None:
        return {}
    current = alloc.event.label
    active, inactive = [], []
    for comp in alloc.components:
        cells = [i.amounts.get(comp.column, ZERO) for i in alloc.investors]
        entry = {
            "column": comp.column,
            "header": comp.header,
            "driver": _num(alloc.fund_drivers.get(comp.column)),
            "vehicle_totals": [_num(v.totals.get(comp.column)) for v in alloc.vehicles],
            "nonzero_cells": sum(1 for a in cells if a),
        }
        number = event_number(comp.header)
        entry["header_names_other_event"] = bool(number and current and number != event_number(current))
        if comp in alloc.active_components:
            active.append(entry)
        else:
            inactive.append(entry)
    prior_active = []
    if data.prior is not None and data.prior.allocation is not None:
        prior_active = sorted({c.component_type for c in data.prior.allocation.active_components})
    return {"current_event_label": current, "active_components": active, "inactive_components": inactive,
            "component_types_active_in_prior_event": prior_active,
            "note": "A leftover label on an unused (inactive, all-zero) column is fine; it only matters when the "
                    "column carries amounts or is used in this event."}


@_facts("CE-ALLOC-SIGNAGE")
def _signage(model, data, options):
    alloc = data.allocation
    if alloc is None:
        return {}
    columns = []
    for comp in alloc.components:
        values = [(i, i.amounts.get(comp.column, ZERO)) for i in alloc.investors]
        pos = [i.name for i, a in values if a > 0]
        neg = [i.name for i, a in values if a < 0]
        if not pos and not neg:
            continue
        minority = pos if len(pos) < len(neg) else neg
        columns.append({"column": comp.column, "header": comp.header, "side": comp.side,
                        "component_type": comp.component_type, "positive": len(pos), "negative": len(neg),
                        "minority_sign_investors": minority[:10] if pos and neg else [],
                        "driver": _num(alloc.fund_drivers.get(comp.column))})
    return {"event_type_selected_by_user": options.get("event_type"), "event_label": alloc.event.label,
            "columns": columns}


# --- ITD / summary / dates -------------------------------------------------------------------


@_facts("CE-ITD-EVENT-BLOCK")
def _itd_block(model, data, options):
    itd = data.itd
    if itd is None:
        return {}
    block = itd.current_block
    labels = [b.label for b in itd.event_blocks]
    if block is None:
        return {"current_block": None, "event_labels": labels}
    from openpyxl.utils.cell import column_index_from_string as ci

    pairs = {}
    if data.allocation is not None:
        from src.pipeline.workbook.checks.ties import _pair_components

        pairs = dict(_pair_components(itd, block, data.allocation)[0])
    columns = []
    matcher = Matcher(data.allocation.vehicles, itd.vehicles) if data.allocation else None
    for comp in block.components:
        alloc_cols = pairs.get(comp.column) or []
        with_amount, missing = [], []
        if matcher is not None:
            for ordinal, inv in matcher.pairs():
                if any(inv.amounts.get(c) for c in alloc_cols):
                    label = matcher.label(ordinal, inv.name)
                    with_amount.append(label)
                    target = matcher.find(ordinal, inv.name)
                    if not (target and target.values.get(comp.column)):
                        missing.append(label)
        alloc_col = "+".join(alloc_cols) if alloc_cols else None
        columns.append({
            "column": comp.column,
            "header": itd.sheet.value(f"{comp.column}{itd.layout.subheader_row}"),
            "component_type": comp.component_type,
            "classifications": itd.marks.get(comp.column, []),
            "overlays": itd.overlay_marks.get(comp.column, []),
            "allocation_column": alloc_col,
            "investors_with_allocation_amount": len(with_amount),
            "investors_populated_in_itd": sum(1 for i in itd.investors if i.values.get(comp.column)),
            "investors_missing_from_itd_block": missing,
        })
    prior_last = max((ci(b.total_column or b.last_column) for b in itd.prior_blocks), default=0)
    return {
        "current_block": {"label": block.label, "first_column": block.first_column, "columns": columns},
        "columns_without_primary_x": [c["column"] for c in columns if not c["classifications"]],
        "columns_with_multiple_primary_x": [c["column"] for c in columns if len(c["classifications"]) > 1],
        "label_is_unique": labels.count(block.label) == 1,
        "appended_after_prior_blocks": ci(block.first_column) > prior_last,
        "event_labels": labels,
        "overlay_rows": [o.name for o in itd.layout.overlay_rows],
        "note": "Participation differs by component (e.g. affiliates pay no management fee, never-funded investors "
                "receive no return of capital). A column is fully populated when investors_missing_from_itd_block "
                "is empty. Any column listed in columns_without_primary_x or columns_with_multiple_primary_x is a "
                "FAIL.",
    }


@_facts("CE-TIE-SUMMARY")
def _tie_summary(model, data, options):
    summary, alloc = data.summary, data.allocation
    if summary is None or alloc is None:
        return {}
    grand_commitment = alloc.grand_totals.get(alloc.layout.columns.commitment)
    commitment_col = alloc.layout.columns.commitment

    def totals_by_type(vehicles) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for comp in alloc.active_components:
            out[comp.component_type] = out.get(comp.component_type, ZERO) + \
                sum((v.totals.get(comp.column, ZERO) for v in vehicles), ZERO)
        return out

    def lines_of(items):
        return [{"label": l.label, "cell": l.amount_cell, "component_type": l.component_type, "amount": _num(l.amount)}
                for l in items]

    def by_type_of(items) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for l in items:
            out[l.component_type] = out.get(l.component_type, ZERO) + l.amount
        return out

    # Fund level: the additive vehicles only (a look-through block re-allocates the others).
    by_type = totals_by_type(alloc.additive_vehicles)
    summary_by_type = by_type_of(summary.lines)
    per_vehicle = [{
        "vehicle": v.name,
        "additive": v.additive,
        "commitments": _num(v.totals.get(commitment_col)),
        "component_totals_by_type": {k: _num(x) for k, x in totals_by_type([v]).items()},
        "cash_due_total": _num(v.totals.get(alloc.layout.columns.cash_due)) if alloc.layout.columns.cash_due else None,
    } for v in alloc.vehicles]
    sections = []
    for index, section in enumerate(summary.sections):
        # Align each Summary section to an Allocation vehicle by name, else by position.
        vehicle = next((v for v in alloc.vehicles if section.vehicle and v.name and
                        (section.vehicle.strip().lower() in v.name.strip().lower()
                         or v.name.strip().lower() in section.vehicle.strip().lower())), None)
        if vehicle is None and index < len(alloc.vehicles):
            vehicle = alloc.vehicles[index]
        vehicle_types = totals_by_type([vehicle]) if vehicle else {}
        section_types = by_type_of(section.lines)
        sections.append({
            "vehicle": section.vehicle,
            "matched_allocation_vehicle": vehicle.name if vehicle else None,
            "summary_total_commitments": _num(section.fund_commitment),
            "allocation_vehicle_commitments": _num(vehicle.totals.get(commitment_col)) if vehicle else None,
            "summary_lines": lines_of(section.lines),
            "allocation_vehicle_totals_by_type": {k: _num(x) for k, x in vehicle_types.items()},
            "differences_by_type": {k: _num(section_types.get(k, ZERO) - vehicle_types.get(k, ZERO))
                                    for k in set(vehicle_types) | set(section_types)},
            "summary_event_total": _num(section.event_total),
            "allocation_vehicle_cash_due": (_num(vehicle.totals.get(alloc.layout.columns.cash_due))
                                            if vehicle and alloc.layout.columns.cash_due else None),
            "summary_check_values": [_num(v) for v in section.check_values],
        })
    multi = len(alloc.vehicles) > 1
    return {
        "summary_total_commitments": _num(summary.fund_commitment),
        "allocation_total_commitments": _num(grand_commitment),
        "summary_lines": lines_of(summary.lines),
        "allocation_component_totals_by_type": {k: _num(v) for k, v in by_type.items()},
        "differences_by_type": {k: _num(summary_by_type.get(k, ZERO) - by_type.get(k, ZERO))
                                for k in set(by_type) | set(summary_by_type)},
        "summary_event_total": _num(summary.event_total),
        "allocation_event_gross": _num(alloc.event_gross),
        "summary_check_values": [_num(v) for v in summary.check_values],
        "allocation_vehicles": per_vehicle,
        "summary_sections": sections,
        "note": ("The Allocation has several vehicles. A Summary that repeats one block per vehicle ties when each "
                 "section's figures equal its own vehicle's totals (summary_sections[*].differences_by_type all 0); "
                 "the top-level summary_lines then describe the first vehicle only, so the fund-level "
                 "differences_by_type is not a finding. A look-through vehicle (additive=false) is not part of the "
                 "fund total.") if multi else "Single vehicle: the Summary figures must equal the Allocation totals.",
        "summary_title": summary.title,
        "summary_dates": {"notice": str(summary.notice_date) if summary.notice_date else None,
                          "due": str(summary.due_date) if summary.due_date else None},
        "allocation_dates": {"notice": str(alloc.event.notice_date), "due": str(alloc.event.due_date)},
    }


@_facts("CE-DATE-CONSISTENCY")
def _date_consistency(model, data, options):
    refs: list[dict[str, Any]] = []
    alloc = data.allocation
    if alloc is not None:
        layout = alloc.layout
        if layout.event.label_cell:
            refs.append({"sheet": alloc.sheet.name, "cell": layout.event.label_cell, "kind": "event_label",
                         "value": alloc.event.label})
        # On a net event the call and distribution event-total headers each name their own number.
        for total in layout.event_total_columns:
            cell = f"{total.column}{layout.header_row}"
            text = alloc.sheet.value(cell)
            if is_text(text) and event_numbers(text) and cell != layout.event.label_cell:
                refs.append({"sheet": alloc.sheet.name, "cell": cell, "kind": "event_label", "value": str(text).strip(),
                             "side": total.side})
        if layout.event.notice_date_cell:
            refs.append({"sheet": alloc.sheet.name, "cell": layout.event.notice_date_cell, "kind": "notice_date",
                         "value": str(alloc.event.notice_date)})
        if layout.event.due_date_cell:
            refs.append({"sheet": alloc.sheet.name, "cell": layout.event.due_date_cell, "kind": "due_date",
                         "value": str(alloc.event.due_date)})
    itd = data.itd
    if itd is not None and itd.current_block is not None:
        block = itd.current_block
        cell = f"{block.first_column}{itd.layout.event_header_row}"
        refs.append({"sheet": itd.sheet.name, "cell": cell, "kind": "event_label", "value": block.label})
        match = _DATE_IN_LABEL.search(block.label)
        if match:
            day = to_date(match.group(1).replace("-", ".").replace("/", "."))
            refs.append({"sheet": itd.sheet.name, "cell": cell, "kind": "header_date", "value": str(day)})
    summary = data.summary
    if summary is not None:
        if summary.layout.title_cell:
            refs.append({"sheet": summary.sheet.name, "cell": summary.layout.title_cell, "kind": "title",
                         "value": summary.title})
        for kind, cell, value in (("notice_date", summary.layout.notice_date_cell, summary.notice_date),
                                  ("due_date", summary.layout.due_date_cell, summary.due_date)):
            if cell:
                refs.append({"sheet": summary.sheet.name, "cell": cell, "kind": kind, "value": str(value)})
    for merge in data.merges:
        for row in merge.rows[:1]:
            cols = merge.layout.columns
            if cols.file_name:
                refs.append({"sheet": merge.sheet.name, "cell": f"{cols.file_name}{row.row}", "kind": "event_label",
                             "value": row.file_name})
            if cols.letter_date:
                refs.append({"sheet": merge.sheet.name, "cell": f"{cols.letter_date}{row.row}", "kind": "notice_date",
                             "value": str(to_date(row.letter_date, allow_serial=True))})
            if cols.due_date:
                refs.append({"sheet": merge.sheet.name, "cell": f"{cols.due_date}{row.row}", "kind": "due_date",
                             "value": str(to_date(row.due_date, allow_serial=True))})
    alloc_periods: list[str] = []
    linked_periods: dict[str, str | None] = {}
    if alloc is not None:
        from src.pipeline.workbook.checks.ties import linked_fee_periods

        for comp in alloc.active_components:
            if comp.component_type != "mgmt_fee":
                continue
            alloc_periods += quarter_range(comp.header)
            linked_periods.update(linked_fee_periods(model, alloc, comp.column))
    tab_periods: list[str] = []
    fee = data.mgmt_fee
    if fee is not None:
        for index, label in enumerate(fee.period_labels):
            refs.append({"sheet": fee.sheet.name, "cell": f"{fee.layout.fee_columns[index].column}{fee.layout.header_row}",
                         "kind": "fee_period", "value": label})
            tab_periods.append(quarter_key(label))
    alloc_set = {p for p in alloc_periods if p}
    # The periods the Allocation actually pulls (from its fee formulas) decide; the mapped fee tab's
    # column list is the fallback when the fee cells are not linked.
    linked_set = {p for p in linked_periods.values() if p}
    tab_set = linked_set if linked_periods else {p for p in tab_periods if p}
    fee_periods = alloc_set | tab_set
    for ref in refs:
        if ref["kind"] == "event_label":
            ref["numbers_by_family"] = event_numbers(ref["value"])
    numbers = sorted({n for r in refs if r["kind"] == "event_label" for n in [event_number(r["value"])] if n is not None})
    by_family: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        for family, number in ref.get("numbers_by_family", {}).items():
            by_family.setdefault(family, []).append({"sheet": ref["sheet"], "cell": ref["cell"], "number": number})
    return {
        "references": refs,
        "event_numbers": numbers,
        "event_numbers_by_family": by_family,
        "event_numbers_consistent_by_family": {f: len({e["number"] for e in entries}) == 1
                                               for f, entries in by_family.items()},
        "notice_dates": sorted({r["value"] for r in refs if r["kind"] == "notice_date"}),
        "due_dates": sorted({r["value"] for r in refs if r["kind"] in ("due_date", "header_date")}),
        "fee_periods": sorted(fee_periods),
        "fee_periods_by_source": {"allocation_fee_components": sorted(alloc_set), "fee_tab_columns": sorted(tab_set),
                                  "linked_fee_tab_columns": linked_periods},
        # A fee-tab column the Allocation pulls must belong to the Allocation fee header's period(s);
        # the header may name a range of quarters billed together.
        "fee_period_mismatch": bool(alloc_set and tab_set and not tab_set <= alloc_set),
    }


# --- net events / distributions ----------------------------------------------------------------


@_facts("CE-NET-EVENT-STRUCTURE")
def _net_event(model, data, options):
    alloc = data.allocation
    if alloc is None:
        return {}
    call = [c for c in alloc.active_components if c.side == "call"]
    dist = [c for c in alloc.active_components if c.side == "distribution"]
    nets = []
    # Fund level = the additive vehicles; a look-through block re-allocates amounts already counted.
    for inv in alloc.fund_investors:
        c = sum((inv.amounts.get(x.column, ZERO) for x in call), ZERO)
        d = sum((inv.amounts.get(x.column, ZERO) for x in dist), ZERO)
        nets.append({"investor": inv.name, "call_side": _num(c), "distribution_side": _num(d),
                     "net_owed": _num(c - abs(d))})
    call_total = sum((alloc.fund_drivers.get(x.column, ZERO) for x in call), ZERO)
    dist_total = sum((alloc.fund_drivers.get(x.column, ZERO) for x in dist), ZERO)
    cash_col = alloc.layout.columns.cash_due
    per_vehicle = []
    for v in alloc.vehicles:
        v_call = sum((v.driver.get(x.column, ZERO) for x in call), ZERO)
        v_dist = sum((v.driver.get(x.column, ZERO) for x in dist), ZERO)
        v_nets = sum((sum((i.amounts.get(x.column, ZERO) for x in call), ZERO)
                      - abs(sum((i.amounts.get(x.column, ZERO) for x in dist), ZERO)) for i in v.investors), ZERO)
        per_vehicle.append({"vehicle": v.name, "additive": v.additive, "investors": len(v.investors),
                            "call_total": _num(v_call), "distribution_total": _num(v_dist),
                            "expected_net": _num(v_call - abs(v_dist)), "sum_of_per_investor_nets": _num(v_nets),
                            "cash_due_total": _num(v.totals.get(cash_col)) if cash_col else None})
    return {
        "vehicles": per_vehicle,
        "fund_level_scope": ("sum of the additive vehicles' drivers and of their investors' rows"
                             if len(alloc.vehicles) > 1 else "the fund driver row and every investor row"),
        "call_side_columns": [{"column": x.column, "header": x.header} for x in call],
        "distribution_side_columns": [{"column": x.column, "header": x.header} for x in dist],
        "separate_blocks": bool(call and dist) and not ({x.column for x in call} & {x.column for x in dist}),
        "distribution_sign_convention": "negative = paid to investor" if dist_total < 0 else "positive",
        "call_total": _num(call_total),
        "distribution_total": _num(dist_total),
        "sum_of_per_investor_nets": _num(sum((Decimal(str(n["net_owed"])) for n in nets), ZERO)),
        "expected_net": _num(call_total - abs(dist_total)),
        "gross": _num(call_total + abs(dist_total)),
        "cash_due_grand_total": _num(alloc.grand_totals.get(cash_col)) if cash_col else None,
        "investors_in_register": len(alloc.fund_investors),
        "per_investor_nets_sample": nets[:15],
    }


@_facts("CE-DIST-CARRY-SPLIT")
def _carry(model, data, options):
    alloc = data.allocation
    if alloc is None:
        return {}
    carry = [c for c in alloc.active_components if c.component_type == "carry"]
    if not carry:
        return {"carry_component_present": False}
    total = sum((alloc.fund_drivers.get(c.column, ZERO) for c in carry), ZERO)
    gp = sum((i.amounts.get(c.column, ZERO) for c in carry for i in alloc.investors if i.is_gp), ZERO)
    lp = sum((i.amounts.get(c.column, ZERO) for c in carry for i in alloc.investors if not i.is_gp), ZERO)
    rate = alloc.event.carried_interest_rate
    basis_total = sum((abs(i.distribution_basis or ZERO) for i in alloc.investors if not i.is_gp), ZERO)
    residuals = []
    if basis_total:
        for inv in alloc.investors:
            if inv.is_gp:
                continue
            amount = sum((inv.amounts.get(c.column, ZERO) for c in carry), ZERO)
            expected = lp * abs(inv.distribution_basis or ZERO) / basis_total
            if abs(amount - expected) > Decimal("2"):
                residuals.append({"investor": inv.name, "amount": _num(amount), "pro_rata": _num(expected)})
    catch_up = [c.column for c in alloc.active_components if c.component_type == "catch_up"]
    return {
        "carry_component_present": True,
        "carry_total": _num(total),
        "carried_interest_rate": _num(rate),
        "gp_share": _num(gp),
        "expected_gp_share": _num(rate * total) if rate is not None else None,
        "lp_share": _num(lp),
        "gp_plus_lp_equals_total": abs(gp + lp - total) <= Decimal("0.005"),
        "lp_shares_off_pro_rata_by_more_than_2": residuals[:10],
        "catch_up_columns": catch_up,
    }
