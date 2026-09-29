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
from src.pipeline.workbook.checks._common import event_number, quarter_key
from src.pipeline.workbook.extract import WorkbookData
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
        f"base the verdict on them, using the sheet excerpts below only for context) for {rule_id}:\n"
        + json.dumps(facts, indent=1, default=str)
        + "\n"
    )


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


@_facts("CE-WB-MERGE-TABS")
def _merge_tabs(model, data, options):
    label = data.allocation.event.label if data.allocation else None
    tabs = []
    for merge in data.merges:
        rows = merge.rows
        names = [r.file_name for r in rows if is_text(r.file_name)]
        expected_suffix = f"_{label}" if label else None
        tabs.append({
            "sheet": merge.sheet.name,
            "vehicle": merge.vehicle,
            "investor_rows": len(rows),
            "has_file_name_column": merge.layout.columns.file_name is not None,
            "rows_missing_investor_id": [r.name for r in rows if r.investor_id in (None, "")],
            "rows_missing_fund_id": [r.name for r in rows if r.fund_id in (None, "")],
            "file_names_sample": names[:5],
            "file_names_not_starting_with_own_ids": [
                r.file_name for r in rows if is_text(r.file_name)
                and not str(r.file_name).startswith(f"{r.fund_id}_{r.investor_id}_")][:10],
            "file_names_without_current_event_label": [
                n for n in names if expected_suffix and label not in str(n)][:10],
        })
    return {
        "vehicles_on_allocation": [v.name for v in data.allocation.vehicles] if data.allocation else [],
        "current_event_label": label,
        "merge_tabs": tabs,
    }


@_facts("CE-WB-NO-PLACEHOLDERS")
def _placeholders(model, data, options):
    placeholders, tbd = [], []
    for name in data.processed_sheets:
        sheet = model.sheet(name)
        for cell in sheet.cells.values():
            if not isinstance(cell.value, str):
                continue
            text = cell.value.strip()
            if _PLACEHOLDER_RE.search(text):
                placeholders.append({"sheet": name, "cell": cell.coord, "text": text[:120]})
            if _TBD_RE.search(text):
                tbd.append({"sheet": name, "cell": cell.coord, "text": text[:120]})
    placeholder_tabs = [s.name for s in model.sheets if re.fullmatch(r"\s*\{[^}]*\}\s*", s.name)]
    return {"placeholders": placeholders, "tbd_cells": tbd, "placeholder_sheet_names": placeholder_tabs,
            "scanned_sheets": data.processed_sheets}


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


