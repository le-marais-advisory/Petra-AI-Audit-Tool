"""Cross-sheet tie-outs: management fees, ITD vs Allocation, Merge tabs, investor identity, return of capital.

Investors are matched across sheets by (vehicle, name) through ``keys.Matcher``: a fund with
several vehicles can carry one investor, and always the GP entity, in more than one block.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from src.pipeline.workbook.cells import CENT, PENNY, col_idx, is_text, norm_text, sheet_cell_refs, to_decimal
from src.pipeline.workbook.checks._common import (
    CheckContext,
    NotApplicable,
    Outcome,
    check,
    differs,
    mag,
    money,
    quarter_key,
    quarter_range,
)
from src.pipeline.workbook.checks.support import _criteria, _Lookup, _support_rows, parse_lookup, split_terms
from src.pipeline.workbook.keys import Matcher

ZERO = Decimal("0")


# --- management fees ------------------------------------------------------------------------


@dataclass(frozen=True)
class _FeeSource:
    """One fee-tab column an Allocation fee cell reads: by per-investor lookup, or by direct cell link.

    Compared by value, so the same pattern on every investor row hashes alike."""

    sheet: str
    column: str
    lookup: _Lookup | None

    def key(self) -> tuple:
        return (self.sheet, self.column, self.lookup.keys if self.lookup else None)

    def __repr__(self) -> str:
        return f"{self.sheet}!{self.column}"


def _fee_sources(formula: str | None, row: int, home: str, known: set[str]) -> tuple[tuple, ...] | None:
    """The fee-tab sources of one Allocation fee cell, as a hashable pattern (None if it is not linked)."""
    if not formula:
        return None
    sources: list[_FeeSource] = []
    for term in split_terms(formula):
        lookup = parse_lookup("=" + term, row, home)
        if lookup is not None:
            if lookup.sheet in known and lookup.sheet != home:
                sources.append(_FeeSource(lookup.sheet, lookup.value_column, lookup))
            continue
        refs = [(s, c) for s, c, _ in sheet_cell_refs(term) if s in known and s != home]
        if len(refs) == 1 and not re.search(r"[*/]", term):
            sources.append(_FeeSource(*refs[0], None))
    if not sources:
        return None
    unique = {src.key(): src for src in sources}
    return tuple(unique[key] for key in sorted(unique))


def _column_header(sheet, column: str, below_row: int | None) -> str | None:
    cells = [c for c in sheet.column_cells(column) if is_text(c.value) and (below_row is None or c.row < below_row)]
    return str(cells[-1].value).strip() if cells else None


def _row_of_name(sheet, name: str, key_column: str | None) -> int | None:
    """The row on a support sheet whose key cell holds ``name`` (searching one column, or any)."""
    target = norm_text(name)
    cells = sheet.column_cells(key_column) if key_column else sheet.cells.values()
    for cell in sorted(cells, key=lambda c: (c.column_index, c.row)):
        if is_text(cell.value) and norm_text(cell.value) == target:
            return cell.row
    return None


def fee_links(model, alloc, column: str) -> dict[str, tuple[_FeeSource, ...]]:
    """Per vehicle, the fee-tab source(s) most of its investors' fee cells in ``column`` read.

    Each vehicle usually has its own fee tab(s), so the dominant pattern is found per vehicle. A
    vehicle whose fee cells are not linked (e.g. a look-through block allocating the GP's share by
    percentage) is left out.
    """
    known = {s.name for s in model.sheets}
    links: dict[str, tuple[_FeeSource, ...]] = {}
    for vehicle in alloc.vehicles:
        patterns: Counter = Counter()
        for inv in vehicle.investors:
            pattern = _fee_sources(inv.formulas.get(column), inv.row, alloc.sheet.name, known)
            if pattern:
                patterns[pattern] += 1
        if patterns and patterns.most_common(1)[0][1] * 2 > len(vehicle.investors):
            links[vehicle.name] = patterns.most_common(1)[0][0]
    return links


def linked_fee_periods(model, alloc, column: str) -> dict[str, str | None]:
    """'Sheet!Column' -> quarter key of the header of every fee-tab column the Allocation fee column pulls."""
    out: dict[str, str | None] = {}
    for sources in fee_links(model, alloc, column).values():
        for src in sources:
            tab = model.sheet(src.sheet)
            first_row = src.lookup.rows[0] if src.lookup and src.lookup.rows else None
            out[repr(src)] = quarter_key(_column_header(tab, src.column, first_row) or "")
    return out


@check("CE-TIE-MGMT-FEE", needs=("allocation",))
def tie_mgmt_fee(ctx: CheckContext, out: Outcome) -> str:
    """FA calibration: pull-through only. Each LP's fee equals what the fee tab(s) hold for that LP,
    the fee tab period(s) fall within the Allocation fee header's period, and nothing on the fee
    tab goes unpulled. An event may bill several periods at once from several fee tabs (e.g. a
    header 'Q3 2025 - Q3 2026 Mgmt Fees' summing five quarterly columns), and each vehicle usually
    has its own fee tab."""
    alloc = ctx.data.allocation
    fee_comps = [c for c in alloc.active_components if c.component_type == "mgmt_fee"]
    if not fee_comps:
        raise NotApplicable("The current event has no management-fee component.")
    fee = ctx.data.mgmt_fee
    sheet = alloc.sheet
    tied = []
    for comp in fee_comps:
        links = fee_links(ctx.model, alloc, comp.column)
        alloc_periods = quarter_range(comp.header)
        pulled: dict[tuple, set] = {}
        sources_seen: dict[tuple, _FeeSource] = {}
        checked_periods: set[tuple] = set()
        for vehicle in alloc.vehicles:
            sources = links.get(vehicle.name)
            if sources is None:
                if fee is not None and len(fee.layout.fee_columns) == 1:
                    # Typed or unlinked fee cells: tie them to the mapped fee tab by investor name.
                    sources = (_FeeSource(fee.sheet.name, fee.layout.fee_columns[0].column, None),)
                elif any(inv.amounts.get(comp.column) for inv in vehicle.investors) and vehicle.additive:
                    out.review(f"{vehicle.name}: the fee cells in column {comp.column} are not linked to a fee tab"
                               + ("" if fee is None else f" and the mapped fee tab has {len(fee.layout.fee_columns)} "
                                  "period columns") + ", so the fees cannot be tied out per investor.", sheet,
                               f"{comp.column}{vehicle.investors[0].row}")
                    continue
                else:
                    continue
            # (1) the fee tab period(s) belong to the Allocation fee component's period.
            for src in sources:
                sources_seen[src.key()] = src
                if src.key() in checked_periods:
                    continue
                checked_periods.add(src.key())
                tab = ctx.model.sheet(src.sheet)
                first_row = min((r for r, _, _, _ in _support_rows(ctx, src.lookup)), default=None) if src.lookup else None
                header = _column_header(tab, src.column, first_row) or ""
                tab_period = quarter_key(header)
                if alloc_periods and tab_period and tab_period not in alloc_periods:
                    out.fail(f"The fee tab column {src.sheet}!{src.column} is for {header!r} but the Allocation fee "
                             f"component is {comp.header!r} (stale period).", tab, f"{src.column}{first_row or 1}")
            # (2) per LP: the Allocation fee equals the sum of what the fee tab(s) hold for that LP.
            for inv in vehicle.investors:
                charged = inv.amounts.get(comp.column, ZERO)
                expected, located = ZERO, False
                for src in sources:
                    tab = ctx.model.sheet(src.sheet)
                    if src.lookup is not None:
                        criteria = _criteria(src.lookup, sheet, inv.row)
                        pulled.setdefault(src.key(), set()).add(criteria)
                        matches = [amount for _, keys, _, amount in _support_rows(ctx, src.lookup) if keys == criteria]
                        if matches:
                            located = True
                            expected += matches[0] if src.lookup.first_match else sum(matches, ZERO)
                    else:
                        key_col = fee.layout.columns.investor if fee is not None and fee.sheet.name == src.sheet else None
                        row = _row_of_name(tab, inv.name, key_col)
                        if row is not None:
                            located = True
                            pulled.setdefault(src.key(), set()).add((norm_text(inv.name),))
                            expected += to_decimal(tab.value(f"{src.column}{row}")) or ZERO
                coord = f"{comp.column}{inv.row}"
                if not located:
                    if charged:
                        out.fail(f"{inv.name} is charged {money(charged)} but has no row on the fee tab(s) "
                                 f"({', '.join(map(str, sources))}).", sheet, coord)
                    continue
                if differs(mag(charged), mag(expected), CENT):
                    out.fail(f"{inv.name}: Allocation fee {money(charged)} does not match the fee tab(s) "
                             f"({money(expected)} from {', '.join(map(str, sources))}).", sheet, coord)
                # A cell that reads another column but still shows the right value is a link defect,
                # reported by CE-ALLOC-REFERENCE-INTEGRITY (link_pattern_exceptions), not a fee mismatch.
            tied.append(f"{vehicle.name}: {comp.header or comp.column} <- {', '.join(map(str, sources))}")
        # (3) nothing on the fee tab(s) goes unpulled.
        for key, src in sources_seen.items():
            if src.lookup is None:
                continue
            tab = ctx.model.sheet(src.sheet)
            for r, keys, label, amount in _support_rows(ctx, src.lookup):
                if amount and keys not in pulled.get(key, set()):
                    out.review(f"{label} carries {money(amount)} on {src.sheet} (column {src.column}) but is not on "
                               "the Allocation sheet.", tab, f"{src.column}{r}")
    if not tied:
        return ""
    return "Per-LP fees pull correctly from the current-period fee tab(s): " + "; ".join(tied) + "."


# --- ITD vs Allocation ------------------------------------------------------------------------


def _pair_components(itd, block, alloc) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """Map each ITD current-block column to the Allocation component column(s) it stands for.

    The formulas decide first: ``=SUM(Allocation!Q7:T7)`` ties one ITD "Investment" column to
    four Allocation investment columns. Columns without links are paired by (type, side): one
    ITD column against all Allocation columns of that type, or one-to-one when the counts agree.
    """
    alloc_name = alloc.sheet.name
    component_cols = {c.column for c in alloc.active_components}
    pairs: list[tuple[str, list[str]]] = []
    unmapped: list[str] = []
    remaining = []
    for comp in block.components:
        votes: Counter = Counter()
        for inv in itd.investors:
            cols = tuple(sorted({c for s, c, _ in sheet_cell_refs(inv.formulas.get(comp.column))
                                 if s == alloc_name and c in component_cols}, key=col_idx))
            if cols:
                votes[cols] += 1
        if votes and votes.most_common(1)[0][1] * 2 > len(itd.investors):
            pairs.append((comp.column, list(votes.most_common(1)[0][0])))
        else:
            remaining.append(comp)
    taken = {c for _, cols in pairs for c in cols}
    pool: dict[tuple[str, str], list[str]] = {}
    for comp in alloc.active_components:
        if comp.column not in taken:
            pool.setdefault((comp.component_type, comp.side), []).append(comp.column)
    by_type = Counter((c.component_type, c.side) for c in remaining)
    for comp in remaining:
        key = (comp.component_type, comp.side)
        candidates = pool.get(key, [])
        if not candidates:
            unmapped.append(comp.column)
        elif by_type[key] == 1:
            pairs.append((comp.column, list(candidates)))
            candidates.clear()
        elif by_type[key] == len(candidates) + sum(1 for c, cols in pairs if c != comp.column and cols and
                                                   (cols[0] in taken)) or len(candidates) >= by_type[key]:
            pairs.append((comp.column, [candidates.pop(0)]))
        else:
            unmapped.append(comp.column)
    return pairs, unmapped


def _describe(cols: list[str]) -> str:
    return "+".join(cols) if len(cols) > 1 else cols[0]


@check("CE-TIE-ITD-ALLOCATION", needs=("allocation", "itd"))
def tie_itd_allocation(ctx: CheckContext, out: Outcome) -> str:
    alloc, itd = ctx.data.allocation, ctx.data.itd
    block = itd.current_block
    if block is None:
        out.review("The current ITD event block could not be identified.")
        return ""
    pairs, unmapped = _pair_components(itd, block, alloc)
    if unmapped:
        out.review(f"Current ITD block column(s) {', '.join(unmapped)} cannot be mapped to an Allocation component.")
    matcher = Matcher(alloc.vehicles, itd.vehicles)
    for ordinal, inv in matcher.pairs():
        target = matcher.find(ordinal, inv.name)
        has_amount = any(inv.amounts.get(a, ZERO) for _, cols in pairs for a in cols)
        if target is None:
            if has_amount:
                out.fail(f"{matcher.label(ordinal, inv.name)} has a current-event amount but is missing from the ITD "
                         "block.", alloc.sheet, f"{alloc.layout.columns.investor}{inv.row}")
            continue
        for itd_col, alloc_cols in pairs:
            a = sum((inv.amounts.get(c, ZERO) for c in alloc_cols), ZERO)
            b = target.values.get(itd_col, ZERO)
            if differs(mag(a), mag(b), PENNY):
                out.fail(f"{matcher.label(ordinal, inv.name)}: ITD {itd_col}{target.row} is {money(b)} but Allocation "
                         f"{_describe(alloc_cols)}{inv.row} is {money(a)}.", itd.sheet, f"{itd_col}{target.row}")
    for itd_col, alloc_cols in pairs:
        itd_total = sum((i.values.get(itd_col, ZERO) for i in itd.investors), ZERO)
        alloc_total = sum((i.amounts.get(c, ZERO) for i in alloc.investors for c in alloc_cols), ZERO)
        if differs(mag(itd_total), mag(alloc_total), PENNY):
            out.fail(f"ITD column {itd_col} totals {money(itd_total)} vs Allocation {_describe(alloc_cols)} "
                     f"{money(alloc_total)}.", itd.sheet, f"{itd_col}{itd.layout.event_header_row}")
    mapping = ", ".join(f"{i} = {_describe(a)}" for i, a in pairs)
    return f"The current ITD block ('{block.label}') equals the Allocation sheet per investor and component ({mapping})."


@check("CE-TIE-ITD-COMMITMENTS", needs=("allocation", "itd"))
def tie_itd_commitments(ctx: CheckContext, out: Outcome) -> str:
    alloc, itd = ctx.data.allocation, ctx.data.itd
    block = itd.current_block
    current_contrib_cols = [c for c in (itd.block_columns(block) if block else [])
                            if set(itd.marks.get(c, [])) & {"investment_contributions", "cost_contributions"}]
    matcher = Matcher(alloc.vehicles, itd.vehicles)
    for ordinal, inv in matcher.pairs():
        target = matcher.find(ordinal, inv.name)
        label = matcher.label(ordinal, inv.name)
        if target is None:
            if inv.commitment:
                out.fail(f"{label} is on the Allocation sheet but not on the ITD sheet.", alloc.sheet,
                         f"{alloc.layout.columns.investor}{inv.row}")
            continue
        rf = inv.roll_forward
        commitment = rf.get("commitment") if rf.get("commitment") is not None else inv.commitment
        itd_commitment = target.cumulative.get("commitment")
        if itd_commitment is not None and differs(mag(commitment), mag(itd_commitment), CENT):
            out.fail(f"{label}: Allocation commitment {money(commitment)} vs ITD {money(itd_commitment)}.",
                     itd.sheet, f"{itd.layout.cumulative_columns.commitment}{target.row}")
        total = target.cumulative.get("total_contributions")
        prior = rf.get("prior_contributions")
        if total is not None and prior is not None:
            itd_prior = total - sum((target.values.get(c, ZERO) for c in current_contrib_cols), ZERO)
            if differs(mag(prior), mag(itd_prior), CENT):
                out.fail(f"{label}: Allocation prior contributions {money(mag(prior))} vs ITD contributions before "
                         f"this event {money(mag(itd_prior))}.", alloc.sheet,
                         f"{alloc.layout.roll_forward.prior_contributions}{inv.row}")
        remaining = rf.get("remaining_commitment")
        unfunded = target.cumulative.get("unfunded")
        if remaining is not None and unfunded is not None and differs(mag(remaining), mag(unfunded), CENT):
            out.fail(f"{label}: Allocation remaining commitment {money(remaining)} vs ITD unfunded "
                     f"{money(unfunded)}.", alloc.sheet, f"{alloc.layout.roll_forward.remaining_commitment}{inv.row}")
    return "Commitments, prior contributions and remaining commitment agree between the Allocation and ITD sheets."


# --- Merge tabs ------------------------------------------------------------------------------


def _merge_rows_for(alloc, merges):
    """Allocation investor -> (merge, row): by the Allocation row the merge row links to, else by
    a name that is unique across the merge tabs."""
    by_source: dict[int, tuple] = {}
    by_name: dict[str, list[tuple]] = {}
    for merge in merges:
        for row in merge.rows:
            if row.source_row is not None and merge.source_sheet == alloc.sheet.name:
                by_source.setdefault(row.source_row, (merge, row))
            by_name.setdefault(row.name, []).append((merge, row))

    def find(inv):
        hit = by_source.get(inv.row)
        if hit is not None:
            return hit
        candidates = by_name.get(inv.name, [])
        return candidates[0] if len(candidates) == 1 else None

    return find


@check("CE-TIE-MERGE", needs=("allocation", "merge"))
def tie_merge(ctx: CheckContext, out: Outcome) -> str:
    """The Merge tabs are the data the investor notices are built from. Each tab must pull every
    active component of its vehicle from the Allocation (a stale tab keeps last event's columns),
    tie per investor and in total, and its own check cells must be zero."""
    alloc = ctx.data.allocation
    layout = alloc.layout
    header_row = layout.header_row
    active = {c.column: c for c in alloc.active_components}
    call_cols = {c for c, comp in active.items() if comp.side == "call"}
    dist_cols = {c for c, comp in active.items() if comp.side == "distribution"}
    cash_col = layout.columns.cash_due
    by_row = {inv.row: inv for inv in alloc.investors}
    summary = []
    for merge in ctx.data.merges:
        msheet = merge.sheet
        if merge.source_sheet not in (None, alloc.sheet.name):
            out.review(f"{msheet.name} pulls its data from '{merge.source_sheet}', not from the Allocation sheet.",
                       msheet, f"{merge.layout.columns.investor}{merge.layout.first_data_row}")
            continue
        linked = [(row, by_row.get(row.source_row) if row.source_row else None) for row in merge.rows]
        if not any(inv for _, inv in linked):
            unique = {inv.name: inv for inv in alloc.investors if sum(1 for i in alloc.investors if i.name == inv.name) == 1}
            linked = [(row, unique.get(row.name)) for row in merge.rows]
        members = [inv for _, inv in linked if inv is not None]
        if not members:
            out.review(f"{msheet.name}: no row links to an Allocation investor row, so the tab cannot be tied out.",
                       msheet, f"{merge.layout.columns.investor}{merge.layout.first_data_row}")
            continue
        vehicles = Counter(v.name for inv in members for v in [alloc.vehicle_of(inv)] if v)
        vehicle_name = vehicles.most_common(1)[0][0] if vehicles else merge.vehicle or msheet.name
        vehicle = next((v for v in alloc.vehicles if v.name == vehicle_name), None)
        pulled = {ac: mc for mc, ac in merge.referenced_columns.items()}
        # (1) coverage: every active component with amounts in this vehicle is pulled into the tab.
        missing = [c for c in call_cols | dist_cols if c not in pulled
                   and any(inv.amounts.get(c, ZERO) for inv in members)]
        missing.sort(key=col_idx)
        if missing:
            omitted = sum((mag(inv.amounts.get(c, ZERO)) for inv in members for c in missing), ZERO)
            names = ", ".join(f"{c} ({active[c].header or active[c].component_type})" for c in missing)
            out.fail(f"{msheet.name} ({vehicle_name}) does not pull Allocation column(s) {names}: the notice data "
                     f"omits {money(omitted)} of this event's amounts for its {len(members)} investors.", msheet,
                     f"{merge.layout.columns.investor}{merge.layout.header_row}")
        # (2) stale headers on component columns.
        for mc, ac in sorted(merge.referenced_columns.items(), key=lambda kv: col_idx(kv[0])):
            if ac not in active or mc in merge.header_refs:
                continue
            merge_header, alloc_header = merge.headers.get(mc), alloc.sheet.value(f"{ac}{header_row}")
            if is_text(merge_header) and is_text(alloc_header) and norm_text(merge_header) != norm_text(alloc_header):
                out.review(f"{msheet.name} column {mc} is headed {merge_header!r} but pulls Allocation column {ac} "
                           f"({str(alloc_header).strip()!r}); the notice label may be stale.", msheet,
                           f"{mc}{merge.layout.header_row}")
        # (3) per investor: the call and distribution amounts on the tab equal the Allocation's.
        explained = 0
        for row, inv in linked:
            if inv is None:
                if any(row.values.values()):
                    out.fail(f"{row.name} on {msheet.name} does not link to an Allocation investor row.", msheet,
                             f"{merge.layout.columns.investor}{row.row}")
                continue
            for side, cols in (("call", call_cols), ("distribution", dist_cols)):
                if not cols:
                    continue
                merge_amount = sum((row.values.get(mc, ZERO) for mc, ac in merge.referenced_columns.items() if ac in cols), ZERO)
                alloc_amount = sum((inv.amounts.get(c, ZERO) for c in cols), ZERO)
                if not differs(mag(merge_amount), mag(alloc_amount), PENNY):
                    continue
                short = sum((inv.amounts.get(c, ZERO) for c in missing if c in cols), ZERO)
                if missing and not differs(mag(alloc_amount) - mag(merge_amount), mag(short), PENNY):
                    explained += 1  # the gap is exactly the missing columns, already reported once
                    continue
                out.fail(f"{inv.name}: {msheet.name} {side} total {money(merge_amount)} vs Allocation "
                         f"{money(alloc_amount)}.", msheet, f"{merge.layout.columns.investor}{row.row}")
            if cash_col and cash_col in pulled:
                merge_cash = row.values.get(pulled[cash_col], ZERO)
                alloc_cash = to_decimal(alloc.sheet.value(f"{cash_col}{inv.row}")) or ZERO
                if differs(mag(merge_cash), mag(alloc_cash), PENNY):
                    out.fail(f"{inv.name}: {msheet.name} cash due {money(merge_cash)} vs Allocation {money(alloc_cash)}.",
                             msheet, f"{pulled[cash_col]}{row.row}")
        if explained:
            out.fail(f"{msheet.name}: {explained} investor total(s) fall short of the Allocation by exactly the "
                     "unpulled column(s) above.", msheet, f"{merge.layout.columns.investor}{merge.layout.first_data_row}")
        # (4) the total row refoots and the tab's own check cells are zero.
        for mc, stated in merge.totals.items():
            expected = sum((row.values.get(mc, ZERO) for row in merge.rows), ZERO)
            if differs(stated, expected, PENNY):
                out.fail(f"{msheet.name} total {mc}{merge.layout.total_row} is {money(stated)} but its rows sum to "
                         f"{money(expected)}.", msheet, f"{mc}{merge.layout.total_row}")
        for coord, value in merge.check_cells:
            if mag(value) > PENNY:
                out.fail(f"{msheet.name} check cell {coord} is {money(value)}, not zero.", msheet, coord)
        summary.append(f"{msheet.name} -> {vehicle_name} ({len(members)} investors)")
    if not summary:
        return ""
    return "Every Merge tab pulls all of its vehicle's current components and ties to the Allocation per investor: " \
        + "; ".join(summary) + "."


# --- identity --------------------------------------------------------------------------------


@check("CE-ID-INVESTOR-KEYS", needs=("allocation", "merge", "investor_data"))
def investor_keys(ctx: CheckContext, out: Outcome) -> str:
    alloc = ctx.data.allocation
    dx = ctx.data.investor_data
    fee = ctx.data.mgmt_fee
    merge_row_for = _merge_rows_for(alloc, ctx.data.merges)
    active_cols = [c.column for c in alloc.active_components]
    in_scope = [i for i in alloc.investors if not i.is_gp and any(i.amounts.get(c, ZERO) for c in active_cols)]
    dx_by_id = dx.by_investor_id
    fee_names = {r.name for r in fee.rows} if fee else set()
    # The mapped fee tab serves the vehicles whose fee formulas read it (each vehicle may have its own
    # tab, and a look-through block allocates the GP's fee by percentage), or every additive vehicle
    # when the fee cells are not linked at all.
    fee_vehicles: set[str] = set()
    if fee is not None:
        fee_cols = [c.column for c in alloc.active_components if c.component_type == "mgmt_fee"]
        links = {v: srcs for col in fee_cols for v, srcs in fee_links(ctx.model, alloc, col).items()}
        if links:
            fee_vehicles = {v for v, srcs in links.items() if any(src.sheet == fee.sheet.name for src in srcs)}
        else:
            fee_vehicles = {v.name for v in alloc.additive_vehicles}
    # A Merge tab whose "Investor ID" is a row counter (1, 2, 3 ...) shares no ID with the document
    # system: report that once per tab instead of once per investor.
    sequence_tabs = set()
    for merge in ctx.data.merges:
        ids = [row.investor_id for row in merge.rows if row.investor_id not in (None, "")]
        numeric = [i for i in ids if isinstance(i, int) and not isinstance(i, bool)]
        small = [i for i in numeric if 0 < i <= len(ids) + 2]
        if ids and not any(i in dx_by_id for i in ids) and len(numeric) == len(ids) and len(small) * 5 >= len(ids) * 4:
            sequence_tabs.add(merge.sheet.name)
            out.fail(f"{merge.sheet.name}: the Investor ID column is a row sequence (1-{max(numeric)}); none of its "
                     f"IDs is on {dx.sheet.name}, so the notice data carries no document-system investor IDs.",
                     merge.sheet, f"{merge.layout.columns.investor_id}{merge.layout.first_data_row}")
    for inv in in_scope:
        found = merge_row_for(inv)
        if found is None:
            out.fail(f"{inv.name} participates in this event but is missing from the Merge tab (exact name match).",
                     alloc.sheet, f"{alloc.layout.columns.investor}{inv.row}")
            continue
        merge, row = found
        if row.investor_id in (None, ""):
            out.fail(f"{inv.name} has no Investor ID on {merge.sheet.name}.", merge.sheet,
                     f"{merge.layout.columns.investor_id}{row.row}")
            continue
        dx_row = dx_by_id.get(row.investor_id)
        if dx_row is None:
            if merge.sheet.name not in sequence_tabs:
                out.fail(f"{inv.name}: Investor ID {row.investor_id} from {merge.sheet.name} is not on "
                         f"{dx.sheet.name}.", dx.sheet, None)
        elif dx_row.name.strip() != row.name.strip():
            out.fail(f"Investor ID {row.investor_id}: {dx.sheet.name} names it {dx_row.name!r} but the Merge tab "
                     f"(system of record) has {row.name!r}.", dx.sheet, f"{dx.layout.columns.investor_name}{dx_row.row}")
        elif row.fund_id not in (None, "") and dx_row.fund_id not in (None, "") and str(dx_row.fund_id) != str(row.fund_id):
            out.fail(f"{inv.name}: Fund ID {row.fund_id} on the Merge tab vs {dx_row.fund_id} on {dx.sheet.name}.",
                     dx.sheet, f"{dx.layout.columns.fund_id}{dx_row.row}")
        if fee is not None and inv.vehicle in fee_vehicles and inv.name not in fee_names:
            out.fail(f"{inv.name} is missing from the fee tab (exact name match).", fee.sheet,
                     f"{fee.layout.columns.investor}{fee.layout.header_row}")
    # FA calibration: investor IDs typically live only on the Merge tab, so the other tabs
    # are matched by exact name.
    return (f"All {len(in_scope)} participating investors tie by exact name across the tabs, and their Merge "
            "Investor and Fund IDs match the investor data tab.")


# --- distributions ----------------------------------------------------------------------------


@check("CE-DIST-ROC-LIMIT", needs=("allocation",))
def roc_limit(ctx: CheckContext, out: Outcome) -> str:
    alloc = ctx.data.allocation
    roc_cols = [c.column for c in alloc.active_components if c.component_type == "return_of_capital"]
    if not roc_cols:
        raise NotApplicable("The current event has no return-of-capital component.")
    itd = ctx.data.itd
    if itd is None:
        out.review("The ITD sheet layout could not be mapped, so contributed capital is unknown.")
        return ""
    prior_roc_cols = [c.column for b in itd.prior_blocks for c in b.components if c.component_type == "return_of_capital"]
    matcher = Matcher(alloc.vehicles, itd.vehicles)
    outstanding_total = ZERO
    roc_total = ZERO
    for ordinal, inv in matcher.pairs():
        roc = sum((mag(inv.amounts.get(c, ZERO)) for c in roc_cols), ZERO)
        target = matcher.find(ordinal, inv.name)
        contributed = mag(target.cumulative.get("total_contributions")) if target else ZERO
        returned = sum((mag(target.values.get(c, ZERO)) for c in prior_roc_cols), ZERO) if target else ZERO
        outstanding = contributed - returned
        outstanding_total += max(outstanding, ZERO)
        roc_total += roc
        coord = f"{roc_cols[0]}{inv.row}"
        label = matcher.label(ordinal, inv.name)
        if roc and contributed == ZERO:
            out.fail(f"{label} never funded but receives {money(roc)} of return of capital.", alloc.sheet, coord)
        elif roc - outstanding > CENT:
            out.fail(f"{label} receives {money(roc)} of return of capital but only {money(outstanding)} of "
                     "contributed capital is outstanding.", alloc.sheet, coord)
    if roc_total - outstanding_total > CENT:
        out.fail(f"Return of capital {money(roc_total)} exceeds total contributed capital outstanding "
                 f"{money(outstanding_total)}.")
    return f"Return of capital ({money(roc_total)}) stays within each investor's outstanding contributed capital."
