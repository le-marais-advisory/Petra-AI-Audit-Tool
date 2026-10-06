"""Roll-forward (Allocation) and ITD capital-activity checks."""
from __future__ import annotations

import re
from decimal import Decimal

from src.pipeline.workbook.cells import CENT, PENNY, col_idx, col_letter, to_date, to_money
from src.pipeline.workbook.checks._common import (
    CheckContext,
    Outcome,
    check,
    differs,
    event_number,
    event_numbers,
    mag,
    money,
)

ZERO = Decimal("0")


def _unclassified_span_columns(alloc) -> list[str]:
    """Columns between the commitment and remaining-commitment columns that are not mapped as a
    roll-forward column (adjustments included) but hold amounts on investor rows."""
    rf = alloc.layout.roll_forward
    if not rf.commitment or not rf.remaining_commitment:
        return []
    mapped = {c for c in rf.model_dump().values() if isinstance(c, str)} | set(rf.adjustments)
    first, last = sorted((col_idx(rf.commitment), col_idx(rf.remaining_commitment)))
    found = []
    for index in range(first + 1, last):
        column = col_letter(index)
        if column in mapped:
            continue
        if any(to_money(alloc.sheet.value(f"{column}{inv.row}")) for inv in alloc.investors):
            found.append(column)
    return found


