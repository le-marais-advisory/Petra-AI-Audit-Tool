"""Cross-event checks: compare the current workbook with the prior event's workbook.

FA calibration: users upload the current workbook together with the most recent prior
one (or mark the run as the fund's first capital event). These rules confirm that the
prior event rolled into the current workbook unchanged and that plugs follow the same
pattern from event to event.
"""
from __future__ import annotations

from decimal import Decimal

from src.pipeline.workbook.cells import CENT, PENNY
from src.pipeline.workbook.checks._common import CheckContext, NotApplicable, Outcome, check, mag, money
from src.pipeline.workbook.checks.allocation import _plugs, plug_eligibility
from src.pipeline.workbook.extract import WorkbookData

ZERO = Decimal("0")


def _prior(ctx: CheckContext, out: Outcome, roles: tuple[str, ...]) -> WorkbookData | None:
    """The prior workbook's data, or None after recording why the rule cannot run."""
    if ctx.options.get("first_event"):
        raise NotApplicable("This is the fund's first capital event, so there is no prior workbook to compare with.")
    prior = ctx.data.prior
    if prior is None:
        out.review("The prior event's workbook was not supplied, so the comparison could not run.")
        return None
    missing = [role for role in roles if getattr(prior, role, None) is None]
    if missing:
        out.review(f"The prior workbook's {', '.join(missing)} layout could not be mapped, so the comparison could "
                   "not run.")
        return None
    return prior


@check("CE-XEV-HISTORY-UNCHANGED", needs=("itd",))
def history_unchanged(ctx: CheckContext, out: Outcome) -> str:
    prior = _prior(ctx, out, ("itd",))
    if prior is None:
        return ""
    current_itd, prior_itd = ctx.data.itd, prior.itd
    current_blocks = {b.label: b for b in current_itd.event_blocks}
    current_investors = {i.name: i for i in current_itd.investors}
    compared = 0
    for block in prior_itd.event_blocks:
        if block.event_type == "transfer":
            continue
        target = current_blocks.get(block.label)
        cell = f"{block.first_column}{prior_itd.layout.event_header_row}"
        if target is None:
            out.fail(f"Prior event '{block.label}' is missing from the current ITD sheet.", prior_itd.sheet, cell)
            continue
        if target.is_current:
            out.fail(f"'{block.label}' is still marked as the current event in the new workbook.", current_itd.sheet,
                     f"{target.first_column}{current_itd.layout.event_header_row}")
        pairs = list(zip(block.components, target.components))
        if len(block.components) != len(target.components):
            out.fail(f"'{block.label}' has {len(target.components)} component column(s) now but had "
                     f"{len(block.components)} in the prior workbook.", current_itd.sheet,
                     f"{target.first_column}{current_itd.layout.event_header_row}")
        for investor in prior_itd.investors:
            now = current_investors.get(investor.name)
            for old_comp, new_comp in pairs:
                before = investor.values.get(old_comp.column, ZERO)
                after = now.values.get(new_comp.column, ZERO) if now else ZERO
                compared += 1
                if now is None and before:
                    out.fail(f"{investor.name} ({money(before)} in '{block.label}') is missing from the current ITD "
                             "sheet.", prior_itd.sheet, f"{old_comp.column}{investor.row}")
                elif now is not None and abs(after - before) > PENNY:
                    out.fail(f"{investor.name}: '{block.label}' {new_comp.component_type} changed from "
                             f"{money(before)} to {money(after)} since the prior workbook.", current_itd.sheet,
                             f"{new_comp.column}{now.row}")
    return f"Every prior event block reappears unchanged in the current ITD sheet ({compared} cells compared)."


