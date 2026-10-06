"""Allocation-sheet checks: formulas, ties, refoot, pro-rata parity, plugs, commitments, rounding, merges."""
from __future__ import annotations

import re
from decimal import Decimal

from openpyxl.utils.cell import range_boundaries

from collections import Counter

from src.pipeline.workbook.cells import CENT, sum_range_rows, PENNY, col_idx, col_letter, is_text, to_money
from src.pipeline.workbook.checks._common import (
    CheckContext,
    NotApplicable,
    Outcome,
    check,
    differs,
    mag,
    money,
    round_digits,
)
from src.pipeline.workbook.extract import AllocationData, AllocationVehicle, _dominant_formula, roll_forward_columns

ZERO = Decimal("0")
PARITY_TOLERANCE = Decimal("2.00")
_PLUG_RE = re.compile(r"^=\s*ROUND\(.*\)\s*([+-])\s*(\d+(?:\.\d+)?)\s*$", re.I)


def _driver_ref_re(column: str, row: int) -> re.Pattern:
    return re.compile(rf"(?<![A-Z]){re.escape(column)}\$?{row}(?!\d)")


def _active_in_vehicle(alloc: AllocationData, vehicle: AllocationVehicle):
    for comp in alloc.active_components:
        if vehicle.driver.get(comp.column, ZERO) != ZERO or any(i.amounts.get(comp.column) for i in vehicle.investors):
            yield comp


def _blank_driver(sheet, column: str, row: int) -> bool:
    cell = sheet.cell(f"{column}{row}")
    return cell is None or cell.value in (None, "") or to_money(cell.value) == ZERO


@check("CE-ALLOC-PER-LP-FORMULAS", needs=("allocation",))
def per_lp_formulas(ctx: CheckContext, out: Outcome) -> str:
    alloc = ctx.data.allocation
    sheet = alloc.sheet
    for vehicle in alloc.vehicles:
        for comp in alloc.active_components:
            amounts = {i.row: i.amounts.get(comp.column, ZERO) for i in vehicle.investors}
            blank_driver = _blank_driver(sheet, comp.column, vehicle.driver_row)
            if blank_driver and not any(amounts.values()):
                continue  # nothing allocated to this vehicle
            # A component nobody in the GP rows receives (e.g. carry paid to the LPs only).
            lp_only = all(amounts[i.row] == ZERO for i in vehicle.investors if i.is_gp)
            driver_ref = _driver_ref_re(comp.column, vehicle.driver_row)
            label = comp.header or comp.component_type
            for inv in vehicle.investors:
                cell = inv.cells.get(comp.column)
                coord = f"{comp.column}{inv.row}"
                if cell is None or cell.value is None:
                    continue
                if cell.formula is None:
                    value = to_money(cell.value)
                    if value == ZERO and inv.is_gp and lp_only:
                        out.notes.append(f"{coord} ({inv.name}) holds a typed 0 on the GP row of {label}, which the "
                                         "GP does not receive.")
                    elif value != ZERO and blank_driver:
                        out.fail(f"{coord} ({inv.name}, {label}) holds a typed value {money(value)} that acts as the "
                                 f"driver of this component: the vehicle driver cell {comp.column}{vehicle.driver_row} "
                                 "is blank and the per-LP formulas divide this cell instead.", sheet, coord)
                    else:
                        out.fail(f"{coord} ({inv.name}, {label}) holds a typed value {money(value)} instead of a "
                                 "driver formula.", sheet, coord)
                elif not blank_driver and "!" not in cell.formula \
                        and not driver_ref.search(cell.formula.replace("$", "")):
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
    # (b) per component, the (additive) vehicle totals sum to the fund-level amount. A look-through
    # block re-allocates amounts already counted in the other blocks and is left out.
    vehicles = alloc.additive_vehicles
    fund_label = "the fund-level amount" if not alloc.fund_driver_row_shared else "the sum of the vehicle drivers"
    for comp in alloc.components:
        vehicle_sum = sum((v.totals.get(comp.column, ZERO) for v in vehicles), ZERO)
        fund = alloc.fund_drivers.get(comp.column, ZERO)
        if differs(vehicle_sum, fund, PENNY):
            out.fail(f"{comp.header or comp.component_type}: vehicle totals sum to {money(vehicle_sum)} but "
                     f"{fund_label} is {money(fund)}.", sheet, f"{comp.column}{driver_row}")
    # (c) bottom-line totals equal the event gross.
    bottom_cols = [t.column for t in layout.event_total_columns]
    if layout.columns.cash_due:
        bottom_cols.append(layout.columns.cash_due)
    grand_row = layout.grand_total_row or (layout.vehicles[-1].subtotal_rows.total if len(layout.vehicles) == 1 else None)
    for column in bottom_cols:
        if grand_row is None:
            continue
        if alloc.fund_driver_row_shared:
            expected = sum((v.totals.get(column, ZERO) for v in vehicles), ZERO)
            where = "the vehicle totals"
        else:
            if sheet.value(f"{column}{driver_row}") is None:
                continue
            expected = _value(alloc, column, driver_row)
            where = f"the event gross {column}{driver_row}"
        grand = alloc.grand_totals.get(column, ZERO)
        if differs(grand, expected, PENNY):
            out.fail(f"Bottom-line total {column}{grand_row} ({money(grand)}) does not equal {where} "
                     f"({money(expected)}).", sheet, f"{column}{grand_row}")
    skipped = [v.name for v in alloc.vehicles if not v.additive]
    note = f" The look-through block(s) {', '.join(skipped)} re-allocate the other blocks and are not added." \
        if skipped else ""
    return f"Components, vehicle totals and bottom-line totals reconcile to the event gross ({money(alloc.event_gross)}).{note}"


