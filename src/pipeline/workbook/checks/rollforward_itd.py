"""Roll-forward (Allocation) and ITD capital-activity checks."""
from __future__ import annotations

import re
from decimal import Decimal

from src.pipeline.workbook.cells import CENT, PENNY, to_date, to_money
from src.pipeline.workbook.checks._common import CheckContext, Outcome, check, differs, event_number, mag, money

ZERO = Decimal("0")


@check("CE-RF-FOOTING", needs=("allocation",))
def rf_footing(ctx: CheckContext, out: Outcome) -> str:
    alloc = ctx.data.allocation
    sheet = alloc.sheet
    rf = alloc.layout.roll_forward
    required = {"commitment": rf.commitment, "prior_contributions": rf.prior_contributions,
                "current_call": rf.current_call, "remaining_commitment": rf.remaining_commitment}
    missing = [k for k, v in required.items() if not v]
    if missing:
        out.review(f"Roll-forward column(s) not located: {', '.join(missing)}.")
        return ""
    for inv in alloc.investors:
        values = inv.roll_forward
        c, p, cur, rem = (values.get(k) or ZERO for k in ("commitment", "prior_contributions", "current_call",
                                                            "remaining_commitment"))
        pr = values.get("prior_recallable") or ZERO
        cr = values.get("current_recallable") or ZERO
        if not any((c, p, cur, rem)):
            continue
        conventions = [
            mag(c) - mag(p) - mag(cur),
            mag(c) - mag(p) - mag(pr) - mag(cur) - mag(cr),
            c + p + pr + cur + cr,
        ]
        cell = f"{rf.remaining_commitment}{inv.row}"
        if all(differs(value, rem, CENT) for value in conventions):
            out.fail(f"{inv.name}: commitment {money(c)} less prior {money(mag(p))} and current {money(mag(cur))} "
                     f"does not foot to remaining {money(rem)}.", sheet, cell)
        if mag(p) - mag(c) > CENT:
            out.fail(f"{inv.name}: prior contributions {money(mag(p))} exceed the commitment {money(c)}.", sheet,
                     f"{rf.prior_contributions}{inv.row}")
        if rem < -PENNY:
            out.fail(f"{inv.name}: remaining commitment is negative ({money(rem)}).", sheet, cell)
    # Refoot the roll-forward subtotals.
    for vehicle in alloc.vehicles:
        for key in ("limited_partners", "general_partner"):
            row = vehicle.subtotal_rows.get(key)
            if row is None:
                continue
            members = [i for i in vehicle.investors if i.is_gp == (key == "general_partner")]
            for name, column in rf.model_dump().items():
                if not column:
                    continue
                expected = sum((to_money(sheet.value(f"{column}{i.row}")) for i in members), ZERO)
                stated = to_money(sheet.value(f"{column}{row}"))
                if differs(stated, expected, CENT):
                    out.fail(f"{vehicle.name}: {key.replace('_', ' ')} subtotal of {name.replace('_', ' ')} "
                             f"({column}{row}) is {money(stated)} but its rows sum to {money(expected)}.", sheet,
                             f"{column}{row}")
    return "Every investor's roll-forward foots, no investor is over-contributed and no remaining commitment is negative."


def _blank(value) -> bool:
    return value is None or value == "" or (isinstance(value, (int, float)) and value == 0)


