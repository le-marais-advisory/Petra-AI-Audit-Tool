"""Allocation-sheet checks: formulas, ties, refoot, pro-rata parity, plugs, commitments, rounding, merges."""
from __future__ import annotations

import re
from decimal import Decimal

from openpyxl.utils.cell import range_boundaries

from src.pipeline.workbook.cells import PENNY, col_idx
from src.pipeline.workbook.checks._common import (
    CheckContext,
    Outcome,
    check,
    differs,
    mag,
    money,
    round_digits,
)
from src.pipeline.workbook.extract import AllocationData, AllocationVehicle

ZERO = Decimal("0")
PARITY_TOLERANCE = Decimal("2.00")
_PLUG_RE = re.compile(r"^=\s*ROUND\(.*\)\s*([+-])\s*(\d+(?:\.\d+)?)\s*$", re.I)


def _driver_ref_re(column: str, row: int) -> re.Pattern:
    return re.compile(rf"(?<![A-Z]){re.escape(column)}\$?{row}(?!\d)")


def _active_in_vehicle(alloc: AllocationData, vehicle: AllocationVehicle):
    for comp in alloc.active_components:
        if vehicle.driver.get(comp.column, ZERO) != ZERO or any(i.amounts.get(comp.column) for i in vehicle.investors):
            yield comp


@check("CE-ALLOC-PER-LP-FORMULAS", needs=("allocation",))
def per_lp_formulas(ctx: CheckContext, out: Outcome) -> str:
    alloc = ctx.data.allocation
    sheet = alloc.sheet
    for vehicle in alloc.vehicles:
        for comp in alloc.active_components:
            if vehicle.driver.get(comp.column, ZERO) == ZERO:
                continue
            driver_ref = _driver_ref_re(comp.column, vehicle.driver_row)
            for inv in vehicle.investors:
                cell = inv.cells.get(comp.column)
                coord = f"{comp.column}{inv.row}"
                if cell is None or cell.value is None:
                    continue
                if cell.formula is None:
                    out.fail(f"{coord} ({inv.name}, {comp.header or comp.component_type}) holds a typed value "
                             f"{money(cell.value)} instead of a driver formula.", sheet, coord)
                elif "!" not in cell.formula and not driver_ref.search(cell.formula.replace("$", "")):
                    out.fail(f"{coord} ({inv.name}) formula {cell.formula} does not reference the vehicle driver "
                             f"{comp.column}{vehicle.driver_row} or a source tab.", sheet, coord)
    return "Every per-LP cell in an active component column is a formula derived from the vehicle driver or fee tab."


@check("CE-ALLOC-VEHICLE-TIE", needs=("allocation",))
def vehicle_tie(ctx: CheckContext, out: Outcome) -> str:
    alloc = ctx.data.allocation
    sheet = alloc.sheet
    for vehicle in alloc.vehicles:
        total_row = vehicle.subtotal_rows.get("total")
        for comp in alloc.components:
            per_lp = sum((i.amounts.get(comp.column, ZERO) for i in vehicle.investors), ZERO)
            driver = vehicle.driver.get(comp.column, ZERO)
            label = comp.header or comp.component_type
            if total_row is not None:
                total = vehicle.totals.get(comp.column, ZERO)
                if differs(per_lp, total, PENNY):
                    out.fail(f"{vehicle.name} / {label}: per-LP cells sum to {money(per_lp)} but the total row "
                             f"shows {money(total)}.", sheet, f"{comp.column}{total_row}")
                if differs(total, driver, PENNY):
                    out.fail(f"{vehicle.name} / {label}: vehicle total {money(total)} does not equal the driver "
                             f"{money(driver)}.", sheet, f"{comp.column}{vehicle.driver_row}")
            elif differs(per_lp, driver, PENNY):
                out.fail(f"{vehicle.name} / {label}: per-LP cells sum to {money(per_lp)} vs driver {money(driver)}.",
                         sheet, f"{comp.column}{vehicle.driver_row}")
    return "Per vehicle and component, per-LP sums, vehicle totals and drivers tie to the penny."


