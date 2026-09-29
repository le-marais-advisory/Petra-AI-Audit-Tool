"""Cross-sheet tie-outs: management fees, ITD vs Allocation, investor identity, return of capital."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from src.pipeline.workbook.cells import CENT, PENNY, norm_text
from src.pipeline.workbook.checks._common import (
    CheckContext,
    NotApplicable,
    Outcome,
    check,
    differs,
    mag,
    money,
    quarter_key,
    round_digits,
)

ZERO = Decimal("0")


def _round(value: Decimal, digits: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)


@check("CE-TIE-MGMT-FEE", needs=("allocation",))
def tie_mgmt_fee(ctx: CheckContext, out: Outcome) -> str:
    alloc = ctx.data.allocation
    fee_comps = [c for c in alloc.active_components if c.component_type == "mgmt_fee"]
    if not fee_comps:
        raise NotApplicable("The current event has no management-fee component.")
    fee = ctx.data.mgmt_fee
    if fee is None:
        out.review("A management-fee component is allocated but the fee tab layout could not be mapped.")
        return ""
    sheet, fee_sheet = alloc.sheet, fee.sheet
    fee_cols = [f.column for f in fee.layout.fee_columns]
    if len(fee_cols) != len(fee_comps):
        out.review(f"{len(fee_comps)} Allocation fee column(s) but {len(fee_cols)} fee-tab period column(s); "
                   "per-period ties cannot be paired.")
        return ""
    rows = {r.name: r for r in fee.rows}
    alloc_by_name = {i.name: i for i in alloc.investors}
    for index, (comp, column) in enumerate(zip(fee_comps, fee_cols)):
        # (4) the fee tab holds the current period.
        alloc_period = quarter_key(comp.header)
        tab_period = quarter_key(fee.period_labels[index] if index < len(fee.period_labels) else "")
        if alloc_period and tab_period and alloc_period != tab_period:
            out.fail(f"The fee tab column {column} is for {fee.period_labels[index]!r} but the Allocation fee "
                     f"component is {comp.header!r} (stale period).", fee_sheet, f"{column}{fee.layout.header_row}")
        # (2) totals tie to the fee driver.
        total = fee.totals.get(column, ZERO)
        driver = alloc.fund_drivers.get(comp.column, ZERO)
        if differs(total, driver, PENNY):
            out.fail(f"Fee tab total {money(total)} ({column}) does not equal the Allocation fee driver "
                     f"{money(driver)}.", sheet, f"{comp.column}{alloc.layout.fund_driver_row}")
        rate = fee.rates[index] if index < len(fee.rates) else (fee.rates[0] if fee.rates else None)
        fraction = fee.period_fractions[index] if index < len(fee.period_fractions) else (
            fee.period_fractions[0] if fee.period_fractions else Decimal("0.25"))
        for inv in alloc.investors:
            charged = inv.amounts.get(comp.column, ZERO)
            row = rows.get(inv.name)
            exempt = inv.is_gp or inv.affiliate is True or (row is not None and (
                row.is_gp or norm_text(row.affiliate_flag) in ("y", "yes", "gp")))
            coord = f"{comp.column}{inv.row}"
            if exempt and charged:
                out.fail(f"{inv.name} is a GP / affiliate but is charged a management fee of {money(charged)}.",
                         sheet, coord)
                continue
            if row is None:
                if charged:
                    out.fail(f"{inv.name} is charged {money(charged)} but has no row on the fee tab.", sheet, coord)
                continue
            # (1) per-investor tie.
            tab_fee = row.fees.get(column, ZERO)
            if differs(charged, tab_fee, CENT):
                out.fail(f"{inv.name}: Allocation fee {money(charged)} vs fee tab {money(tab_fee)}.", sheet, coord)
            # (3) recompute rate x basis x period fraction.
            if rate is None or exempt:
                continue
            basis = row.commitment if row.commitment is not None else inv.commitment
            digits = round_digits(row.formulas.get(column))
            expected = _round(basis * rate * fraction, digits if digits is not None else 2)
            if differs(tab_fee, expected, CENT):
                out.fail(f"{inv.name}: fee tab shows {money(tab_fee)} but {rate:.4%} x {money(basis)} x {fraction} = "
                         f"{money(expected)}.", fee_sheet, f"{column}{row.row}")
    for name, row in rows.items():
        if name not in alloc_by_name and any(row.fees.values()):
            out.review(f"{name} carries a fee on the fee tab but is not on the Allocation sheet.", fee_sheet,
                       f"{fee_cols[0]}{row.row}")
    return "Per-LP fees tie to the current-period fee tab, recompute as rate x basis x period, and totals tie."


def _pair_components(itd_block, alloc):
    """Map each ITD current-block column to an Allocation active component of the same type and side."""
    pool: dict[tuple[str, str], list[str]] = {}
    for comp in alloc.active_components:
        pool.setdefault((comp.component_type, comp.side), []).append(comp.column)
    pairs, unmapped = [], []
    for comp in itd_block.components:
        candidates = pool.get((comp.component_type, comp.side))
        if candidates:
            pairs.append((comp.column, candidates.pop(0)))
        else:
            unmapped.append(comp.column)
    return pairs, unmapped


@check("CE-TIE-ITD-ALLOCATION", needs=("allocation", "itd"))
def tie_itd_allocation(ctx: CheckContext, out: Outcome) -> str:
    alloc, itd = ctx.data.allocation, ctx.data.itd
    block = itd.current_block
    if block is None:
        out.review("The current ITD event block could not be identified.")
        return ""
    pairs, unmapped = _pair_components(block, alloc)
    if unmapped:
        out.review(f"Current ITD block column(s) {', '.join(unmapped)} cannot be mapped to an Allocation component.")
    itd_by_key = {(i.vehicle, i.name): i for i in itd.investors}
    itd_by_name = {i.name: i for i in itd.investors}
    for inv in alloc.investors:
        target = itd_by_key.get((inv.vehicle, inv.name)) or itd_by_name.get(inv.name)
        has_amount = any(inv.amounts.get(a, ZERO) for _, a in pairs)
        if target is None:
            if has_amount:
                out.fail(f"{inv.name} has a current-event amount but is missing from the ITD block.", alloc.sheet,
                         f"{alloc.layout.columns.investor}{inv.row}")
            continue
        for itd_col, alloc_col in pairs:
            a, b = inv.amounts.get(alloc_col, ZERO), target.values.get(itd_col, ZERO)
            if differs(mag(a), mag(b), PENNY):
                out.fail(f"{inv.name}: ITD {itd_col}{target.row} is {money(b)} but Allocation {alloc_col}{inv.row} is "
                         f"{money(a)}.", itd.sheet, f"{itd_col}{target.row}")
    for itd_col, alloc_col in pairs:
        itd_total = sum((i.values.get(itd_col, ZERO) for i in itd.investors), ZERO)
        alloc_total = sum((i.amounts.get(alloc_col, ZERO) for i in alloc.investors), ZERO)
        if differs(mag(itd_total), mag(alloc_total), PENNY):
            out.fail(f"ITD column {itd_col} totals {money(itd_total)} vs Allocation {alloc_col} {money(alloc_total)}.",
                     itd.sheet, f"{itd_col}{itd.layout.event_header_row}")
    return f"The current ITD block ('{block.label}') equals the Allocation sheet per investor and component."


@check("CE-TIE-ITD-COMMITMENTS", needs=("allocation", "itd"))
def tie_itd_commitments(ctx: CheckContext, out: Outcome) -> str:
    alloc, itd = ctx.data.allocation, ctx.data.itd
    block = itd.current_block
    current_contrib_cols = [c for c in (itd.block_columns(block) if block else [])
                            if set(itd.marks.get(c, [])) & {"investment_contributions", "cost_contributions"}]
    itd_by_key = {(i.vehicle, i.name): i for i in itd.investors}
    itd_by_name = {i.name: i for i in itd.investors}
    for inv in alloc.investors:
        target = itd_by_key.get((inv.vehicle, inv.name)) or itd_by_name.get(inv.name)
        if target is None:
            if inv.commitment:
                out.fail(f"{inv.name} is on the Allocation sheet but not on the ITD sheet.", alloc.sheet,
                         f"{alloc.layout.columns.investor}{inv.row}")
            continue
        rf = inv.roll_forward
        commitment = rf.get("commitment") if rf.get("commitment") is not None else inv.commitment
        itd_commitment = target.cumulative.get("commitment")
        if itd_commitment is not None and differs(mag(commitment), mag(itd_commitment), CENT):
            out.fail(f"{inv.name}: Allocation commitment {money(commitment)} vs ITD {money(itd_commitment)}.",
                     itd.sheet, f"{itd.layout.cumulative_columns.commitment}{target.row}")
        total = target.cumulative.get("total_contributions")
        prior = rf.get("prior_contributions")
        if total is not None and prior is not None:
            itd_prior = total - sum((target.values.get(c, ZERO) for c in current_contrib_cols), ZERO)
            if differs(mag(prior), mag(itd_prior), CENT):
                out.fail(f"{inv.name}: Allocation prior contributions {money(mag(prior))} vs ITD contributions before "
                         f"this event {money(mag(itd_prior))}.", alloc.sheet,
                         f"{alloc.layout.roll_forward.prior_contributions}{inv.row}")
        remaining = rf.get("remaining_commitment")
        unfunded = target.cumulative.get("unfunded")
        if remaining is not None and unfunded is not None and differs(mag(remaining), mag(unfunded), CENT):
            out.fail(f"{inv.name}: Allocation remaining commitment {money(remaining)} vs ITD unfunded "
                     f"{money(unfunded)}.", alloc.sheet, f"{alloc.layout.roll_forward.remaining_commitment}{inv.row}")
    return "Commitments, prior contributions and remaining commitment agree between the Allocation and ITD sheets."


@check("CE-ID-INVESTOR-KEYS", needs=("allocation", "merge", "investor_data"))
def investor_keys(ctx: CheckContext, out: Outcome) -> str:
    alloc = ctx.data.allocation
    dx = ctx.data.investor_data
    fee = ctx.data.mgmt_fee
    merge_rows = {r.name: (m, r) for m in ctx.data.merges for r in m.rows}
    active_cols = [c.column for c in alloc.active_components]
    in_scope = [i for i in alloc.investors if not i.is_gp and any(i.amounts.get(c, ZERO) for c in active_cols)]
    dx_by_id = dx.by_investor_id
    fee_names = {r.name for r in fee.rows} if fee else set()
    for inv in in_scope:
        found = merge_rows.get(inv.name)
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
            out.fail(f"{inv.name}: Investor ID {row.investor_id} from {merge.sheet.name} is not on {dx.sheet.name}.",
                     dx.sheet, None)
        elif dx_row.name.strip() != row.name.strip():
            out.fail(f"Investor ID {row.investor_id}: {dx.sheet.name} names it {dx_row.name!r} but the Merge tab "
                     f"(system of record) has {row.name!r}.", dx.sheet, f"{dx.layout.columns.investor_name}{dx_row.row}")
        elif row.fund_id not in (None, "") and dx_row.fund_id not in (None, "") and str(dx_row.fund_id) != str(row.fund_id):
            out.fail(f"{inv.name}: Fund ID {row.fund_id} on the Merge tab vs {dx_row.fund_id} on {dx.sheet.name}.",
                     dx.sheet, f"{dx.layout.columns.fund_id}{dx_row.row}")
        if fee is not None and inv.name not in fee_names:
            out.fail(f"{inv.name} is missing from the fee tab (exact name match).", fee.sheet, None)
    no_id_tabs = [alloc.sheet.name] + ([fee.sheet.name] if fee is not None and not fee.layout.columns.vehicle and False else [])
    if fee is not None:
        no_id_tabs.append(fee.sheet.name)
    out.review(f"{', '.join(no_id_tabs)} carry no Investor ID column, so identity on those tabs could only be "
               "compared by exact name.")
    return ""


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
    itd_by_name = {i.name: i for i in itd.investors}
    outstanding_total = ZERO
    roc_total = ZERO
    for inv in alloc.investors:
        roc = sum((mag(inv.amounts.get(c, ZERO)) for c in roc_cols), ZERO)
        target = itd_by_name.get(inv.name)
        contributed = mag(target.cumulative.get("total_contributions")) if target else ZERO
        returned = sum((mag(target.values.get(c, ZERO)) for c in prior_roc_cols), ZERO) if target else ZERO
        outstanding = contributed - returned
        outstanding_total += max(outstanding, ZERO)
        roc_total += roc
        coord = f"{roc_cols[0]}{inv.row}"
        if roc and contributed == ZERO:
            out.fail(f"{inv.name} never funded but receives {money(roc)} of return of capital.", alloc.sheet, coord)
        elif roc - outstanding > CENT:
            out.fail(f"{inv.name} receives {money(roc)} of return of capital but only {money(outstanding)} of "
                     "contributed capital is outstanding.", alloc.sheet, coord)
    if roc_total - outstanding_total > CENT:
        out.fail(f"Return of capital {money(roc_total)} exceeds total contributed capital outstanding "
                 f"{money(outstanding_total)}.")
    return f"Return of capital ({money(roc_total)}) stays within each investor's outstanding contributed capital."