@check("CE-RF-CURRENT-CALL-LINK", needs=("allocation",))
def rf_current_call_link(ctx: CheckContext, out: Outcome) -> str:
    alloc = ctx.data.allocation
    sheet = alloc.sheet
    rf = alloc.layout.roll_forward
    if not rf.current_call:
        raise_na = "The sheet has no Current Capital Call roll-forward column."
        from src.pipeline.workbook.checks._common import NotApplicable

        raise NotApplicable(raise_na)
    call_cols = [c.column for c in alloc.active_components if c.side == "call"]
    for inv in alloc.investors:
        expected = sum((inv.amounts.get(c, ZERO) for c in call_cols), ZERO)
        current = inv.roll_forward.get("current_call") or ZERO
        coord = f"{rf.current_call}{inv.row}"
        if differs(mag(current), mag(expected), PENNY):
            out.fail(f"{inv.name}: Current Capital Call {money(mag(current))} does not equal this event's call total "
                     f"{money(expected)}.", sheet, coord)
        cell = sheet.cell(coord)
        if cell is not None and cell.value not in (None, 0) and cell.formula is None:
            out.fail(f"{coord} ({inv.name}) Current Capital Call is a typed value, not a link to this event.", sheet,
                     coord)
        if rf.remaining_commitment:
            remaining = sheet.cell(f"{rf.remaining_commitment}{inv.row}")
            if remaining is not None and remaining.value is not None and remaining.formula is None:
                out.fail(f"{rf.remaining_commitment}{inv.row} ({inv.name}) Remaining Commitment is a typed value.",
                         sheet, f"{rf.remaining_commitment}{inv.row}")
        for field_name, column in (("received", alloc.layout.columns.received),
                                   ("received date", alloc.layout.columns.received_date)):
            if column and not _blank(sheet.value(f"{column}{inv.row}")):
                out.fail(f"{column}{inv.row} ({inv.name}) {field_name} is still populated for an unfunded event.",
                         sheet, f"{column}{inv.row}")
    return "Current Capital Call links to this event's per-LP totals and the funding cells are blank."


def _allocation_sheet_names(ctx: CheckContext) -> list[str]:
    names = [name for name, layout in ctx.data.layouts.items() if getattr(layout, "role", None) == "allocation"]
    return names


def _references(formula: str, sheet_name: str) -> bool:
    quoted = "'" + sheet_name.replace("'", "''") + "'!"
    return quoted.lower() in formula.lower() or re.search(rf"(?<![A-Za-z0-9_']){re.escape(sheet_name)}!", formula,
                                                          re.I) is not None


@check("CE-ITD-PRIOR-FROZEN", needs=("itd",))
def itd_prior_frozen(ctx: CheckContext, out: Outcome) -> str:
    itd = ctx.data.itd
    sheet = itd.sheet
    targets = _allocation_sheet_names(ctx)
    if itd.current_block is None:
        out.review("The current event block could not be identified, so prior blocks cannot be isolated.")
        return ""
    for block in itd.prior_blocks:
        columns = itd.block_columns(block)
        for inv in itd.investors:
            for column in columns:
                formula = inv.formulas.get(column)
                if not formula:
                    continue
                live = any(_references(formula, t) for t in targets) if targets else "!" in formula
                if not live:
                    continue
                message = (f"{column}{inv.row} ({inv.name}) in prior block '{block.label}' is a live formula "
                           f"{formula[:60]} into the Allocation sheet")
                if inv.values.get(column, ZERO):
                    out.fail(message + ".", sheet, f"{column}{inv.row}")
                else:
                    # FA calibration: a live link that currently reads $0 is a warning, not a failure.
                    out.review(message + " (currently $0).", sheet, f"{column}{inv.row}")
    return "Prior ITD event blocks hold frozen values; only the current block links to the Allocation sheet."


_CUM_CATEGORIES = ("investment_contributions", "cost_contributions", "recallable_distributions",
                   "non_recallable_distributions")