@_facts("CE-ALLOC-COMPONENT-PARTICIPATION")
def _participation(model, data, options):
    alloc = data.allocation
    if alloc is None:
        return {}
    fee_flags = {r.name: r.affiliate_flag for r in data.mgmt_fee.rows} if data.mgmt_fee else {}
    components = []
    for comp in alloc.active_components:
        amounts = [(i, i.amounts.get(comp.column, ZERO)) for i in alloc.investors]
        components.append({
            "column": comp.column,
            "header": comp.header,
            "component_type": comp.component_type,
            "participating_investors": sum(1 for _, a in amounts if a),
            "gp_amounts": {i.name: _num(a) for i, a in amounts if i.is_gp},
            "affiliate_amounts": {i.name: _num(a) for i, a in amounts
                                  if i.affiliate or str(fee_flags.get(i.name, "")).upper() == "Y"},
            "lps_at_zero_while_peers_participate": [i.name for i, a in amounts if not i.is_gp and not a and i.commitment]
            if any(a for _, a in amounts) else [],
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
        if comp in alloc.active_components:
            active.append(entry)
        else:
            number = event_number(comp.header)
            entry["header_names_other_event"] = bool(number and current and number != event_number(current))
            inactive.append(entry)
    return {"current_event_label": current, "active_components": active, "inactive_components": inactive}


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

    columns = []
    for comp in block.components:
        populated = sum(1 for i in itd.investors if i.values.get(comp.column))
        columns.append({
            "column": comp.column,
            "header": itd.sheet.value(f"{comp.column}{itd.layout.subheader_row}"),
            "component_type": comp.component_type,
            "classifications": itd.marks.get(comp.column, []),
            "overlays": itd.overlay_marks.get(comp.column, []),
            "populated_investors": populated,
        })
    alloc_participants = sum(1 for i in data.allocation.investors
                             if any(i.amounts.get(c.column) for c in data.allocation.active_components)) \
        if data.allocation else None
    prior_last = max((ci(b.total_column or b.last_column) for b in itd.prior_blocks), default=0)
    return {
        "current_block": {"label": block.label, "first_column": block.first_column, "columns": columns},
        "label_is_unique": labels.count(block.label) == 1,
        "appended_after_prior_blocks": ci(block.first_column) > prior_last,
        "event_labels": labels,
        "overlay_rows": [o.name for o in itd.layout.overlay_rows],
        "participating_investors_on_allocation": alloc_participants,
    }


@_facts("CE-TIE-SUMMARY")
def _tie_summary(model, data, options):
    summary, alloc = data.summary, data.allocation
    if summary is None or alloc is None:
        return {}
    grand_commitment = alloc.grand_totals.get(alloc.layout.columns.commitment)
    by_type: dict[str, Decimal] = {}
    for comp in alloc.active_components:
        by_type[comp.component_type] = by_type.get(comp.component_type, ZERO) + \
            sum((v.totals.get(comp.column, ZERO) for v in alloc.vehicles), ZERO)
    lines = [{"label": l.label, "cell": l.amount_cell, "component_type": l.component_type, "amount": _num(l.amount)}
             for l in summary.lines]
    summary_by_type: dict[str, Decimal] = {}
    for l in summary.lines:
        summary_by_type[l.component_type] = summary_by_type.get(l.component_type, ZERO) + l.amount
    return {
        "summary_total_commitments": _num(summary.fund_commitment),
        "allocation_total_commitments": _num(grand_commitment),
        "summary_lines": lines,
        "allocation_component_totals_by_type": {k: _num(v) for k, v in by_type.items()},
        "differences_by_type": {k: _num(summary_by_type.get(k, ZERO) - by_type.get(k, ZERO))
                                for k in set(by_type) | set(summary_by_type)},
        "summary_event_total": _num(summary.event_total),
        "allocation_event_gross": _num(alloc.event_gross),
        "summary_check_values": [_num(v) for v in summary.check_values],
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
    fee_periods: list[str] = []
    if alloc is not None:
        fee_periods += [quarter_key(c.header) for c in alloc.active_components if c.component_type == "mgmt_fee"]
    fee = data.mgmt_fee
    if fee is not None:
        for index, label in enumerate(fee.period_labels):
            refs.append({"sheet": fee.sheet.name, "cell": f"{fee.layout.fee_columns[index].column}{fee.layout.header_row}",
                         "kind": "fee_period", "value": label})
            fee_periods.append(quarter_key(label))
    numbers = sorted({n for r in refs if r["kind"] == "event_label" for n in [event_number(r["value"])] if n is not None})
    return {
        "references": refs,
        "event_numbers": numbers,
        "notice_dates": sorted({r["value"] for r in refs if r["kind"] == "notice_date"}),
        "due_dates": sorted({r["value"] for r in refs if r["kind"] in ("due_date", "header_date")}),
        "fee_periods": sorted({p for p in fee_periods if p}),
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
    for inv in alloc.investors:
        c = sum((inv.amounts.get(x.column, ZERO) for x in call), ZERO)
        d = sum((inv.amounts.get(x.column, ZERO) for x in dist), ZERO)
        nets.append({"investor": inv.name, "call_side": _num(c), "distribution_side": _num(d),
                     "net_owed": _num(c - abs(d))})
    call_total = sum((alloc.fund_drivers.get(x.column, ZERO) for x in call), ZERO)
    dist_total = sum((alloc.fund_drivers.get(x.column, ZERO) for x in dist), ZERO)
    cash_col = alloc.layout.columns.cash_due
    return {
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
        "investors_in_register": len(alloc.investors),
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
