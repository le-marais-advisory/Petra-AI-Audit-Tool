"""Summary, date, format and workbook-structure checks."""
from __future__ import annotations

import re
from decimal import Decimal

from src.pipeline.workbook.cells import to_date, to_decimal
from src.pipeline.workbook.checks._common import CheckContext, NotApplicable, Outcome, check, event_number, money
from src.pipeline.workbook.layout import AllocationLayout, ItdLayout, MergeLayout, SummaryLayout
from src.pipeline.workbook.loader import CellModel, SheetModel

ZERO = Decimal("0")


def _check_cells(ctx: CheckContext) -> list[tuple[SheetModel, str]]:
    """Every check / control cell the layouts point at, on every processed sheet (FA calibration)."""
    cells: list[tuple[SheetModel, str]] = []
    data = ctx.data
    if data.summary is not None:
        cells += [(data.summary.sheet, c) for c in data.summary.check_cells]

    def numeric_cells_on(sheet: SheetModel, rows: list[int]) -> None:
        for row in rows:
            for cell in sheet.row_cells(row):
                if cell.is_error or (isinstance(cell.value, (int, float)) and not isinstance(cell.value, bool)):
                    cells.append((sheet, cell.coord))

    if data.allocation is not None:
        numeric_cells_on(data.allocation.sheet, data.allocation.layout.check_rows)
    if data.itd is not None:
        numeric_cells_on(data.itd.sheet, data.itd.layout.check_rows)
    if data.mgmt_fee is not None:
        numeric_cells_on(data.mgmt_fee.sheet, data.mgmt_fee.layout.check_rows)
    for merge in data.merges:
        numeric_cells_on(merge.sheet, merge.layout.check_rows)
        column = merge.layout.columns.check
        if column:
            cells += [(merge.sheet, f"{column}{row.row}") for row in merge.rows
                      if merge.sheet.value(f"{column}{row.row}") is not None]
    return cells


@check("CE-SUM-CHECKS-ZERO", needs=("summary",))
def summary_checks_zero(ctx: CheckContext, out: Outcome) -> str:
    cells = _check_cells(ctx)
    if not cells:
        out.review("No check / control cells were identified.")
        return ""
    for sheet, coord in cells:
        model = sheet.cell(coord)
        if model is not None and model.is_error:
            out.fail(f"Check cell {sheet.name}!{coord} shows {model.value}.", sheet, coord)
            continue
        value = to_decimal(sheet.value(coord)) or ZERO
        if abs(value) > Decimal("0.000001"):
            out.fail(f"Check cell {sheet.name}!{coord} is {money(value)}, not 0.00.", sheet, coord)
    sheets = sorted({sheet.name for sheet, _ in cells})
    return f"All {len(cells)} check cell(s) equal 0.00 ({', '.join(sheets)})."


def _event_date_cells(ctx: CheckContext) -> list[tuple[SheetModel, str, str, CellModel | None]]:
    """(sheet, coord, kind, cell) for every notice / due date the layouts point at."""
    out = []
    for name, layout in ctx.data.layouts.items():
        sheet = ctx.model.sheet(name)
        if isinstance(layout, AllocationLayout):
            pairs = [("notice", layout.event.notice_date_cell), ("due", layout.event.due_date_cell)]
        elif isinstance(layout, SummaryLayout):
            pairs = [("notice", layout.notice_date_cell), ("due", layout.due_date_cell)]
        elif isinstance(layout, MergeLayout):
            pairs = []
            for kind, column in (("notice", layout.columns.letter_date), ("due", layout.columns.due_date)):
                if column:
                    pairs += [(kind, f"{column}{r}") for r in range(layout.first_data_row, layout.last_data_row + 1)
                              if sheet.value(f"{column}{r}") is not None]
        else:
            continue
        for kind, coord in pairs:
            if coord:
                out.append((sheet, coord, kind, sheet.cell(coord)))
    return out


@check("CE-DATE-VALIDITY", needs=("allocation",))
def date_validity(ctx: CheckContext, out: Outcome) -> str:
    cells = _event_date_cells(ctx)
    if not cells:
        raise NotApplicable("No event dates were located.")
    reported: set[tuple[str, str]] = set()
    for sheet, coord, kind, cell in cells:
        value = cell.value if cell else None
        day = to_date(value, allow_serial=True)
        if day is None:
            out.fail(f"{sheet.name}!{coord} {kind} date {value!r} is not a valid calendar date.", sheet, coord)
            continue
        if kind == "due" and day.weekday() >= 5 and (sheet.name, day.isoformat()) not in reported:
            reported.add((sheet.name, day.isoformat()))
            out.fail(f"{sheet.name}!{coord} due / payment date {day:%A %B %d, %Y} falls on a weekend.", sheet, coord)
    return "All event dates are valid and the due / payment date falls on a weekday."