@check("CE-ITD-CUMULATIVE", needs=("itd",))
def itd_cumulative(ctx: CheckContext, out: Outcome) -> str:
    itd = ctx.data.itd
    sheet = itd.sheet
    cum_cols = itd.layout.cumulative_columns.model_dump()
    for inv in itd.investors:
        sums = {cat: sum((inv.values.get(col, ZERO) for col, cats in itd.marks.items() if cat in cats), ZERO)
                for cat in _CUM_CATEGORIES}
        for cat in _CUM_CATEGORIES:
            column = cum_cols.get(cat)
            stated = inv.cumulative.get(cat)
            if column and stated is not None and differs(stated, sums[cat], CENT):
                out.fail(f"{inv.name}: {cat.replace('_', ' ')} {money(stated)} ({column}{inv.row}) does not equal "
                         f"its classified event columns {money(sums[cat])}.", sheet, f"{column}{inv.row}")
        total = inv.cumulative.get("total_contributions")
        if total is not None and cum_cols.get("total_contributions"):
            expected = sums["investment_contributions"] + sums["cost_contributions"]
            if differs(total, expected, CENT):
                out.fail(f"{inv.name}: Total Contributions {money(total)} does not equal classified contributions "
                         f"{money(expected)}.", sheet, f"{cum_cols['total_contributions']}{inv.row}")
        unfunded = inv.cumulative.get("unfunded")
        commitment = inv.cumulative.get("commitment")
        if unfunded is not None and commitment is not None and total is not None:
            recallable = inv.cumulative.get("recallable_distributions") or ZERO
            options = [commitment - total, commitment - total - recallable, commitment - total + mag(recallable)]
            if all(differs(unfunded, value, CENT) for value in options):
                out.fail(f"{inv.name}: Unfunded {money(unfunded)} does not equal commitment less contributions "
                         f"{money(commitment - total)}.", sheet, f"{cum_cols['unfunded']}{inv.row}")
    for vehicle in itd.vehicles:
        row = vehicle.subtotal_rows.get("limited_partners")
        if row is None:
            continue
        lps = [i for i in vehicle.investors if not i.is_gp]
        for key, column in cum_cols.items():
            if not column:
                continue
            expected = sum((inv.cumulative.get(key) or ZERO for inv in lps), ZERO)
            stated = to_money(sheet.value(f"{column}{row}"))
            if differs(stated, expected, CENT):
                out.fail(f"{vehicle.name}: Limited Partners subtotal {column}{row} is {money(stated)} but its rows "
                         f"sum to {money(expected)}.", sheet, f"{column}{row}")
    return "ITD cumulative columns equal their classified event columns, Unfunded foots and subtotals refoot."


_DATE_IN_LABEL = re.compile(r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})")


def _families(block) -> list[str]:
    """Numbering families an event block may continue, in order of preference."""
    if block.event_type == "capital_call":
        return ["call"]
    if block.event_type == "distribution":
        return ["distribution"]
    return ["distribution", "call"] if "distribution" in block.label.lower() else ["call", "distribution"]


@check("CE-ITD-EVENT-SEQUENCE", needs=("itd",))
def itd_event_sequence(ctx: CheckContext, out: Outcome) -> str:
    itd = ctx.data.itd
    sheet = itd.sheet
    header_row = itd.layout.event_header_row
    last_by_family: dict[str, int | None] = {"call": None, "distribution": None}
    seen: dict[str, set[int]] = {"call": set(), "distribution": set()}
    last_date = None
    for block in itd.event_blocks:
        if block.event_type in ("transfer", "other"):
            continue  # FA calibration: transfers do not touch the numbering
        cell = f"{block.first_column}{header_row}"
        number = event_number(block.label) or block.number
        match = _DATE_IN_LABEL.search(block.label)
        day = to_date(match.group(1).replace("-", ".").replace("/", ".")) if match else to_date(block.date)
        if number is None or (match and day is None):
            out.review(f"Event header '{block.label}' has no machine-readable number or date.", sheet, cell)
            continue
        families = _families(block)
        if block.event_type == "net_event":
            # FA calibration: a net event continues the call counter, the distribution counter,
            # or both, depending on the client.
            matched = [f for f in families if last_by_family[f] is not None and number == last_by_family[f] + 1]
            if not matched:
                fresh = [f for f in families if last_by_family[f] is None]
                matched = fresh[:1]
            if not matched:
                expected = " or ".join(f"#{last_by_family[f] + 1}" for f in families if last_by_family[f] is not None)
                out.fail(f"'{block.label}' continues neither the call nor the distribution numbering "
                         f"(expected {expected}).", sheet, cell)
                matched = []
        else:
            family = families[0]
            previous = last_by_family[family]
            if number in seen[family]:
                out.fail(f"'{block.label}' repeats event number {number}.", sheet, cell)
            elif previous is not None and number != previous + 1:
                out.fail(f"'{block.label}' follows #{previous} of the same type; expected #{previous + 1}.", sheet,
                         cell)
            matched = [family]
        for family in matched:
            seen[family].add(number)
            last_by_family[family] = number
        if day is not None:
            if last_date is not None and day < last_date:
                out.fail(f"'{block.label}' is dated before the event to its left ({last_date.isoformat()}).", sheet,
                         cell)
            last_date = day
    return "Event numbers are sequential per event type and event dates run forward."
