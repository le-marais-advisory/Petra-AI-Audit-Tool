"""Check an LLM-proposed SheetLayout against the real cells before trusting it.

Every issue has a stable ``code`` so the layout mapper can re-prompt with specific
corrections, and a ``cell`` pointing at the evidence where one exists.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from src.pipeline.workbook.cells import col_idx, is_text, norm_text, sum_range_rows, to_date, to_money
from src.pipeline.workbook.layout import (
    AllocationLayout,
    HolidayCalendarLayout,
    InvestorDataLayout,
    ItdLayout,
    MergeLayout,
    MgmtFeeLayout,
    SummaryLayout,
    VehicleRows,
)
from src.pipeline.workbook.loader import SheetModel, WorkbookModel

_CLASSIFICATION_WORDS = {
    "investment_contributions": ("investment",),
    "cost_contributions": ("cost",),
    "recallable_distributions": ("recallable",),
    "non_recallable_distributions": ("non",),
    "tax_withholding": ("tax",),
}
_SUBTOTAL_WORDS = {
    "limited_partners": ("limited", "lp"),
    "general_partner": ("general", "gp"),
    "total": ("total",),
}


@dataclass
class LayoutIssue:
    code: str
    message: str
    sheet: str
    cell: str | None = None


class _Checker:
    def __init__(self, sheet: SheetModel) -> None:
        self.sheet = sheet
        self.issues: list[LayoutIssue] = []

    def add(self, code: str, message: str, cell: str | None = None) -> None:
        self.issues.append(LayoutIssue(code=code, message=message, sheet=self.sheet.name, cell=cell))

    def text(self, cell: str) -> str:
        value = self.sheet.value(cell)
        return str(value) if value is not None else ""

    def column_ok(self, column: str | None, what: str) -> bool:
        if column is None:
            return True
        try:
            index = col_idx(column)
        except ValueError:
            self.add("column_out_of_range", f"{what}: {column!r} is not a column", None)
            return False
        if index > self.sheet.max_column:
            self.add("column_out_of_range", f"{what}: column {column} is beyond the used range "
                                            f"({self.sheet.max_column_letter})", None)
            return False
        return True

    def row_ok(self, row: int | None, what: str) -> bool:
        if row is None:
            return True
        if row < 1 or row > self.sheet.max_row:
            self.add("row_out_of_range", f"{what}: row {row} is outside the used range 1-{self.sheet.max_row}")
            return False
        return True

    def header_contains(self, column: str | None, row: int, words: tuple[str, ...], what: str) -> None:
        if column is None or not self.column_ok(column, what):
            return
        cell = f"{column}{row}"
        text = norm_text(self.text(cell))
        if not text or not any(w in text for w in words):
            self.add("header_mismatch", f"{what}: expected a header containing {' / '.join(words)!r} at {cell}, "
                                        f"found {self.text(cell)!r}", cell)

    def header_equals(self, column: str, row: int, expected: str | None, what: str) -> None:
        if not self.column_ok(column, what):
            return
        cell = f"{column}{row}"
        actual = self.text(cell)
        if expected is None:
            if not is_text(actual):
                self.add("header_mismatch", f"{what}: expected a text header at {cell}, found {actual!r}", cell)
        elif norm_text(actual) != norm_text(expected):
            self.add("header_mismatch", f"{what}: expected {expected!r} at {cell}, found {actual!r}", cell)

    def non_empty(self, cell: str | None, what: str) -> None:
        if cell is None:
            return
        if self.sheet.cell(cell) is None or self.sheet.value(cell) in (None, ""):
            self.add("cell_empty", f"{what}: {cell} is empty", cell)

    def label_row(self, row: int | None, label_column: str, words: tuple[str, ...], what: str) -> None:
        if row is None:
            return
        cell = f"{label_column}{row}"
        text = norm_text(self.text(cell))
        if not any(w in text.split() or w in text for w in words):
            self.add("subtotal_row_mismatch", f"{what}: row {row} is labelled {self.text(cell)!r} at {cell}", cell)

    def investor_range(self, span: list[int], subtotal_row: int | None, columns: list[str], label_column: str,
                       what: str) -> None:
        if len(span) != 2 or span[0] > span[1]:
            self.add("investor_range_mismatch", f"{what}: investor_rows must be [first, last], got {span}")
            return
        if not (self.row_ok(span[0], what) and self.row_ok(span[1], what)):
            return
        for row in span:
            cell = f"{label_column}{row}"
            if not is_text(self.text(cell)):
                self.add("investor_range_mismatch", f"{what}: {cell} has no investor name", cell)
        if subtotal_row is None:
            return
        for column in columns:
            cell = self.sheet.cell(f"{column}{subtotal_row}")
            rows = sum_range_rows(cell.formula if cell else None)
            if rows is None:
                continue
            if rows != (span[0], span[1]) and not self._padding_only(rows, span, columns, label_column):
                self.add("investor_range_mismatch",
                         f"{what}: the subtotal at {column}{subtotal_row} sums rows {rows[0]}-{rows[1]}, "
                         f"but investor_rows is {span[0]}-{span[1]}", f"{column}{subtotal_row}")
            return

    def _padding_only(self, rows: tuple[int, int], span: list[int], columns: list[str], label_column: str) -> bool:
        """A subtotal may also sum blank template rows around the investors (no name, all zero)."""
        if rows[0] > span[0] or rows[1] < span[1]:
            return False
        extra = [r for r in range(rows[0], rows[1] + 1) if r < span[0] or r > span[1]]
        for row in extra:
            if is_text(self.text(f"{label_column}{row}")) and self.text(f"{label_column}{row}").strip() not in ("0",):
                return False
            if any(to_money(self.sheet.value(f"{c}{row}")) for c in columns):
                return False
        return True

    def date_cell(self, cell: str | None, what: str) -> None:
        if cell is None:
            return
        value = self.sheet.value(cell)
        if value in (None, ""):
            self.add("cell_empty", f"{what}: {cell} is empty", cell)
        elif to_date(value, allow_serial=True) is None:
            self.add("not_a_date", f"{what}: {cell} holds {value!r}, which is not a date", cell)

    def vehicle_rows(self, vehicles: list[VehicleRows], label_column: str, sum_columns: list[str]) -> None:
        spans = []
        for vehicle in vehicles:
            what = f"vehicle {vehicle.name!r}"
            self.investor_range(vehicle.investor_rows, vehicle.subtotal_rows.limited_partners, sum_columns,
                                label_column, what)
            for key, words in _SUBTOTAL_WORDS.items():
                self.label_row(getattr(vehicle.subtotal_rows, key), label_column, words, f"{what} {key} row")
            if len(vehicle.investor_rows) == 2:
                spans.append((vehicle.investor_rows[0], vehicle.subtotal_rows.total or vehicle.investor_rows[1],
                              vehicle.name))
        spans.sort()
        for (a0, a1, an), (b0, b1, bn) in zip(spans, spans[1:]):
            if b0 <= a1:
                self.add("row_overlap", f"vehicles {an!r} and {bn!r} overlap (rows {a0}-{a1} and {b0}-{b1})")


def _validate_allocation(c: _Checker, layout: AllocationLayout) -> None:
    header = layout.header_row
    c.row_ok(header, "header_row")
    if layout.fund_driver_row >= header:
        c.add("driver_row_position", f"fund_driver_row {layout.fund_driver_row} must sit above header_row {header}")
    cols = layout.columns
    c.header_contains(cols.investor, header, ("investor", "partner", "name", "lp"), "columns.investor")
    c.header_contains(cols.commitment, header, ("commit",), "columns.commitment")
    c.header_contains(cols.commitment_pct, header, ("%", "percent", "ratio"), "columns.commitment_pct")
    for name in ("affiliate_flag", "late_interest", "cash_due", "received", "received_date", "distribution_basis",
                 "distribution_basis_pct", "mgmt_fee_rate"):
        column = getattr(cols, name)
        if column is not None:
            c.column_ok(column, f"columns.{name}")
    for comp in layout.components:
        c.header_equals(comp.column, header, comp.header, f"component {comp.component_type}")
    for total in layout.event_total_columns:
        c.header_equals(total.column, header, None, f"event total ({total.side})")
    for name, column in layout.roll_forward.model_dump().items():
        if column is not None:
            c.header_equals(column, header, None, f"roll_forward.{name}")
    for name in ("label_cell", "carried_interest_rate_cell"):
        c.non_empty(getattr(layout.event, name), f"event.{name}")
    for name in ("notice_date_cell", "due_date_cell"):
        c.date_cell(getattr(layout.event, name), f"event.{name}")
    if not layout.vehicles:
        c.add("investor_range_mismatch", "no vehicle blocks")
    for vehicle in layout.vehicles:
        if vehicle.driver_row is not None and vehicle.driver_row < layout.fund_driver_row:
            c.add("driver_row_position", f"vehicle {vehicle.name!r} driver_row sits above the fund driver row")
    c.vehicle_rows(layout.vehicles, cols.investor, [cols.commitment] + [comp.column for comp in layout.components])


def _validate_itd(c: _Checker, layout: ItdLayout) -> None:
    c.row_ok(layout.event_header_row, "event_header_row")
    c.row_ok(layout.subheader_row, "subheader_row")
    blocks = layout.event_blocks
    first_block_col = min((col_idx(b.first_column) for b in blocks), default=10**6)
    for category, row in layout.classification_rows.model_dump().items():
        if row is None:
            continue
        labels = [norm_text(cell.value) for cell in c.sheet.row_cells(row)
                  if is_text(cell.value) and cell.column_index < first_block_col]
        words = _CLASSIFICATION_WORDS[category]
        ok = any(any(w in label for w in words) for label in labels)
        if category == "recallable_distributions":
            ok = any("recallable" in label and "non" not in label for label in labels)
        if not ok:
            c.add("classification_row_mismatch",
                  f"classification_rows.{category}: row {row} is labelled {labels or 'nothing'}")
    current = [b for b in blocks if b.is_current]
    if len(current) != 1:
        c.add("current_block_count", f"expected exactly one current event block, found {len(current)}")
    spans = []
    for block in blocks:
        if not (c.column_ok(block.first_column, f"block {block.label!r}") and
                c.column_ok(block.last_column, f"block {block.label!r}")):
            continue
        c.header_equals(block.first_column, layout.event_header_row, block.label, f"block {block.label!r} label")
        if block.total_column:
            c.header_contains(block.total_column, layout.subheader_row, ("total",), f"block {block.label!r} total")
        for comp in block.components:
            c.header_equals(comp.column, layout.subheader_row, None, f"block {block.label!r} component")
        end = max(col_idx(block.last_column), col_idx(block.total_column or block.last_column))
        spans.append((col_idx(block.first_column), end, block.label))
    spans.sort()
    for (a0, a1, an), (b0, b1, bn) in zip(spans, spans[1:]):
        if b0 <= a1:
            c.add("block_overlap", f"event blocks {an!r} and {bn!r} overlap")
    for name, column in layout.cumulative_columns.model_dump().items():
        if column is not None:
            c.header_equals(column, layout.subheader_row, None, f"cumulative_columns.{name}")
    sum_cols = [col for col in [layout.cumulative_columns.commitment] if col]
    c.vehicle_rows(layout.vehicles, layout.investor_column, sum_cols)


def _validate_summary(c: _Checker, layout: SummaryLayout) -> None:
    for name in ("title_cell", "fund_commitment_cell", "event_total_cell"):
        c.non_empty(getattr(layout, name), name)
    for name in ("notice_date_cell", "due_date_cell"):
        c.date_cell(getattr(layout, name), name)
    for cell in layout.check_cells:
        c.non_empty(cell, "check cell")
    for line in layout.component_lines:
        c.non_empty(line.amount_cell, f"component line {line.component_type}")


def _validate_merge(c: _Checker, layout: MergeLayout) -> None:
    row = layout.header_row
    cols = layout.columns
    c.header_contains(cols.investor, row, ("investor", "partner", "name"), "columns.investor")
    c.header_contains(cols.investor_id, row, ("investor id", "investorid", "lp id"), "columns.investor_id")
    c.header_contains(cols.fund_id, row, ("fund id", "fundid"), "columns.fund_id")
    c.header_contains(cols.file_name, row, ("file",), "columns.file_name")
    sum_cols = [comp.column for comp in layout.component_columns] + [x for x in [cols.event_total] if x]
    c.investor_range([layout.first_data_row, layout.last_data_row], layout.total_row, sum_cols, cols.investor,
                     "merge rows")


def _validate_fee(c: _Checker, layout: MgmtFeeLayout) -> None:
    row = layout.header_row
    c.header_contains(layout.columns.investor, row, ("investor", "partner", "name"), "columns.investor")
    c.header_contains(layout.columns.commitment, row, ("commit",), "columns.commitment")
    for fee in layout.fee_columns:
        c.header_contains(fee.column, row, ("fee",), "fee column")
        if fee.period_label and c.column_ok(fee.column, "fee column"):
            cell = f"{fee.column}{row}"
            if norm_text(fee.period_label) not in norm_text(c.text(cell)):
                c.add("header_mismatch", f"fee column period {fee.period_label!r} not in header {c.text(cell)!r}", cell)
    sum_cols = [x for x in [layout.columns.commitment] if x] + [f.column for f in layout.fee_columns]
    c.investor_range(layout.investor_rows, layout.subtotal_rows.limited_partners, sum_cols, layout.columns.investor,
                     "fee investor rows")
    for key, words in _SUBTOTAL_WORDS.items():
        c.label_row(getattr(layout.subtotal_rows, key), layout.columns.investor, words, f"fee {key} row")
    for cell in layout.rate_cells + layout.period_fraction_cells:
        c.non_empty(cell, "fee rate cell")


def _validate_investor_data(c: _Checker, layout: InvestorDataLayout) -> None:
    row = layout.header_row
    c.header_contains(layout.columns.investor_name, row, ("investor", "name"), "columns.investor_name")
    c.header_contains(layout.columns.investor_id, row, ("id",), "columns.investor_id")
    c.header_contains(layout.columns.fund_id, row, ("fund",), "columns.fund_id")
    c.row_ok(layout.last_data_row, "last_data_row")


def _validate_holidays(c: _Checker, layout: HolidayCalendarLayout) -> None:
    c.column_ok(layout.date_column, "date_column")
    for row in (layout.first_row, layout.last_row):
        c.non_empty(f"{layout.date_column}{row}", "holiday date")


_VALIDATORS = {
    AllocationLayout: _validate_allocation,
    ItdLayout: _validate_itd,
    SummaryLayout: _validate_summary,
    MergeLayout: _validate_merge,
    MgmtFeeLayout: _validate_fee,
    InvestorDataLayout: _validate_investor_data,
    HolidayCalendarLayout: _validate_holidays,
}


def validate_layout(model: WorkbookModel, layout: BaseModel) -> list[LayoutIssue]:
    if not model.has_sheet(layout.sheet):
        return [LayoutIssue("sheet_missing", f"no sheet named {layout.sheet!r}", layout.sheet)]
    checker = _Checker(model.sheet(layout.sheet))
    _VALIDATORS[type(layout)](checker, layout)
    return checker.issues