@check("CE-ALLOC-GROSS-TIE", needs=("allocation",))
def gross_tie(ctx: CheckContext, out: Outcome) -> str:
    alloc = ctx.data.allocation
    sheet = alloc.sheet
    layout = alloc.layout
    driver_row = layout.fund_driver_row
    # (a) component drivers sum to each side's event total driver.
    for total in layout.event_total_columns:
        side_sum = sum((alloc.fund_drivers.get(c.column, ZERO) for c in alloc.components if c.side == total.side), ZERO)
        stated = alloc.fund_drivers.get(total.column, ZERO)
        if differs(side_sum, stated, PENNY):
            out.fail(f"{total.side} components sum to {money(side_sum)} but the event total driver "
                     f"{total.column}{driver_row} is {money(stated)}.", sheet, f"{total.column}{driver_row}")
    # (b) per component, vehicle totals sum to the fund-level amount.
    for comp in alloc.components:
        vehicle_sum = sum((v.totals.get(comp.column, ZERO) for v in alloc.vehicles), ZERO)
        fund = alloc.fund_drivers.get(comp.column, ZERO)
        if differs(vehicle_sum, fund, PENNY):
            out.fail(f"{comp.header or comp.component_type}: vehicle totals sum to {money(vehicle_sum)} but the "
                     f"fund-level amount is {money(fund)}.", sheet, f"{comp.column}{driver_row}")
    # (c) bottom-line totals equal the event gross.
    bottom_cols = [t.column for t in layout.event_total_columns]
    if layout.columns.cash_due:
        bottom_cols.append(layout.columns.cash_due)
    grand_row = layout.grand_total_row or (layout.vehicles[-1].subtotal_rows.total if len(layout.vehicles) == 1 else None)
    for column in bottom_cols:
        driver_value = sheet.value(f"{column}{driver_row}")
        if driver_value is None or grand_row is None:
            continue
        grand = alloc.grand_totals.get(column, ZERO)
        if differs(grand, _value(alloc, column, driver_row), PENNY):
            out.fail(f"Bottom-line total {column}{grand_row} ({money(grand)}) does not equal the event gross "
                     f"{column}{driver_row} ({money(driver_value)}).", sheet, f"{column}{grand_row}")
    return f"Components, vehicle totals and bottom-line totals reconcile to the event gross ({money(alloc.event_gross)})."


def _numeric_columns(alloc: AllocationData) -> list[tuple[str, Decimal]]:
    layout = alloc.layout
    pct_tol = Decimal("0.000001")
    cols = [(layout.columns.commitment, PENNY), (layout.columns.commitment_pct, pct_tol)]
    cols += [(c.column, PENNY) for c in alloc.components]
    cols += [(t.column, PENNY) for t in layout.event_total_columns]
    cols += [(c, PENNY) for c in layout.roll_forward.model_dump().values() if c]
    if layout.columns.cash_due:
        cols.append((layout.columns.cash_due, PENNY))
    if layout.columns.distribution_basis:
        cols.append((layout.columns.distribution_basis, PENNY))
    if layout.columns.distribution_basis_pct:
        cols.append((layout.columns.distribution_basis_pct, pct_tol))
    seen, unique = set(), []
    for col, tol in cols:
        if col not in seen:
            seen.add(col)
            unique.append((col, tol))
    return unique


def _value(alloc: AllocationData, column: str, row: int) -> Decimal:
    from src.pipeline.workbook.cells import to_money

    return to_money(alloc.sheet.value(f"{column}{row}"))