@check("CE-XEV-ROLL-FORWARD", needs=("allocation",))
def roll_forward(ctx: CheckContext, out: Outcome) -> str:
    prior = _prior(ctx, out, ("itd",))
    if prior is None:
        return ""
    alloc = ctx.data.allocation
    column = alloc.layout.roll_forward.prior_contributions
    if column is None:
        out.review("The Allocation sheet has no Prior Capital Contributions column.")
        return ""
    contributed = {i.name: i.cumulative.get("total_contributions") for i in prior.itd.investors}
    commitments = {i.name: i.cumulative.get("commitment") for i in prior.itd.investors}
    for inv in alloc.investors:
        now = mag(inv.roll_forward.get("prior_contributions"))
        if inv.name not in contributed:
            if now > CENT:
                out.fail(f"{inv.name} shows {money(now)} of prior contributions but is not in the prior workbook.",
                         alloc.sheet, f"{column}{inv.row}")
            continue
        before = mag(contributed[inv.name])
        if abs(now - before) > CENT:
            out.fail(f"{inv.name}: prior contributions {money(now)} do not equal the contributions to date in the "
                     f"prior workbook ({money(before)}).", alloc.sheet, f"{column}{inv.row}")
        old_commitment = commitments.get(inv.name)
        if old_commitment is not None and abs(mag(old_commitment) - inv.commitment) > CENT:
            out.review(f"{inv.name}: commitment changed from {money(old_commitment)} to {money(inv.commitment)} "
                       "since the prior event (transfer or new close?).", alloc.sheet,
                       f"{alloc.layout.columns.commitment}{inv.row}")
    return "Prior contributions on the Allocation sheet carry over exactly from the prior event's workbook."


_ITD_CATEGORIES = {
    "investment_contributions": ("investment_contributions",),
    "cost_contributions": ("cost_contributions",),
    "total_contributions": ("investment_contributions", "cost_contributions"),
    "recallable_distributions": ("recallable_distributions",),
    "non_recallable_distributions": ("non_recallable_distributions",),
    "total_distributions": ("recallable_distributions", "non_recallable_distributions"),
}


def _label(category: str) -> str:
    return "ITD " + category.replace("_", " ")


@check("CE-XEV-ITD-ROLL-FORWARD", needs=("itd",))
def itd_roll_forward(ctx: CheckContext, out: Outcome) -> str:
    """FA (QC practice): prior ITD balance + this event = new ITD balance, per investor and category.

    E.g. 150k ITD distributions before, a 25k distribution now, but 200k ITD distributions in the
    new workbook: 25k is double counted or entered wrong somewhere.
    """
    prior = _prior(ctx, out, ("itd",))
    if prior is None:
        return ""
    itd, before_itd = ctx.data.itd, prior.itd
    block = itd.current_block
    if block is None:
        out.review("The current ITD event block could not be identified, so the roll-forward could not be checked.")
        return ""
    prior_labels = {b.label for b in before_itd.event_blocks}
    for other in itd.prior_blocks:
        if other.event_type != "transfer" and other.label not in prior_labels:
            out.fail(f"'{other.label}' is in the current ITD sheet but not in the prior workbook: either the prior "
                     "workbook uploaded is not the most recent one, or the event was entered twice.", itd.sheet,
                     f"{other.first_column}{itd.layout.event_header_row}")
    columns = {c.column for c in block.components}
    cum = itd.layout.cumulative_columns.model_dump()
    before = {i.name: i for i in before_itd.investors}
    rows = []  # (investor, category, delta, movement)
    for inv in itd.investors:
        old = before.get(inv.name)
        for category, members in _ITD_CATEGORIES.items():
            now = inv.cumulative.get(category)
            if now is None or not cum.get(category):
                continue
            was = (old.cumulative.get(category) if old else ZERO) or ZERO
            movement = sum((inv.values.get(col, ZERO) for col in columns
                            if set(itd.marks.get(col, [])) & set(members)), ZERO)
            rows.append((inv, category, now - was, movement, was, now))
    for name, old in before.items():
        if name not in {i.name for i in itd.investors} and any(
                mag(old.cumulative.get(c)) > CENT for c in _ITD_CATEGORIES):
            out.fail(f"{name} carries ITD balances in the prior workbook but is missing from the current ITD sheet.",
                     before_itd.sheet, f"{before_itd.layout.investor_column}{old.row}")
    # Workbooks differ in sign convention (distributions negative or positive): use the one most rows follow.
    signs = {}
    for category in _ITD_CATEGORIES:
        moved = [(d, m) for _, c, d, m, _, _ in rows if c == category and m]
        same = sum(1 for d, m in moved if abs(d - m) <= CENT)
        flipped = sum(1 for d, m in moved if abs(d + m) <= CENT)
        signs[category] = -1 if flipped > same else 1
    checked = 0
    for inv, category, delta, movement, was, now in rows:
        checked += 1
        expected = movement * signs[category]
        if abs(delta - expected) > CENT:
            out.fail(f"{inv.name}: {_label(category)} went from {money(was)} to {money(now)} ({money(delta)}), but "
                     f"the current event ('{block.label}') moves it by {money(expected)}; {money(delta - expected)} is "
                     "double counted or entered wrong.", itd.sheet, f"{cum[category]}{inv.row}")
    return (f"Every ITD balance equals the prior workbook's balance plus the current event ({checked} investor "
            "balances checked).")