@check("CE-RF-FOOTING", needs=("allocation",))
def rf_footing(ctx: CheckContext, out: Outcome) -> str:
    alloc = ctx.data.allocation
    sheet = alloc.sheet
    rf = alloc.layout.roll_forward
    header_row = alloc.layout.header_row
    required = {"commitment": rf.commitment, "prior_contributions": rf.prior_contributions,
                "current_call": rf.current_call, "remaining_commitment": rf.remaining_commitment}
    missing = [k for k, v in required.items() if not v]
    if missing:
        out.review(f"Roll-forward column(s) not located: {', '.join(missing)}.")
        return ""
    adjustments = list(rf.adjustments)
    unclassified = _unclassified_span_columns(alloc)

    def header(column: str) -> str:
        text = sheet.value(f"{column}{header_row}")
        return str(text).strip() if text is not None else column

    if unclassified:
        names = ", ".join(f"{c} ({header(c)})" for c in unclassified)
        out.review(f"Column(s) {names} sit between the commitment and remaining-commitment columns and hold amounts "
                   "but are not classified as roll-forward columns; they are not included in the footing.", sheet,
                   f"{unclassified[0]}{header_row}")
    # A fund that recycles distributions calls more than the commitment over its life; the
    # recallable column then adds back the recycled capital. Over-contribution is a question
    # for the accountant there, not an arithmetic failure.
    recycles = any(mag(i.roll_forward.get("prior_recallable")) > CENT or mag(i.roll_forward.get("current_recallable")) > CENT
                   for i in alloc.investors)
    over = 0
    for inv in alloc.investors:
        values = inv.roll_forward
        c, p, cur, rem = (values.get(k) or ZERO for k in ("commitment", "prior_contributions", "current_call",
                                                            "remaining_commitment"))
        pr = values.get("prior_recallable") or ZERO
        cr = values.get("current_recallable") or ZERO
        adj = sum((to_money(sheet.value(f"{a}{inv.row}")) for a in adjustments), ZERO)
        if not any((c, p, cur, rem, adj)):
            continue
        conventions = [
            mag(c) - mag(p) - mag(cur),
            mag(c) - mag(p) - mag(pr) - mag(cur) - mag(cr),
            c + p + pr + cur + cr,
        ]
        if adjustments and adj:
            # A row with an adjustment (waiver, transfer) must include it: the signed sum of every mapped
            # roll-forward column, or the magnitude conventions plus the signed adjustment.
            conventions = [c + p + pr + cur + cr + adj, mag(c) - mag(p) - mag(pr) - mag(cur) - mag(cr) + adj,
                           mag(c) - mag(p) - mag(cur) + adj]
        cell = f"{rf.remaining_commitment}{inv.row}"
        if all(differs(value, rem, CENT) for value in conventions):
            extra = sum((to_money(sheet.value(f"{u}{inv.row}")) for u in unclassified), ZERO)
            if unclassified and not differs(c + p + pr + cur + cr + adj + extra, rem, CENT):
                out.review(f"{inv.name}: the roll-forward foots to remaining {money(rem)} only when the unclassified "
                           f"column(s) {', '.join(unclassified)} ({money(extra)}) are included; confirm they belong "
                           "in the roll-forward.", sheet, cell)
            else:
                out.fail(f"{inv.name}: commitment {money(c)} less prior {money(mag(p))} and current {money(mag(cur))}"
                         + (f" and adjustments {money(adj)}" if adjustments else "")
                         + f" does not foot to remaining {money(rem)}.", sheet, cell)
        if mag(p) - mag(c) > CENT:
            over += 1
            if recycles:
                if over == 1:
                    out.review(f"{inv.name}: prior contributions {money(mag(p))} exceed the commitment {money(c)}; "
                               "the fund models recallable distributions (recycling), so confirm the recycled "
                               "amount against the LPA cap.", sheet, f"{rf.prior_contributions}{inv.row}")
            else:
                out.fail(f"{inv.name}: prior contributions {money(mag(p))} exceed the commitment {money(c)}.", sheet,
                         f"{rf.prior_contributions}{inv.row}")
        if rem < -PENNY:
            out.fail(f"{inv.name}: remaining commitment is negative ({money(rem)}).", sheet, cell)
    # Refoot the roll-forward subtotals.
    rf_columns = {name: col for name, col in rf.model_dump().items() if isinstance(col, str)}
    rf_columns.update({f"adjustment {a}": a for a in adjustments})
    for vehicle in alloc.vehicles:
        for key in ("limited_partners", "general_partner"):
            row = vehicle.subtotal_rows.get(key)
            if row is None:
                continue
            members = [i for i in vehicle.investors if i.is_gp == (key == "general_partner")]
            for name, column in rf_columns.items():
                expected = sum((to_money(sheet.value(f"{column}{i.row}")) for i in members), ZERO)
                stated = to_money(sheet.value(f"{column}{row}"))
                if differs(stated, expected, CENT):
                    out.fail(f"{vehicle.name}: {key.replace('_', ' ')} subtotal of {name.replace('_', ' ')} "
                             f"({column}{row}) is {money(stated)} but its rows sum to {money(expected)}.", sheet,
                             f"{column}{row}")
    if recycles and over:
        out.review(f"{over} investor(s) have called more than their commitment; the recallable column accounts "
                   "for the recycled capital and every row still foots.")
    note = ""
    if adjustments:
        note = " The adjustment column(s) " + ", ".join(f"{a} ({header(a)})" for a in adjustments) + " are included."
    return ("Every investor's roll-forward foots, no investor is over-contributed and no remaining commitment is "
            f"negative.{note}")


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
    layout = itd.layout
    targets = _allocation_sheet_names(ctx)
    if itd.current_block is None:
        out.review("The current event block could not be identified, so prior blocks cannot be isolated.")
        return ""

    def live(formula: str | None) -> bool:
        if not formula:
            return False
        return any(_references(formula, t) for t in targets) if targets else "!" in formula

    # Every row of each vehicle span (blank template rows included) plus the sub-header row.
    span_rows: list[int] = []
    for vehicle in layout.vehicles:
        first = vehicle.investor_rows[0] if vehicle.investor_rows else None
        last = max([vehicle.subtotal_rows.total or 0, vehicle.subtotal_rows.general_partner or 0,
                    vehicle.subtotal_rows.limited_partners or 0] + list(vehicle.gp_rows)
                   + (vehicle.investor_rows[-1:] if vehicle.investor_rows else []))
        if first:
            span_rows += [r for r in range(first, last + 1)]
    investors = {inv.row: inv for inv in itd.investors}
    for block in itd.prior_blocks:
        columns = itd.block_columns(block) + ([block.total_column] if block.total_column else [])
        for column in columns:
            header = sheet.cell(f"{column}{layout.subheader_row}")
            if header is not None and live(header.formula):
                if header.is_error:
                    out.fail(f"{header.coord}: the sub-header of prior block '{block.label}' is a live link "
                             f"{header.formula[:60]} into the Allocation sheet and shows {header.value}.", sheet,
                             header.coord)
                else:
                    out.fail(f"{header.coord}: the sub-header of prior block '{block.label}' is a live link "
                             f"{header.formula[:60]} into the Allocation header row, so the block is relabelled by "
                             f"whatever that header says now ({str(header.value)[:40]!r}).", sheet, header.coord)
            for row in span_rows:
                cell = sheet.cell(f"{column}{row}")
                if cell is None or not live(cell.formula):
                    continue
                who = investors.get(row)
                label = f"{column}{row}" + (f" ({who.name})" if who else "")
                message = (f"{label} in prior block '{block.label}' is a live formula {cell.formula[:60]} into the "
                           "Allocation sheet")
                if cell.is_error:
                    out.fail(message + f" showing {cell.value}.", sheet, cell.coord)
                elif to_money(cell.value):
                    out.fail(message + ".", sheet, cell.coord)
                else:
                    # FA calibration: a live link that currently reads $0 is a warning, not a failure.
                    out.review(message + " (currently $0).", sheet, cell.coord)
    return "Prior ITD event blocks hold frozen values; only the current block links to the Allocation sheet."