@check("CE-ALLOC-REFOOT", needs=("allocation",))
def refoot(ctx: CheckContext, out: Outcome) -> str:
    alloc = ctx.data.allocation
    sheet = alloc.sheet
    columns = _numeric_columns(alloc)
    for vehicle in alloc.vehicles:
        rows = vehicle.subtotal_rows
        lps = vehicle.limited_partners
        gps = [i for i in vehicle.investors if i.is_gp]
        for column, tol in columns:
            lp_sum = sum((_value(alloc, column, i.row) for i in lps), ZERO)
            gp_sum = sum((_value(alloc, column, i.row) for i in gps), ZERO)
            checks = [("limited_partners", lp_sum), ("general_partner", gp_sum)]
            for key, expected in checks:
                row = rows.get(key)
                if row is None:
                    continue
                stated = _value(alloc, column, row)
                if differs(stated, expected, tol):
                    out.fail(f"{vehicle.name}: {key.replace('_', ' ')} subtotal {column}{row} is {stated} but its rows "
                             f"sum to {expected}.", sheet, f"{column}{row}")
            total_row = rows.get("total")
            if total_row is not None:
                parts = [rows.get("limited_partners"), rows.get("general_partner")]
                expected = sum((_value(alloc, column, r) for r in parts if r is not None), ZERO) \
                    if any(parts) else lp_sum + gp_sum
                stated = _value(alloc, column, total_row)
                if differs(stated, expected, tol):
                    out.fail(f"{vehicle.name}: total {column}{total_row} is {stated} but should be {expected}.",
                             sheet, f"{column}{total_row}")
    layout = alloc.layout
    if len(alloc.vehicles) > 1 and layout.grand_total_row:
        for column, tol in columns:
            expected = sum((_value(alloc, column, v.subtotal_rows["total"]) for v in alloc.vehicles
                            if v.subtotal_rows.get("total")), ZERO)
            stated = _value(alloc, column, layout.grand_total_row)
            if differs(stated, expected, tol):
                out.fail(f"Grand total {column}{layout.grand_total_row} is {stated} but vehicle totals sum to "
                         f"{expected}.", sheet, f"{column}{layout.grand_total_row}")
    # Crossfoot: each side's event total equals that side's active components on every row.
    active = alloc.active_components
    for total in layout.event_total_columns:
        comps = [c.column for c in active if c.side == total.side]
        rows_to_check = [i.row for i in alloc.investors]
        for vehicle in alloc.vehicles:
            rows_to_check += [r for r in vehicle.subtotal_rows.values() if r]
        for row in rows_to_check:
            expected = sum((_value(alloc, c, row) for c in comps), ZERO)
            stated = _value(alloc, total.column, row)
            if differs(stated, expected, PENNY):
                out.fail(f"Row {row}: {total.side} total {total.column}{row} is {money(stated)} but its components "
                         f"sum to {money(expected)}.", sheet, f"{total.column}{row}")
    return "All subtotals and totals refoot and every row crossfoots to its components."


def _parity_rows(alloc: AllocationData, vehicle: AllocationVehicle, comp):
    """(investor, basis, amount) triples for the component's participating pool, or None if no basis."""
    investors = vehicle.investors
    if comp.side == "distribution":
        if not alloc.layout.columns.distribution_basis:
            return None
        pool = [i for i in investors if not (comp.component_type == "carry" and i.is_gp)]
        return [(i, mag(i.distribution_basis), i.amounts.get(comp.column, ZERO)) for i in pool
                if mag(i.distribution_basis) or i.amounts.get(comp.column)]
    return [(i, i.commitment, i.amounts.get(comp.column, ZERO)) for i in investors if i.amounts.get(comp.column)]


@check("CE-ALLOC-PRO-RATA-PARITY", needs=("allocation",))
def pro_rata_parity(ctx: CheckContext, out: Outcome) -> str:
    alloc = ctx.data.allocation
    sheet = alloc.sheet
    worst = ZERO
    for vehicle in alloc.vehicles:
        for comp in _active_in_vehicle(alloc, vehicle):
            rows = _parity_rows(alloc, vehicle, comp)
            label = f"{vehicle.name} / {comp.header or comp.component_type}"
            if rows is None:
                out.review(f"{label}: no allocation basis column (contributed capital) is shown for this "
                           "distribution component, so parity cannot be measured.")
                continue
            pool = sum((amount for _, _, amount in rows), ZERO)
            basis_total = sum((basis for _, basis, _ in rows), ZERO)
            if not basis_total:
                continue
            residuals = []
            for inv, basis, amount in rows:
                residual = amount - pool * basis / basis_total
                worst = max(worst, abs(residual))
                if abs(residual) > PARITY_TOLERANCE:
                    residuals.append((residual, inv))
            if residuals:
                residuals.sort(key=lambda item: -abs(item[0]))
                detail = "; ".join(f"{inv.name} {money(res)}" for res, inv in residuals[:6])
                out.fail(f"{label}: {len(residuals)} investor(s) deviate from pure pro-rata by more than $2 "
                         f"({detail}).", sheet, f"{comp.column}{residuals[0][1].row}")
    return f"Every investor is within $2 of its pure pro-rata share (largest residual {money(worst)})."