def _numeric_columns(alloc: AllocationData) -> list[tuple[str, Decimal]]:
    layout = alloc.layout
    pct_tol = Decimal("0.000001")
    cols = [(layout.columns.commitment, PENNY), (layout.columns.commitment_pct, pct_tol)]
    cols += [(c.column, PENNY) for c in alloc.components]
    cols += [(t.column, PENNY) for t in layout.event_total_columns]
    cols += [(c, PENNY) for c in roll_forward_columns(layout).values()]
    if layout.columns.cash_due:
        cols.append((layout.columns.cash_due, PENNY))
    if layout.columns.tax_withholding:
        cols.append((layout.columns.tax_withholding, PENNY))
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
        # Percentages are 100% per vehicle and do not add across vehicles; look-through blocks are
        # re-allocations of the other blocks and are not added either.
        pct_columns = {layout.columns.commitment_pct, layout.columns.distribution_basis_pct}
        for column, tol in columns:
            if column in pct_columns:
                continue
            expected = sum((_value(alloc, column, v.subtotal_rows["total"]) for v in alloc.additive_vehicles
                            if v.subtotal_rows.get("total")), ZERO)
            stated = _value(alloc, column, layout.grand_total_row)
            if differs(stated, expected, tol):
                out.fail(f"Grand total {column}{layout.grand_total_row} is {stated} but vehicle totals sum to "
                         f"{expected}.", sheet, f"{column}{layout.grand_total_row}")
    _check_subtotal_ranges(alloc, out)
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


def _check_subtotal_ranges(alloc: AllocationData, out: Outcome) -> None:
    """Every SUM(X7:X58) on a subtotal row must span the block's investor rows, in every column of
    the sheet (hidden admin columns included): a range that starts late silently drops investors
    even where the mapped columns refoot."""
    sheet = alloc.sheet
    for vehicle in alloc.vehicles:
        lps = [i.row for i in vehicle.limited_partners]
        gps = [i.row for i in vehicle.investors if i.is_gp]
        for key, members in (("limited_partners", lps), ("general_partner", gps)):
            row = vehicle.subtotal_rows.get(key)
            if row is None or not members:
                continue
            first, last = min(members), max(members)
            for cell in sheet.row_cells(row):
                span = sum_range_rows(cell.formula)
                if span is None or cell.formula is None or not cell.formula.upper().startswith("=SUM("):
                    continue
                if span[0] > first or span[1] < last:
                    out.fail(f"{vehicle.name}: {key.replace('_', ' ')} subtotal {cell.coord} sums rows "
                             f"{span[0]}-{span[1]} but the block's rows run {first}-{last}.", sheet, cell.coord)