@check("CE-DATE-ORDER", needs=("allocation",))
def date_order(ctx: CheckContext, out: Outcome) -> str:
    alloc = ctx.data.allocation
    sheet = alloc.sheet
    event = alloc.event
    if event.notice_date is None or event.due_date is None:
        raise NotApplicable("Fewer than two event dates are present on the Allocation sheet.")
    due_coord = alloc.layout.event.due_date_cell
    notice_coord = alloc.layout.event.notice_date_cell
    if event.due_date <= event.notice_date:
        out.fail(f"Due / payment date {event.due_date.isoformat()} is on or before the notice date "
                 f"{event.notice_date.isoformat()}.", sheet, due_coord)
    summary = ctx.data.summary
    if summary and summary.notice_date and summary.due_date and summary.due_date <= summary.notice_date:
        out.fail("The Summary's due date is on or before its notice date.", summary.sheet, summary.layout.due_date_cell)
    itd = ctx.data.itd
    if itd is not None:
        from src.pipeline.workbook.checks.rollforward_itd import _DATE_IN_LABEL

        prior_dates = []
        for block in itd.prior_blocks:
            match = _DATE_IN_LABEL.search(block.label)
            day = to_date(match.group(1).replace("-", ".").replace("/", ".")) if match else to_date(block.date)
            if day:
                prior_dates.append(day)
        if prior_dates and event.notice_date < max(prior_dates):
            out.fail(f"The current notice date {event.notice_date.isoformat()} is before the prior event "
                     f"({max(prior_dates).isoformat()}).", sheet, notice_coord)
    due_cell = event.due_cell
    if due_cell is not None and due_cell.formula:
        anchor = notice_coord.replace("$", "")
        if not re.search(rf"\$?{re.escape(re.match(r'[A-Z]+', anchor).group())}\$?{re.search(r'\d+', anchor).group()}"
                         rf"(?!\d)", due_cell.formula):
            out.fail(f"Due date formula {due_cell.formula} does not derive from the notice-date anchor {anchor}.",
                     sheet, due_coord)
    elif due_cell is not None:
        out.review(f"The due date at {due_coord} is a typed value; it is unclear whether it overrides the WORKDAY "
                   "derivation from the notice date.", sheet, due_coord)
    return "The due date follows the notice date and derives from it, and the event does not predate the prior one."


_DATE_TOKENS = re.compile(r"[dmy]", re.I)


def _strip_format(fmt: str) -> str:
    fmt = re.sub(r'"[^"]*"', "", fmt or "")
    fmt = re.sub(r"\[[^\]]*\]", "", fmt)
    return fmt.lower()


@check("CE-FMT-DATE-DISPLAY", needs=("allocation",))
def date_display(ctx: CheckContext, out: Outcome) -> str:
    cells = _event_date_cells(ctx)
    if not cells:
        raise NotApplicable("No event dates were located.")
    formats: dict[tuple[str, str, str], set[str]] = {}
    for sheet, coord, kind, cell in cells:
        if cell is None or isinstance(cell.value, str):
            continue  # a date written inside text (e.g. a title) renders as that text
        fmt = _strip_format(cell.number_format)
        if not _DATE_TOKENS.search(fmt) or fmt in ("general", "0", "0.00"):
            out.fail(f"{sheet.name}!{coord} {kind} date renders as a raw number ({cell.value!r}, format "
                     f"{cell.number_format!r}).", sheet, coord)
            continue
        if re.search(r"(?<!m)m{5}(?!m)", fmt) or re.search(r"m{6,}", fmt):
            out.fail(f"{sheet.name}!{coord} uses a malformed month format {cell.number_format!r}.", sheet, coord)
        column = re.match(r"[A-Z]+", coord).group()
        formats.setdefault((sheet.name, kind, column), set()).add(fmt)
    for (sheet_name, kind, column), fmts in formats.items():
        if len(fmts) > 1:
            out.fail(f"{sheet_name} column {column} mixes {kind}-date formats: {', '.join(sorted(fmts))}.")
    return "Event dates render legibly and consistently."


_ERROR_OR_OVERFLOW = re.compile(r"^#+$")