def _plugs(alloc: AllocationData, vehicle: AllocationVehicle, column: str):
    for inv in vehicle.investors:
        formula = inv.formulas.get(column)
        match = _PLUG_RE.match(formula or "")
        if match:
            sign = -1 if match.group(1) == "-" else 1
            yield inv, Decimal(match.group(2)) * sign


@check("CE-ALLOC-PLUG-DISCIPLINE", needs=("allocation",))
def plug_discipline(ctx: CheckContext, out: Outcome) -> str:
    alloc = ctx.data.allocation
    sheet = alloc.sheet
    active_cols = {c.column for c in alloc.active_components}
    fee_cols = [c.column for c in alloc.active_components if c.component_type == "mgmt_fee"]
    for vehicle in alloc.vehicles:
        lps = vehicle.limited_partners
        ceiling = max(Decimal("0.50"), Decimal(len(lps)) * Decimal("0.01"))

        def eligible(inv) -> bool | None:
            if inv.is_gp or inv.affiliate is True:
                return False
            if fee_cols and all(inv.amounts.get(c, ZERO) == ZERO for c in fee_cols) and inv.commitment:
                return False
            if inv.affiliate is None and not fee_cols:
                return None
            return True

        eligible_lps = [i for i in lps if eligible(i)]
        largest = max(eligible_lps, key=lambda i: (i.commitment, -i.row)) if eligible_lps else None
        for comp in alloc.components:
            plugs = list(_plugs(alloc, vehicle, comp.column))
            label = f"{vehicle.name} / {comp.header or comp.component_type}"
            if not plugs:
                continue
            if comp.column not in active_cols:
                for inv, offset in plugs:
                    out.fail(f"{label}: stale plug {offset:+} left on an inactive component ({inv.name}).",
                             sheet, f"{comp.column}{inv.row}")
                continue
            if len(plugs) > 1:
                names = ", ".join(f"{inv.name} {offset:+}" for inv, offset in plugs)
                out.fail(f"{label}: {len(plugs)} plugs in one component ({names}); at most one is allowed.",
                         sheet, f"{comp.column}{plugs[0][0].row}")
            for inv, offset in plugs:
                status = eligible(inv)
                coord = f"{comp.column}{inv.row}"
                if status is False:
                    out.fail(f"{label}: plug {offset:+} sits on {inv.name}, which is a GP, affiliate or fee-exempt "
                             "investor.", sheet, coord)
                elif status is None:
                    out.review(f"{label}: cannot tell whether {inv.name} (plugged {offset:+}) is an eligible LP.",
                               sheet, coord)
                elif largest is not None and inv is not largest and len(plugs) == 1:
                    out.fail(f"{label}: plug on {inv.name}, but the largest eligible LP is {largest.name}.", sheet,
                             coord)
                if abs(offset) > ceiling:
                    out.fail(f"{label}: plug {offset:+} on {inv.name} exceeds the rounding ceiling {ceiling}.",
                             sheet, coord)
    return "At most one plug per vehicle and component, on the largest eligible LP, within the rounding ceiling."


def _pct_scale(values: list[Decimal]) -> Decimal:
    total = sum(values, ZERO)
    return Decimal("100") if total > Decimal("50") else Decimal("1")


def _pct_tolerance(number_format: str) -> Decimal:
    match = re.search(r"0\.(0+)%", number_format or "")
    decimals = len(match.group(1)) if match else 4
    return Decimal("0.5") * Decimal(10) ** -(decimals + 2)