def _is_pro_rata(vehicle: AllocationVehicle, comp) -> bool:
    """A component is pro-rata when most of its per-LP cells are driver x % formulas.

    Columns pulled from another tab (e.g. management fees via SUMIFS on the fee tab) or
    typed in are not pro-rata allocations, so parity does not apply to them (FA calibration).
    """
    driver_ref = _driver_ref_re(comp.column, vehicle.driver_row)
    formulas = [i.formulas.get(comp.column) for i in vehicle.investors if i.amounts.get(comp.column)]
    if not formulas:
        return False
    linked = sum(1 for f in formulas if f and "!" not in f and driver_ref.search(f.replace("$", "")))
    return linked * 2 >= len(formulas)


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
    checked, skipped = set(), set()
    for vehicle in alloc.vehicles:
        no_basis: list[tuple[str, str]] = []
        for comp in _active_in_vehicle(alloc, vehicle):
            label = f"{vehicle.name} / {comp.header or comp.component_type}"
            if not _is_pro_rata(vehicle, comp):
                skipped.add(comp.header or comp.component_type)
                continue
            checked.add(comp.header or comp.component_type)
            rows = _parity_rows(alloc, vehicle, comp)
            if rows is None:
                shape, _ = _dominant_formula(sheet, comp.column, [i.row for i in vehicle.limited_partners])
                no_basis.append((comp.header or comp.component_type, shape or "typed"))
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
        if no_basis:
            # FA question outstanding: distributions allocated on commitment rather than contributed capital.
            shown = "; ".join(f"{name} ({shape[:70]})" for name, shape in no_basis[:4])
            out.review(f"{vehicle.name}: no allocation basis column (contributed capital) is mapped for "
                       f"{len(no_basis)} distribution component(s), so parity cannot be measured against the "
                       f"contributed-capital basis. The per-LP formulas seen: {shown}. Confirm the LPA basis for "
                       "distributions.", sheet, f"{alloc.layout.columns.commitment}{alloc.layout.header_row}")
    if skipped:
        out.notes.append(f"Not pro-rata, so not checked: {', '.join(sorted(skipped))}.")
    if not checked:
        raise NotApplicable("No active component is allocated pro-rata in this event.")
    return f"Every investor is within $2 of its pure pro-rata share (largest residual {money(worst)})."


def _plugs(alloc: AllocationData, vehicle: AllocationVehicle, column: str):
    for inv in vehicle.investors:
        formula = inv.formulas.get(column)
        match = _PLUG_RE.match(formula or "")
        if match:
            sign = -1 if match.group(1) == "-" else 1
            yield inv, Decimal(match.group(2)) * sign


def plug_eligibility(alloc: AllocationData):
    """eligible(investor) -> True / False / None (unknown): GPs, affiliates and fee-exempt LPs carry no plug."""
    fee_cols = [c.column for c in alloc.active_components if c.component_type == "mgmt_fee"]
    # "Fee-exempt" is relative to the investor's own block: in a block where nobody pays a fee
    # (e.g. the GP entity's own partners) the fee says nothing about eligibility.
    fee_paying_blocks = {v.name for v in alloc.vehicles
                         if any(i.amounts.get(c, ZERO) for i in v.investors for c in fee_cols)}

    def eligible(inv) -> bool | None:
        if inv.is_gp or inv.affiliate is True:
            return False
        if fee_cols and inv.vehicle in fee_paying_blocks and all(inv.amounts.get(c, ZERO) == ZERO for c in fee_cols) \
                and inv.commitment:
            return False
        if inv.affiliate is None and not fee_cols:
            return None
        return True

    return eligible


def rounding_unit(vehicle: AllocationVehicle, column: str) -> Decimal:
    """The unit the component's per-LP formulas round to: 10^-digits of their dominant ROUND(...)
    (whole dollars -> 1, cents -> 0.01); cents when no formula rounds."""
    digits = Counter(round_digits(inv.formulas.get(column)) for inv in vehicle.investors
                     if inv.formulas.get(column))
    digits.pop(None, None)
    if not digits:
        return Decimal("0.01")
    return Decimal(10) ** -digits.most_common(1)[0][0]