def _money_cells(ctx: CheckContext) -> list[tuple[SheetModel, str, list[CellModel]]]:
    groups = []
    for name, layout in ctx.data.layouts.items():
        sheet = ctx.model.sheet(name)
        if isinstance(layout, AllocationLayout):
            rows = [r for v in layout.vehicles for r in range(v.investor_rows[0], v.investor_rows[-1] + 1)]
            rows += [r for v in layout.vehicles for r in v.gp_rows] + [layout.fund_driver_row]
            rows += [r for v in layout.vehicles for r in v.subtotal_rows.model_dump().values() if r]
            columns = [c.column for c in layout.components] + [t.column for t in layout.event_total_columns]
            columns += [c for c in layout.roll_forward.model_dump().values() if c]
        elif isinstance(layout, ItdLayout):
            rows = [r for v in layout.vehicles for r in range(v.investor_rows[0], v.investor_rows[-1] + 1)]
            columns = [c.column for b in layout.event_blocks for c in b.components]
        elif isinstance(layout, MergeLayout):
            rows = list(range(layout.first_data_row, layout.last_data_row + 1))
            columns = [c.column for c in layout.component_columns]
        else:
            continue
        for column in columns:
            cells = [sheet.cell(f"{column}{r}") for r in rows]
            cells = [c for c in cells if c is not None and isinstance(c.value, (int, float)) and not isinstance(c.value, bool)]
            if cells:
                groups.append((sheet, column, cells))
    return groups


def _decimals(fmt: str) -> int:
    first = fmt.split(";")[0]
    match = re.search(r"0\.(0+)", first)
    return len(match.group(1)) if match else 0


@check("CE-FMT-ACCOUNTING", needs=("allocation",))
def accounting_format(ctx: CheckContext, out: Outcome) -> str:
    groups = _money_cells(ctx)
    if not groups:
        raise NotApplicable("No monetary cells were located.")
    for sheet, column, cells in groups:
        bad = [c for c in cells if "#,##0" not in _strip_format(c.number_format).replace("\\", "")]
        if bad:
            out.fail(f"{sheet.name} column {column}: {len(bad)} monetary cell(s) lack an accounting/currency format "
                     f"(e.g. {bad[0].coord} uses {bad[0].number_format!r}).", sheet, bad[0].coord)
            continue
        by_decimals: dict[int, list[CellModel]] = {}
        for cell in cells:
            by_decimals.setdefault(_decimals(cell.number_format), []).append(cell)
        if len(by_decimals) > 1:
            usual = max(by_decimals, key=lambda d: len(by_decimals[d]))
            odd = [c for d, group in by_decimals.items() if d != usual for c in group]
            out.fail(f"{sheet.name} column {column}: {len(odd)} cell(s) use a different number of decimals than the "
                     f"rest of the column ({', '.join(c.coord for c in odd[:6])}).", sheet, odd[0].coord)
        negatives = [c for c in cells if c.value < 0]
        for cell in negatives[:1]:
            sections = cell.number_format.split(";")
            if len(sections) < 2 or "(" not in sections[1]:
                out.fail(f"{sheet.name}!{cell.coord} shows negatives with a minus sign rather than parentheses.",
                         sheet, cell.coord)
    return "Monetary cells use an accounting format with thousands separators, consistent decimals and parenthesized negatives."


def _inactive_rows(ctx: CheckContext) -> set[tuple[str, int]]:
    """(sheet, row) of investors no longer active: no commitment and nothing in this event."""
    data = ctx.data
    rows: set[tuple[str, int]] = set()
    if data.allocation is not None:
        rows |= {(data.allocation.sheet.name, i.row) for i in data.allocation.investors
                 if not i.commitment and not any(i.amounts.values())}
    if data.itd is not None:
        rows |= {(data.itd.sheet.name, i.row) for i in data.itd.investors
                 if not (i.cumulative.get("commitment") or ZERO) and not any(i.values.values())}
    for merge in data.merges:
        rows |= {(merge.sheet.name, r.row) for r in merge.rows
                 if not (r.commitment or ZERO) and not (r.event_total or ZERO) and not any(r.amounts.values())}
    if data.mgmt_fee is not None:
        rows |= {(data.mgmt_fee.sheet.name, r.row) for r in data.mgmt_fee.rows
                 if not (r.commitment or ZERO) and not any(r.fees.values())}
    return rows