_CUM_CATEGORIES = ("investment_contributions", "cost_contributions", "recallable_distributions",
                   "non_recallable_distributions")



def _explain_difference(itd, cat: str, mismatches: list) -> str:
    """Name the block column(s) whose values equal the difference on most mismatching rows."""
    sheet = itd.sheet
    candidates = [c for c, cats in itd.marks.items() if cat not in cats]
    needed = max(1, (len(mismatches) * 4 + 4) // 5)  # 80 %

    def explains(columns: list[str]) -> int:
        hits = 0
        for inv, _, _, diff in mismatches:
            total = sum((inv.values.get(c, ZERO) for c in columns), ZERO)
            if not differs(diff, total, CENT) or not differs(diff, -total, CENT):
                hits += 1
        return hits

    def describe(column: str) -> str:
        header = sheet.value(f"{column}{itd.layout.subheader_row}")
        block = next((b.label for b in itd.event_blocks if column in itd.block_columns(b)), None)
        text = f"{column}"
        if header is not None:
            text += f" ({str(header).strip()[:40]}"
            text += f", block '{block}')" if block else ")"
        elif block:
            text += f" (block '{block}')"
        return text

    best = max(((explains([c]), c) for c in candidates), default=(0, None))
    if best[0] >= needed:
        return f"the difference equals column {describe(best[1])} on {best[0]} of them"
    unclassified = [c for c, cats in itd.marks.items() if not cats]
    if unclassified and explains(unclassified) >= needed:
        return (f"the difference equals the sum of the unclassified column(s) "
                f"{', '.join(describe(c) for c in unclassified[:6])} on {explains(unclassified)} of them")
    return "no event column explains the difference"


@check("CE-ITD-CUMULATIVE", needs=("itd",))
def itd_cumulative(ctx: CheckContext, out: Outcome) -> str:
    itd = ctx.data.itd
    sheet = itd.sheet
    cum_cols = itd.layout.cumulative_columns.model_dump()
    derived = itd.derived_cumulatives & set(_CUM_CATEGORIES)
    for cat in sorted(derived):
        # E.g. a recycling fund's "Recallable Distributions" = -(Unfunded + Contributions - Commitment):
        # the recyclable room under the LPA cap, not a sum of the marked distribution columns.
        column = cum_cols[cat]
        first = next((i for i in itd.investors if i.formulas.get(column) or sheet.cell(f"{column}{i.row}")), None)
        formula = (sheet.cell(f"{column}{first.row}").formula if first else None) or ""
        out.review(f"{cat.replace('_', ' ').capitalize()} ({column}) is derived by formula ({formula[:60]}) rather "
                   "than accumulated from the marked event columns, so it cannot be reconciled to them; confirm "
                   "the basis (e.g. a recycling cap) against the LPA.", sheet,
                   f"{column}{first.row}" if first else None)
    sums = {inv.row: {cat: sum((inv.values.get(col, ZERO) for col, cats in itd.marks.items() if cat in cats), ZERO)
                      for cat in _CUM_CATEGORIES} for inv in itd.investors}
    # Each cumulative column must equal the sum of the block columns classified into it. The same
    # difference on most rows is one finding naming the column(s) that explain it, not one per investor.
    for cat in _CUM_CATEGORIES:
        column = cum_cols.get(cat)
        if cat in derived or not column:
            continue
        mismatches = []
        checked = 0
        for inv in itd.investors:
            stated = inv.cumulative.get(cat)
            if stated is None:
                continue
            checked += 1
            classified = sums[inv.row][cat]
            if differs(stated, classified, CENT) and differs(stated, -classified, CENT):
                diff = stated - classified if abs(stated - classified) <= abs(stated + classified) else stated + classified
                mismatches.append((inv, stated, classified, diff))
        if not mismatches:
            continue
        inv, stated, classified, _ = mismatches[0]
        label = cat.replace("_", " ")
        if len(mismatches) == 1:
            out.fail(f"{inv.name}: {label} {money(stated)} ({column}{inv.row}) does not equal its classified event "
                     f"columns {money(classified)}.", sheet, f"{column}{inv.row}")
        else:
            out.fail(f"{label.capitalize()} ({column}): {len(mismatches)} of {checked} investor rows do not equal "
                     f"their classified event columns; {_explain_difference(itd, cat, mismatches)} "
                     f"(e.g. {inv.name}: {money(stated)} vs {money(classified)} at {column}{inv.row}).", sheet,
                     f"{column}{inv.row}")
    total_col = cum_cols.get("total_contributions")
    unfunded_col = cum_cols.get("unfunded")
    total_misses, unfunded_misses = [], []
    for inv in itd.investors:
        total = inv.cumulative.get("total_contributions")
        if total is not None and total_col and "total_contributions" not in itd.derived_cumulatives:
            expected = sums[inv.row]["investment_contributions"] + sums[inv.row]["cost_contributions"]
            if differs(total, expected, CENT):
                total_misses.append((inv, total, expected))
        unfunded = inv.cumulative.get("unfunded")
        commitment = inv.cumulative.get("commitment")
        if unfunded is not None and commitment is not None and total is not None and unfunded_col \
                and not {"unfunded", "recallable_distributions"} & itd.derived_cumulatives:
            recallable = inv.cumulative.get("recallable_distributions") or ZERO
            options = [commitment - total, commitment - total - recallable, commitment - total + mag(recallable)]
            if all(differs(unfunded, value, CENT) for value in options):
                unfunded_misses.append((inv, unfunded, commitment - total))
    if unfunded_misses:
        # An unmapped numeric column among the cumulative columns (e.g. a waiver adjustment) that explains
        # every Unfunded difference is a review item, not a failure.
        explained = _unmapped_cumulative_explains(itd, unfunded_misses)
        if explained:
            column, header = explained
            inv, stated, expected = unfunded_misses[0]
            out.review(f"Unfunded ({unfunded_col}): {len(unfunded_misses)} investor row(s) equal commitment less "
                       f"contributions only once the unmapped column {column} ({header}) is included (e.g. {inv.name}: "
                       f"{money(stated)} vs {money(expected)} at {unfunded_col}{inv.row}); confirm the column belongs "
                       "in the unfunded commitment.", sheet, f"{column}{inv.row}")
            unfunded_misses = []
    for misses, what, column in ((total_misses, "Total Contributions", total_col),
                                 (unfunded_misses, "Unfunded", unfunded_col)):
        if not misses:
            continue
        inv, stated, expected = misses[0]
        basis = "classified contributions" if what == "Total Contributions" else "commitment less contributions"
        if len(misses) == 1:
            out.fail(f"{inv.name}: {what} {money(stated)} ({column}{inv.row}) does not equal {basis} "
                     f"{money(expected)}.", sheet, f"{column}{inv.row}")
        else:
            out.fail(f"{what} ({column}): {len(misses)} investor rows do not equal {basis} (e.g. {inv.name}: "
                     f"{money(stated)} vs {money(expected)} at {column}{inv.row}).", sheet, f"{column}{inv.row}")
    _refoot_itd_totals(itd, out)
    return "ITD cumulative columns equal their classified event columns, Unfunded foots and subtotals refoot."


def _unmapped_cumulative_explains(itd, misses: list) -> tuple[str, str] | None:
    """(column, header) of an unmapped numeric column between the cumulative columns whose value (either
    sign) closes the Unfunded gap on every mismatching row."""
    sheet = itd.sheet
    mapped = {c for c in itd.layout.cumulative_columns.model_dump().values() if c}
    if not mapped:
        return None
    first, last = min(col_idx(c) for c in mapped), max(col_idx(c) for c in mapped)
    block_cols = {c for b in itd.event_blocks for c in itd.block_columns(b)}
    for index in range(first, last + 1):
        column = col_letter(index)
        if column in mapped or column in block_cols or column in itd.check_columns:
            continue
        values = [to_money(sheet.value(f"{column}{inv.row}")) for inv, _, _ in misses]
        if not any(values):
            continue
        gaps = [stated - expected for _, stated, expected in misses]
        if all(not differs(gap, v, CENT) or not differs(gap, -v, CENT) for gap, v in zip(gaps, values)):
            header = sheet.value(f"{column}{itd.layout.subheader_row}")
            return column, str(header).strip() if header else "no header"
    return None


def _refoot_itd_totals(itd, out: Outcome) -> None:
    """LP / GP subtotals equal their rows and each total row equals LP + GP, per block and cumulative column."""
    sheet = itd.sheet
    cum_cols = [c for c in itd.layout.cumulative_columns.model_dump().values() if c]
    block_cols = [c for b in itd.event_blocks for c in itd.block_columns(b) + ([b.total_column] if b.total_column else [])]
    for vehicle in itd.vehicles:
        lp_row = vehicle.subtotal_rows.get("limited_partners")
        gp_row = vehicle.subtotal_rows.get("general_partner")
        total_row = vehicle.subtotal_rows.get("total")
        lps = [i for i in vehicle.investors if not i.is_gp]
        gps = [i for i in vehicle.investors if i.is_gp]
        for column in cum_cols + block_cols:
            def value_of(inv, col=column):
                return inv.cumulative.get(_cum_key(itd, col)) if col in cum_cols else inv.values.get(col)
            lp_sum = sum((value_of(i) or ZERO for i in lps), ZERO)
            gp_sum = sum((value_of(i) or ZERO for i in gps), ZERO)
            if lp_row is not None and sheet.cell(f"{column}{lp_row}") is not None:
                stated = to_money(sheet.value(f"{column}{lp_row}"))
                if differs(stated, lp_sum, CENT):
                    out.fail(f"{vehicle.name}: Limited Partners subtotal {column}{lp_row} is {money(stated)} but its "
                             f"rows sum to {money(lp_sum)}.", sheet, f"{column}{lp_row}")
            if gp_row is not None and gps and sheet.cell(f"{column}{gp_row}") is not None:
                stated = to_money(sheet.value(f"{column}{gp_row}"))
                if differs(stated, gp_sum, CENT):
                    out.fail(f"{vehicle.name}: General Partner subtotal {column}{gp_row} is {money(stated)} but its "
                             f"rows sum to {money(gp_sum)}.", sheet, f"{column}{gp_row}")
            if total_row is not None and sheet.cell(f"{column}{total_row}") is not None:
                stated = to_money(sheet.value(f"{column}{total_row}"))
                if lp_row is not None or gp_row is not None:
                    parts = [to_money(sheet.value(f"{column}{r}")) for r in (lp_row, gp_row) if r is not None]
                    expected = sum(parts, ZERO)
                    where = "the LP and GP subtotals" if len(parts) == 2 else "its subtotal"
                else:
                    expected = lp_sum + gp_sum
                    where = "its rows"
                if differs(stated, expected, CENT):
                    out.fail(f"{vehicle.name}: total row {column}{total_row} is {money(stated)} but {where} add up "
                             f"to {money(expected)}.", sheet, f"{column}{total_row}")


def _cum_key(itd, column: str) -> str | None:
    return next((k for k, c in itd.layout.cumulative_columns.model_dump().items() if c == column), None)


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
    """The current block must continue its family's numbering and chronology. Gaps, repeats and
    unnumbered headers among the prior blocks are history the accountant did not write this event:
    they are reported once, for review."""
    itd = ctx.data.itd
    sheet = itd.sheet
    header_row = itd.layout.event_header_row
    last_by_family: dict[str, int | None] = {"call": None, "distribution": None}
    seen: dict[str, set[int]] = {"call": set(), "distribution": set()}
    last_date = None
    history: list[str] = []

    def report(block, message: str, cell: str) -> None:
        if block.is_current:
            out.fail(message, sheet, cell)
        else:
            history.append(message)

    for block in itd.event_blocks:
        if block.event_type in ("transfer", "other"):
            continue  # FA calibration: transfers do not touch the numbering
        cell = f"{block.first_column}{header_row}"
        numbers = event_numbers(block.label)
        number = event_number(block.label) or block.number
        match = _DATE_IN_LABEL.search(block.label)
        day = to_date(match.group(1).replace("-", ".").replace("/", ".")) if match else to_date(block.date)
        if number is None or (match and day is None):
            message = f"Event header '{block.label}' has no machine-readable number or date."
            if block.is_current:
                out.review(message, sheet, cell)
            else:
                history.append(message)
            continue
        families = _families(block)
        if len(numbers) == 2:
            # A combined label ("Capital Call #9 & Distribution #1"): each family continues its own counter.
            matched = []
            for family, own in numbers.items():
                previous = last_by_family[family]
                if own in seen[family]:
                    report(block, f"'{block.label}' repeats {family} number {own}.", cell)
                elif previous is not None and own != previous + 1:
                    report(block, f"'{block.label}' follows {family} #{previous}; expected #{previous + 1}.", cell)
                seen[family].add(own)
                last_by_family[family] = own
        elif block.event_type == "net_event":
            # FA calibration: a net event continues the call counter, the distribution counter,
            # or both, depending on the client.
            matched = [f for f in families if last_by_family[f] is not None and number == last_by_family[f] + 1]
            if not matched:
                fresh = [f for f in families if last_by_family[f] is None]
                matched = fresh[:1]
            if not matched:
                expected = " or ".join(f"#{last_by_family[f] + 1}" for f in families if last_by_family[f] is not None)
                report(block, f"'{block.label}' continues neither the call nor the distribution numbering "
                              f"(expected {expected}).", cell)
                matched = []
        else:
            family = families[0]
            previous = last_by_family[family]
            if number in seen[family]:
                report(block, f"'{block.label}' repeats event number {number}.", cell)
            elif previous is not None and number != previous + 1:
                report(block, f"'{block.label}' follows #{previous} of the same type; expected #{previous + 1}.", cell)
            matched = [family]
        for family in matched:
            seen[family].add(number)
            last_by_family[family] = number
        if day is not None:
            if last_date is not None and day < last_date:
                report(block, f"'{block.label}' is dated before the event to its left ({last_date.isoformat()}).", cell)
            last_date = day
    if history:
        shown = "; ".join(history[:6]) + (f"; and {len(history) - 6} more" if len(history) > 6 else "")
        out.review(f"{len(history)} historical header issue(s) among the prior blocks (not written this event): "
                   f"{shown}", sheet, f"{itd.event_blocks[0].first_column}{header_row}" if itd.event_blocks else None)
    return "Event numbers are sequential per event type and event dates run forward."