def _pattern(data: WorkbookData) -> dict[str, tuple[str, frozenset[str]]]:
    """component type -> ('single' | 'spread', plugged investors), over all vehicles."""
    alloc = data.allocation
    patterns: dict[str, tuple[str, frozenset[str]]] = {}
    for comp in alloc.active_components:
        names = [inv.name for vehicle in alloc.vehicles for inv, _ in _plugs(alloc, vehicle, comp.column)]
        if not names:
            continue
        per_vehicle = max(sum(1 for inv, _ in _plugs(alloc, v, comp.column)) for v in alloc.vehicles)
        patterns[comp.component_type] = ("spread" if per_vehicle > 1 else "single", frozenset(names))
    return patterns


@check("CE-XEV-PLUG-CONSISTENCY", needs=("allocation",))
def plug_consistency(ctx: CheckContext, out: Outcome) -> str:
    prior = _prior(ctx, out, ("allocation",))
    if prior is None:
        return ""
    now, before = _pattern(ctx.data), _pattern(prior)
    shared = sorted(set(now) & set(before))
    if not shared:
        raise NotApplicable("No component carries a rounding plug in both events.")
    sheet = ctx.data.allocation.sheet
    eligible = plug_eligibility(ctx.data.allocation)
    current_names = {i.name: i for i in ctx.data.allocation.investors}
    for component in shared:
        (style_now, names_now), (style_before, names_before) = now[component], before[component]
        if style_now != style_before:
            out.fail(f"{component}: the prior event used a {style_before} plug ({', '.join(sorted(names_before))}) but "
                     f"this event uses a {style_now} plug ({', '.join(sorted(names_now))}).", sheet,
                     _first_plug_cell(ctx, component))
        elif names_now != names_before:
            # Same style on different investors: fine if the earlier ones are no longer eligible.
            moved_from = [n for n in names_before - names_now if n in current_names and eligible(current_names[n])]
            if moved_from:
                out.review(f"{component}: the plug moved from {', '.join(sorted(names_before))} to "
                           f"{', '.join(sorted(names_now))} since the prior event.", sheet,
                           _first_plug_cell(ctx, component))
    return f"Plugs follow the prior event's pattern ({', '.join(f'{c}: {now[c][0]}' for c in shared)})."


def _first_plug_cell(ctx: CheckContext, component_type: str) -> str | None:
    alloc = ctx.data.allocation
    for comp in alloc.active_components:
        if comp.component_type != component_type:
            continue
        for vehicle in alloc.vehicles:
            for inv, _ in _plugs(alloc, vehicle, comp.column):
                return f"{comp.column}{inv.row}"
    return None