@check("CE-FMT-NO-FORMULA-ERRORS", needs=("allocation",))
def no_formula_errors(ctx: CheckContext, out: Outcome) -> str:
    """FA calibration: processed sheets plus every sheet they reference (hidden or not);
    unreferenced hidden sheets and inactive investors' rows are ignored."""
    skip = _inactive_rows(ctx)
    for name in ctx.data.scanned_sheets:
        sheet = ctx.model.sheet(name)
        hits = [c for c in sheet.cells.values() if (c.is_error or (isinstance(c.value, str) and
                                                                   _ERROR_OR_OVERFLOW.match(c.value.strip() or "x")))
                and (name, c.row) not in skip]
        for cell in sorted(hits, key=lambda c: (c.row, c.column_index))[:10]:
            out.fail(f"{name}!{cell.coord} shows {cell.value}.", sheet, cell.coord)
    scanned = ", ".join(ctx.data.scanned_sheets)
    return f"No error values or '####' overflows on the processed and referenced sheets ({scanned})."


@check("CE-WB-NO-HIDDEN-DATA", needs=("allocation", "itd"))
def no_hidden_data(ctx: CheckContext, out: Outcome) -> str:
    targets = [ctx.data.allocation, ctx.data.itd]
    for data in targets:
        sheet = data.sheet
        if sheet.state != "visible":
            out.fail(f"The {sheet.name} sheet is {sheet.state}.", sheet, None)
        investor_rows = {i.row for i in data.investors}
        for row in sorted(sheet.hidden_rows & investor_rows):
            populated = [c for c in sheet.row_cells(row) if c.value not in (None, "", 0)]
            if populated:
                out.fail(f"{sheet.name} row {row} is hidden but holds investor data "
                         f"({populated[0].coord}={populated[0].value!r}).", sheet, f"A{row}")
        for column in sorted(sheet.hidden_cols):
            populated = [sheet.cell(f"{column}{r}") for r in sorted(investor_rows)]
            populated = [c for c in populated if c is not None and c.value not in (None, "", 0)]
            if populated:
                out.fail(f"{sheet.name} column {column} is hidden but holds populated investor values "
                         f"({populated[0].coord}={populated[0].value!r}).", sheet, populated[0].coord)
    allocation_name = ctx.data.allocation.sheet.name
    for sheet in ctx.model.sheets:
        if sheet.state == "visible":
            continue
        linked = [c for c in sheet.cells.values() if c.formula and allocation_name.lower() in c.formula.lower()
                  and c.value not in (None, 0, "")]
        if linked:
            out.review(f"Hidden sheet '{sheet.name}' pulls non-zero values from the Allocation sheet "
                       f"(e.g. {linked[0].coord}); confirm it holds no current-event data.", sheet, linked[0].coord)
    return "The Allocation and ITD sheets are visible and no hidden row, column or sheet holds event data."


@check("CE-WB-PAGE-BREAK-VIEW", needs=("allocation", "itd"))
def page_break_view(ctx: CheckContext, out: Outcome) -> str:
    for data in (ctx.data.allocation, ctx.data.itd):
        if data.sheet.view != "pageBreakPreview":
            out.fail(f"The {data.sheet.name} sheet is saved in {data.sheet.view} view, not Page Break Preview.",
                     data.sheet, None)
    return "The Allocation and ITD sheets are saved in Page Break Preview."


_FILE_NAME_RE = re.compile(r"^(?P<fund>[a-z0-9-]+)_(?P<num>\d{3})_(?P<slug>[a-z0-9-]{1,40})_allocation_summary\.xlsx$")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


@check("CE-WB-FILE-NAMING", needs=("allocation",))
def file_naming(ctx: CheckContext, out: Outcome) -> str:
    name = ctx.model.file_name
    sheet = ctx.data.allocation.sheet
    match = _FILE_NAME_RE.match(name)
    if not match:
        out.fail(f"Workbook name '{name}' does not follow {{fund_short}}_{{NNN}}_{{event-slug}}_allocation_summary.xlsx.",
                 sheet, None)
        return ""
    label = ctx.data.allocation.event.label or ""
    number = event_number(label)
    if number is not None and int(match.group("num")) != number:
        out.fail(f"File name event number {match.group('num')} does not match the current event '{label}'.", sheet, None)
    if label and match.group("slug") != _slug(label)[:40]:
        out.fail(f"File name event slug '{match.group('slug')}' does not match the current event '{label}'.", sheet, None)
    return f"The file name '{name}' follows the fund/event pattern and matches the current event."
