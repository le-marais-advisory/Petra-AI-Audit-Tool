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
from src.pipeline.workbook.keys import Matcher

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



def _pair_block_columns(before, now) -> list[tuple[str, str, str]]:
    """(prior column, current column, component type) pairs of a block in the two workbooks: by column
    letter when the block sits in the same place, else by position."""
    if before.first_column == now.first_column:
        current_cols = {c.column: c for c in now.components}
        pairs = [(c.column, c.column, current_cols[c.column].component_type) for c in before.components
                 if c.column in current_cols]
        if pairs:
            return pairs
    return [(old.column, new.column, new.component_type) for old, new in zip(before.components, now.components)]


@check("CE-XEV-HISTORY-UNCHANGED", needs=("itd",))
def history_unchanged(ctx: CheckContext, out: Outcome) -> str:
    prior = _prior(ctx, out, ("itd",))
    if prior is None:
        return ""
    current_itd, prior_itd = ctx.data.itd, prior.itd
    by_label = {b.label: b for b in current_itd.event_blocks}
    by_column = {b.first_column: b for b in current_itd.event_blocks}
    matcher = Matcher(prior_itd.vehicles, current_itd.vehicles)  # (vehicle, name): an LP may sit in two vehicles
    compared = 0
    changed: dict[str, list[tuple]] = {}
    for block in prior_itd.event_blocks:
        if block.event_type == "transfer":
            continue
        cell = f"{block.first_column}{prior_itd.layout.event_header_row}"
        target = by_label.get(block.label)
        if target is None:
            # The block may have been relabelled (e.g. a template label replaced by the real one).
            target = by_column.get(block.first_column)
            if target is not None and target.label != block.label:
                out.review(f"Prior event '{block.label}' (column {block.first_column}) is now labelled "
                           f"'{target.label}'; confirm the relabel.", current_itd.sheet,
                           f"{target.first_column}{current_itd.layout.event_header_row}")
        if target is None:
            out.fail(f"Prior event '{block.label}' is missing from the current ITD sheet.", prior_itd.sheet, cell)
            continue
        if target.is_current:
            out.fail(f"'{block.label}' is still marked as the current event in the new workbook.", current_itd.sheet,
                     f"{target.first_column}{current_itd.layout.event_header_row}")
        pairs = _pair_block_columns(block, target)
        if len(block.components) != len(target.components):
            out.fail(f"'{block.label}' has {len(target.components)} component column(s) now but had "
                     f"{len(block.components)} in the prior workbook.", current_itd.sheet,
                     f"{target.first_column}{current_itd.layout.event_header_row}")
        for ordinal, investor in matcher.pairs():
            now = matcher.find(ordinal, investor.name)
            label = matcher.label(ordinal, investor.name)
            for old_col, new_col, component_type in pairs:
                before = investor.values.get(old_col, ZERO)
                after = now.values.get(new_col, ZERO) if now else ZERO
                compared += 1
                if now is None and before:
                    out.fail(f"{label} ({money(before)} in '{block.label}') is missing from the current ITD "
                             "sheet.", prior_itd.sheet, f"{old_col}{investor.row}")
                elif now is not None and abs(after - before) > PENNY:
                    changed.setdefault(label, []).append((block.label, component_type, new_col, before, after, now.row))
    # One line per investor: a transfer or re-allocation rewrites that investor's whole history.
    for label, cells in changed.items():
        block_label, component_type, column, before, after, row = cells[0]
        example = f"'{block_label}' {component_type} ({column}{row}) {money(before)} -> {money(after)}"
        if len(cells) == 1:
            out.fail(f"{label}: {example} changed since the prior workbook.", current_itd.sheet, f"{column}{row}")
        else:
            blocks_hit = len({c[0] for c in cells})
            out.fail(f"{label}: {len(cells)} prior-event cells across {blocks_hit} block(s) changed since the prior "
                     f"workbook (e.g. {example}).", current_itd.sheet, f"{column}{row}")
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
    matcher = Matcher(alloc.vehicles, prior.itd.vehicles)
    for ordinal, inv in matcher.pairs():
        now = mag(inv.roll_forward.get("prior_contributions"))
        label = matcher.label(ordinal, inv.name)
        old = matcher.find(ordinal, inv.name)
        if old is None:
            if now > CENT:
                out.fail(f"{label} shows {money(now)} of prior contributions but is not in the prior workbook.",
                         alloc.sheet, f"{column}{inv.row}")
            continue
        before = mag(old.cumulative.get("total_contributions"))
        if abs(now - before) > CENT:
            out.fail(f"{label}: prior contributions {money(now)} do not equal the contributions to date in the "
                     f"prior workbook ({money(before)}).", alloc.sheet, f"{column}{inv.row}")
        old_commitment = old.cumulative.get("commitment")
        if old_commitment is not None and abs(mag(old_commitment) - inv.commitment) > CENT:
            out.review(f"{label}: commitment changed from {money(old_commitment)} to {money(inv.commitment)} "
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
    prior_columns = {b.first_column for b in before_itd.event_blocks}
    for other in itd.prior_blocks:
        if other.event_type != "transfer" and other.label not in prior_labels \
                and other.first_column not in prior_columns:
            out.fail(f"'{other.label}' is in the current ITD sheet but not in the prior workbook: either the prior "
                     "workbook uploaded is not the most recent one, or the event was entered twice.", itd.sheet,
                     f"{other.first_column}{itd.layout.event_header_row}")
    columns = {c.column for c in block.components}
    cum = itd.layout.cumulative_columns.model_dump()
    matcher = Matcher(itd.vehicles, before_itd.vehicles)
    derived = {c for c in _ITD_CATEGORIES if c in itd.derived_cumulatives}
    for category in sorted(derived):
        # E.g. a recycling fund's recallable column is the room left under the LPA cap, so the
        # current event moves it by more than the event's own recallable amounts.
        out.review(f"{_label(category)} ({cum[category]}) is derived by formula rather than accumulated from the "
                   "event columns, so its roll-forward cannot be checked event by event; confirm the basis "
                   "(e.g. a recycling cap) against the LPA.", itd.sheet, f"{cum[category]}{itd.layout.subheader_row}")
    rows = []  # (investor, label, category, delta, movement, was, now)
    for ordinal, inv in matcher.pairs():
        old = matcher.find(ordinal, inv.name)
        for category, members in _ITD_CATEGORIES.items():
            now = inv.cumulative.get(category)
            if now is None or not cum.get(category) or category in derived:
                continue
            was = (old.cumulative.get(category) if old else ZERO) or ZERO
            movement = sum((inv.values.get(col, ZERO) for col in columns
                            if set(itd.marks.get(col, [])) & set(members)), ZERO)
            rows.append((inv, matcher.label(ordinal, inv.name), category, now - was, movement, was, now))
    for t_ordinal, old in matcher.target_pairs():
        if not matcher.source_has(t_ordinal, old.name) and any(
                mag(old.cumulative.get(c)) > CENT for c in _ITD_CATEGORIES):
            out.fail(f"{old.name} carries ITD balances in the prior workbook but is missing from the current ITD sheet.",
                     before_itd.sheet, f"{before_itd.layout.investor_column}{old.row}")
    # Workbooks differ in sign convention (distributions negative or positive): use the one most rows follow.
    signs = {}
    for category in _ITD_CATEGORIES:
        moved = [(d, m) for _, _, c, d, m, _, _ in rows if c == category and m]
        same = sum(1 for d, m in moved if abs(d - m) <= CENT)
        flipped = sum(1 for d, m in moved if abs(d + m) <= CENT)
        signs[category] = -1 if flipped > same else 1
    checked = 0
    by_investor: dict[str, list[tuple]] = {}
    for inv, label, category, delta, movement, was, now in rows:
        checked += 1
        expected = movement * signs[category]
        if abs(delta - expected) > CENT:
            by_investor.setdefault(label, []).append((inv, category, delta, expected, was, now))
    for label, items in by_investor.items():
        inv, category, delta, expected, was, now = items[0]
        if len(items) < 3:
            for inv, category, delta, expected, was, now in items:
                out.fail(f"{label}: {_label(category)} went from {money(was)} to {money(now)} ({money(delta)}), but "
                         f"the current event ('{block.label}') moves it by {money(expected)}; "
                         f"{money(delta - expected)} is double counted or entered wrong.", itd.sheet,
                         f"{cum[category]}{inv.row}")
            continue
        # Several balances of one investor moved without a current-event movement: one line (a transfer
        # or re-allocation of the investor's history, not a stray entry).
        moves = "; ".join(f"{_label(c).replace('ITD ', '')} {money(w)} -> {money(n)} (event: {money(e)})"
                          for _, c, _, e, w, n in items)
        out.fail(f"{label}: {len(items)} ITD balances changed beyond the current event ('{block.label}'): {moves}.",
                 itd.sheet, f"{cum[category]}{inv.row}")
    return (f"Every ITD balance equals the prior workbook's balance plus the current event ({checked} investor "
            "balances checked).")


def _pattern(data: WorkbookData) -> dict[tuple[str, int], tuple[str, frozenset[str]]]:
    """(component type, vehicle ordinal) -> ('single' | 'spread', plugged investors).

    Per vehicle: each block carries its own rounding residual, and a block whose residual
    happens to be zero in one event has no plug to compare."""
    alloc = data.allocation
    patterns: dict[tuple[str, int], tuple[str, frozenset[str]]] = {}
    for comp in alloc.active_components:
        for ordinal, vehicle in enumerate(alloc.vehicles):
            names = [inv.name for inv, _ in _plugs(alloc, vehicle, comp.column)]
            if not names:
                continue
            patterns[(comp.component_type, ordinal)] = ("spread" if len(names) > 1 else "single", frozenset(names))
    return patterns


@check("CE-XEV-PLUG-CONSISTENCY", needs=("allocation",))
def plug_consistency(ctx: CheckContext, out: Outcome) -> str:
    prior = _prior(ctx, out, ("allocation",))
    if prior is None:
        return ""
    now, before = _pattern(ctx.data), _pattern(prior)
    # Vehicle blocks are aligned between the two workbooks by their investors.
    alignment = Matcher(ctx.data.allocation.vehicles, prior.allocation.vehicles).alignment
    shared = sorted((c, o) for (c, o) in now if (c, alignment.get(o)) in before)
    if not shared:
        raise NotApplicable("No component carries a rounding plug in both events.")
    sheet = ctx.data.allocation.sheet
    eligible = plug_eligibility(ctx.data.allocation)
    vehicles = ctx.data.allocation.vehicles
    for component, ordinal in shared:
        (style_now, names_now) = now[(component, ordinal)]
        (style_before, names_before) = before[(component, alignment[ordinal])]
        vehicle = vehicles[ordinal]
        label = f"{vehicle.name} / {component}" if len(vehicles) > 1 else component
        current_names = {i.name: i for i in vehicle.investors}
        if style_now != style_before:
            out.fail(f"{label}: the prior event used a {style_before} plug ({', '.join(sorted(names_before))}) but "
                     f"this event uses a {style_now} plug ({', '.join(sorted(names_now))}).", sheet,
                     _first_plug_cell(ctx, component, vehicle))
        elif names_now != names_before:
            # Same style on different investors: fine if the earlier ones are no longer eligible.
            moved_from = [n for n in names_before - names_now if n in current_names and eligible(current_names[n])]
            if moved_from:
                out.review(f"{label}: the plug moved from {', '.join(sorted(names_before))} to "
                           f"{', '.join(sorted(names_now))} since the prior event.", sheet,
                           _first_plug_cell(ctx, component, vehicle))
    styles = sorted({f"{c}: {now[(c, o)][0]}" for c, o in shared})
    return f"Plugs follow the prior event's pattern ({', '.join(styles)})."


def _first_plug_cell(ctx: CheckContext, component_type: str, vehicle=None) -> str | None:
    alloc = ctx.data.allocation
    for comp in alloc.active_components:
        if comp.component_type != component_type:
            continue
        for candidate in ([vehicle] if vehicle is not None else alloc.vehicles):
            for inv, _ in _plugs(alloc, candidate, comp.column):
                return f"{comp.column}{inv.row}"
    return None