@check("CE-ALLOC-COMMITMENTS", needs=("allocation",))
def commitments(ctx: CheckContext, out: Outcome) -> str:
    alloc = ctx.data.allocation
    sheet = alloc.sheet
    pct_col = alloc.layout.columns.commitment_pct
    commit_col = alloc.layout.columns.commitment
    for vehicle in alloc.vehicles:
        investors = vehicle.investors
        total_row = vehicle.subtotal_rows.get("total")
        commitment_sum = sum((i.commitment for i in investors), ZERO)
        if total_row is not None:
            stated = vehicle.totals.get(commit_col, ZERO)
            if differs(commitment_sum, stated, PENNY):
                out.fail(f"{vehicle.name}: commitments sum to {money(commitment_sum)} but the total row shows "
                         f"{money(stated)}.", sheet, f"{commit_col}{total_row}")
        pcts = [i.commitment_pct or ZERO for i in investors]
        scale = _pct_scale(pcts)
        drift = abs(sum(pcts, ZERO) / scale - 1)
        if drift > Decimal("0.0001"):
            out.fail(f"{vehicle.name}: commitment % sums to {sum(pcts, ZERO) / scale:.6%} (off by {drift:.6f}).",
                     sheet, f"{pct_col}{total_row or investors[0].row}")
        elif drift > Decimal("0.000000001"):
            out.review(f"{vehicle.name}: commitment % sums to {sum(pcts, ZERO) / scale:.8%}, a sub-basis-point drift.")
        if commitment_sum:
            for inv in investors:
                cell = sheet.cell(f"{pct_col}{inv.row}")
                tolerance = _pct_tolerance(cell.number_format if cell else "")
                stated = (inv.commitment_pct or ZERO) / scale
                expected = inv.commitment / commitment_sum
                if differs(stated, expected, tolerance):
                    out.fail(f"{inv.name}: commitment % {stated:.6%} does not match its commitment share "
                             f"{expected:.6%}.", sheet, f"{pct_col}{inv.row}")
    return "Commitments tie to each vehicle total and commitment % sums to 100% and matches each commitment."


def _has_cents(value: Decimal) -> bool:
    return value != value.to_integral_value()


def _too_precise(value: Decimal) -> bool:
    scaled = value * 100
    return abs(scaled - scaled.to_integral_value()) > Decimal("0.001")


@check("CE-ALLOC-ROUNDING", needs=("allocation",))
def rounding(ctx: CheckContext, out: Outcome) -> str:
    alloc = ctx.data.allocation
    sheet = alloc.sheet
    bases: dict[str, str] = {}
    first_cells: dict[str, str] = {}
    for comp in alloc.components:
        values = []
        for inv in alloc.investors:
            amount = inv.amounts.get(comp.column, ZERO)
            if _too_precise(amount):
                out.fail(f"{comp.column}{inv.row} ({inv.name}) carries more than two decimals ({amount}).", sheet,
                         f"{comp.column}{inv.row}")
            if amount:
                values.append((inv, amount))
        if comp.column not in {c.column for c in alloc.active_components} or not values:
            continue
        first_cells[comp.header or comp.component_type] = f"{comp.column}{values[0][0].row}"
        digits = {round_digits(inv.formulas.get(comp.column)) for inv, _ in values} - {None}
        if digits == {0} or (not digits and len(values) >= 3 and not any(_has_cents(v) for _, v in values)):
            bases[comp.header or comp.component_type] = "whole dollars"
        elif any(_has_cents(v) for _, v in values) or digits == {2}:
            bases[comp.header or comp.component_type] = "cents"
    if len(set(bases.values())) > 1:
        detail = "; ".join(f"{name}: {basis}" for name, basis in bases.items())
        whole = next(name for name, basis in bases.items() if basis == "whole dollars")
        out.fail(f"The event mixes whole-dollar and cent-level rounding across components ({detail}).", sheet,
                 first_cells[whole])
    return "All amounts carry at most two decimals with one rounding basis."


@check("CE-ALLOC-MERGED-CELLS", needs=("allocation",))
def merged_cells(ctx: CheckContext, out: Outcome) -> str:
    alloc = ctx.data.allocation
    sheet = alloc.sheet
    layout = alloc.layout
    settable_cols = {col_idx(c.column) for c in alloc.components} | {col_idx(t.column) for t in layout.event_total_columns}
    first_row = min([layout.fund_driver_row, layout.header_row] + [v.driver_row or layout.fund_driver_row
                                                                    for v in layout.vehicles])
    last_row = max([v.subtotal_rows.total or v.investor_rows[-1] for v in layout.vehicles] +
                   [layout.grand_total_row or 0])
    for rng in sheet.merged_ranges:
        min_col, min_row, max_col, max_row = range_boundaries(rng)
        if max_row < first_row or min_row > last_row:
            continue  # decorative banner outside the settable area
        spanned = [c for c in range(min_col, max_col + 1) if c in settable_cols]
        if len(spanned) >= 2:
            out.fail(f"Merged range {rng} spans {len(spanned)} component/driver columns on a settable row.", sheet,
                     rng.split(":")[0])
    return "No merged range spans component or driver columns on settable rows."