@check("CE-ALLOC-PLUG-DISCIPLINE", needs=("allocation",))
def plug_discipline(ctx: CheckContext, out: Outcome) -> str:
    """FA calibration: the residual may sit on one LP or be spread, but only over the largest
    eligible LPs and within the rounding ceiling (one rounding unit per LP) in total."""
    alloc = ctx.data.allocation
    sheet = alloc.sheet
    active_cols = {c.column for c in alloc.active_components}
    eligible = plug_eligibility(alloc)
    for vehicle in alloc.vehicles:
        lps = vehicle.limited_partners
        # Eligibility unknown (no affiliate flag, no fee) still ranks the LP: a plug belongs on the largest.
        ranked = sorted((i.commitment for i in lps if eligible(i) is not False), reverse=True)
        unknown: list[str] = []
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
            unit = rounding_unit(vehicle, comp.column)
            ceiling = Decimal(len(lps)) * unit
            total = sum((abs(offset) for _, offset in plugs), ZERO)
            if total > ceiling:
                names = ", ".join(f"{inv.name} {offset:+}" for inv, offset in plugs)
                out.fail(f"{label}: plugs total {total} ({names}), above the rounding ceiling {ceiling} "
                         f"({len(lps)} LPs x {unit}).", sheet, f"{comp.column}{plugs[0][0].row}")
            threshold = ranked[min(len(plugs), len(ranked)) - 1] if ranked else None
            for inv, offset in plugs:
                status = eligible(inv)
                coord = f"{comp.column}{inv.row}"
                if status is False:
                    out.fail(f"{label}: plug {offset:+} sits on {inv.name}, which is a GP, affiliate or fee-exempt "
                             "investor.", sheet, coord)
                    continue
                if status is None and inv.name not in unknown:
                    unknown.append(inv.name)
                if threshold is not None and inv.commitment < threshold:
                    out.fail(f"{label}: plug {offset:+} on {inv.name}, but {len(plugs)} plug(s) belong on the "
                             f"{len(plugs)} largest eligible LP(s) (commitment {money(threshold)} or more).", sheet,
                             coord)
        if unknown:
            shown = ", ".join(unknown[:5]) + (f" and {len(unknown) - 5} more" if len(unknown) > 5 else "")
            out.review(f"{vehicle.name}: the sheet has no affiliate flag and this event has no management-fee "
                       f"component, so plug eligibility cannot be confirmed for {len(unknown)} plugged LP(s) "
                       f"({shown}); the plugs do sit on the largest LPs.", sheet, None)
    return "Plugs sit only on the largest eligible LPs and stay within the rounding ceiling."


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
    """FA calibration: each component keeps one rounding basis; components may differ
    (e.g. fees in whole dollars, investment in cents). Roll-forward columns are checked for
    sub-cent precision too."""
    alloc = ctx.data.allocation
    sheet = alloc.sheet
    header_row = alloc.layout.header_row
    active = {c.column for c in alloc.active_components}
    scanned = [(c.column, c.header or c.component_type) for c in alloc.components]
    for name, column in roll_forward_columns(alloc.layout).items():
        header = sheet.value(f"{column}{header_row}")
        scanned.append((column, str(header).strip() if header else name.replace("_", " ")))
    for column, label in scanned:
        precise = [(inv, _value(alloc, column, inv.row)) for inv in alloc.investors
                   if _too_precise(_value(alloc, column, inv.row))]
        if precise:
            inv, amount = precise[0]
            out.fail(f"{column} ({label}): {len(precise)} cell(s) carry more than two decimals "
                     f"(e.g. {column}{inv.row} {inv.name} = {amount}).", sheet, f"{column}{inv.row}")
    for comp in alloc.components:
        values = [(inv, inv.amounts.get(comp.column, ZERO)) for inv in alloc.investors if inv.amounts.get(comp.column)]
        if comp.column not in active or not values:
            continue
        label = comp.header or comp.component_type
        digits = {round_digits(inv.formulas.get(comp.column)): inv for inv, _ in values}
        digits.pop(None, None)
        if 0 in digits and 2 in digits:
            out.fail(f"{label}: some per-LP cells round to whole dollars and others to cents.", sheet,
                     f"{comp.column}{digits[0].row}")
        elif set(digits) == {0}:
            with_cents = [inv for inv, v in values if _has_cents(v)]
            if with_cents:
                out.fail(f"{label}: rounded to whole dollars but {with_cents[0].name} carries cents.", sheet,
                         f"{comp.column}{with_cents[0].row}")
    return "No amount carries more than two decimals and each component keeps one rounding basis."


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
