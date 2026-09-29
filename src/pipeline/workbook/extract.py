"""Read typed capital-event data out of a workbook using validated layouts.

Everything downstream (deterministic checks, hybrid-rule facts) works on these
dataclasses, never on raw coordinates. A role whose layout is missing (not relevant
to the event, or rejected by the validator) is ``None`` here, and the checks that need
it return needs_review.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from src.pipeline.workbook.cells import col_idx, is_text, norm_text, rows_between, to_date, to_decimal, to_money
from src.pipeline.workbook.layout import (
    AllocationLayout,
    ComponentColumn,
    EventBlock,
    HolidayCalendarLayout,
    InvestorDataLayout,
    ItdLayout,
    MergeLayout,
    MgmtFeeLayout,
    SummaryLayout,
)
from src.pipeline.workbook.loader import CellModel, SheetModel, WorkbookModel

# --- Allocation --------------------------------------------------------------------------


@dataclass
class EventInfo:
    event_type: str | None
    label: str | None
    notice_date: dt.date | None
    due_date: dt.date | None
    notice_cell: CellModel | None
    due_cell: CellModel | None
    carried_interest_rate: Decimal | None


@dataclass
class AllocationInvestor:
    name: str
    row: int
    vehicle: str
    is_gp: bool
    affiliate: bool | None
    commitment: Decimal
    commitment_pct: Decimal | None
    amounts: dict[str, Decimal]  # every component column -> amount
    formulas: dict[str, str | None]  # every component column -> stored formula
    call_total: Decimal | None
    dist_total: Decimal | None
    roll_forward: dict[str, Decimal | None]
    distribution_basis: Decimal | None = None
    received: Any = None
    received_date: Any = None
    cells: dict[str, CellModel | None] = field(default_factory=dict)  # component column -> cell


@dataclass
class AllocationVehicle:
    name: str
    driver_row: int
    driver: dict[str, Decimal]
    totals: dict[str, Decimal]  # vehicle total row, per column
    lp_subtotals: dict[str, Decimal]
    gp_subtotals: dict[str, Decimal]
    investors: list[AllocationInvestor]
    subtotal_rows: dict[str, int | None]

    @property
    def limited_partners(self) -> list[AllocationInvestor]:
        return [i for i in self.investors if not i.is_gp]


@dataclass
class AllocationData:
    sheet: SheetModel
    layout: AllocationLayout
    event: EventInfo
    components: list[ComponentColumn]
    vehicles: list[AllocationVehicle]
    fund_drivers: dict[str, Decimal]
    grand_totals: dict[str, Decimal]

    @property
    def active_components(self) -> list[ComponentColumn]:
        out = []
        for comp in self.components:
            active = comp.active if comp.active is not None else bool(self.fund_drivers.get(comp.column))
            if active:
                out.append(comp)
        return out

    @property
    def inactive_components(self) -> list[ComponentColumn]:
        active = {c.column for c in self.active_components}
        return [c for c in self.components if c.column not in active]

    @property
    def event_gross(self) -> Decimal:
        return sum((self.fund_drivers.get(c.column, Decimal("0")) for c in self.active_components), Decimal("0"))

    @property
    def investors(self) -> list[AllocationInvestor]:
        return [i for v in self.vehicles for i in v.investors]

    def event_total_column(self, side: str) -> str | None:
        return next((t.column for t in self.layout.event_total_columns if t.side == side), None)


def _row_values(sheet: SheetModel, row: int | None, columns: list[str]) -> dict[str, Decimal]:
    if row is None:
        return {}
    return {col: to_money(sheet.value(f"{col}{row}")) for col in columns}


def _numeric_columns(layout: AllocationLayout) -> list[str]:
    cols = [layout.columns.commitment, layout.columns.commitment_pct]
    cols += [c.column for c in layout.components] + [t.column for t in layout.event_total_columns]
    cols += [c for c in layout.roll_forward.model_dump().values() if c]
    for extra in (layout.columns.cash_due, layout.columns.late_interest, layout.columns.distribution_basis,
                  layout.columns.distribution_basis_pct):
        if extra:
            cols.append(extra)
    return list(dict.fromkeys(cols))


def _affiliate(value: Any) -> bool | None:
    text = norm_text(value)
    if text in ("y", "yes", "affiliate", "true"):
        return True
    if text in ("n", "no", "false"):
        return False
    return None


def extract_allocation(model: WorkbookModel, layout: AllocationLayout) -> AllocationData:
    sheet = model.sheet(layout.sheet)
    cols = layout.columns
    numeric = _numeric_columns(layout)
    comp_cols = [c.column for c in layout.components]
    fund_drivers = _row_values(sheet, layout.fund_driver_row, comp_cols + [t.column for t in layout.event_total_columns])

    rate_cell = layout.event.carried_interest_rate_cell
    notice_cell = sheet.cell(layout.event.notice_date_cell) if layout.event.notice_date_cell else None
    due_cell = sheet.cell(layout.event.due_date_cell) if layout.event.due_date_cell else None
    label = layout.event.label
    if layout.event.label_cell and is_text(sheet.value(layout.event.label_cell)):
        label = str(sheet.value(layout.event.label_cell)).strip()
    event = EventInfo(
        event_type=layout.event.event_type,
        label=label,
        notice_date=to_date(notice_cell.value, allow_serial=True) if notice_cell else None,
        due_date=to_date(due_cell.value, allow_serial=True) if due_cell else None,
        notice_cell=notice_cell,
        due_cell=due_cell,
        carried_interest_rate=to_decimal(sheet.value(rate_cell)) if rate_cell else None,
    )

    vehicles: list[AllocationVehicle] = []
    for vrows in layout.vehicles:
        gp_rows = set(vrows.gp_rows)
        rows = [r for r in rows_between(vrows.investor_rows) if is_text(sheet.value(f"{cols.investor}{r}"))]
        rows += sorted(gp_rows)
        investors = []
        for row in rows:
            rf = {name: (to_decimal(sheet.value(f"{col}{row}")) if col else None)
                  for name, col in layout.roll_forward.model_dump().items()}
            call_col = layout_total(layout, "call")
            dist_col = layout_total(layout, "distribution")
            investors.append(AllocationInvestor(
                name=str(sheet.value(f"{cols.investor}{row}")).strip(),
                row=row,
                vehicle=vrows.name,
                is_gp=row in gp_rows,
                affiliate=_affiliate(sheet.value(f"{cols.affiliate_flag}{row}")) if cols.affiliate_flag else None,
                commitment=to_money(sheet.value(f"{cols.commitment}{row}")),
                commitment_pct=to_decimal(sheet.value(f"{cols.commitment_pct}{row}")),
                amounts={c: to_money(sheet.value(f"{c}{row}")) for c in comp_cols},
                formulas={c: (sheet.cell(f"{c}{row}").formula if sheet.cell(f"{c}{row}") else None) for c in comp_cols},
                call_total=to_decimal(sheet.value(f"{call_col}{row}")) if call_col else None,
                dist_total=to_decimal(sheet.value(f"{dist_col}{row}")) if dist_col else None,
                roll_forward=rf,
                distribution_basis=to_decimal(sheet.value(f"{cols.distribution_basis}{row}"))
                if cols.distribution_basis else None,
                received=sheet.value(f"{cols.received}{row}") if cols.received else None,
                received_date=sheet.value(f"{cols.received_date}{row}") if cols.received_date else None,
                cells={c: sheet.cell(f"{c}{row}") for c in comp_cols},
            ))
        driver_row = vrows.driver_row or layout.fund_driver_row
        vehicles.append(AllocationVehicle(
            name=vrows.name,
            driver_row=driver_row,
            driver=_row_values(sheet, driver_row, comp_cols + [t.column for t in layout.event_total_columns]),
            totals=_row_values(sheet, vrows.subtotal_rows.total, numeric),
            lp_subtotals=_row_values(sheet, vrows.subtotal_rows.limited_partners, numeric),
            gp_subtotals=_row_values(sheet, vrows.subtotal_rows.general_partner, numeric),
            investors=investors,
            subtotal_rows=vrows.subtotal_rows.model_dump(),
        ))
    grand_row = layout.grand_total_row or (layout.vehicles[-1].subtotal_rows.total if len(layout.vehicles) == 1 else None)
    return AllocationData(
        sheet=sheet,
        layout=layout,
        event=event,
        components=list(layout.components),
        vehicles=vehicles,
        fund_drivers=fund_drivers,
        grand_totals=_row_values(sheet, grand_row, numeric),
    )


def layout_total(layout: AllocationLayout, side: str) -> str | None:
    return next((t.column for t in layout.event_total_columns if t.side == side), None)


# --- ITD ------------------------------------------------------------------------------------


@dataclass
class ItdInvestor:
    name: str
    row: int
    vehicle: str
    is_gp: bool
    cumulative: dict[str, Decimal | None]
    values: dict[str, Decimal]  # block component (and total) column -> amount
    formulas: dict[str, str | None]


@dataclass
class ItdVehicle:
    name: str
    investors: list[ItdInvestor]
    subtotal_rows: dict[str, int | None]
    totals: dict[str, Decimal]


@dataclass
class ItdData:
    sheet: SheetModel
    layout: ItdLayout
    event_blocks: list[EventBlock]
    vehicles: list[ItdVehicle]
    marks: dict[str, list[str]]  # block column -> classification categories marked 'X'
    overlay_marks: dict[str, list[str]]

    @property
    def current_block(self) -> EventBlock | None:
        current = [b for b in self.event_blocks if b.is_current]
        return current[0] if len(current) == 1 else None

    @property
    def prior_blocks(self) -> list[EventBlock]:
        return [b for b in self.event_blocks if not b.is_current]

    @property
    def investors(self) -> list[ItdInvestor]:
        return [i for v in self.vehicles for i in v.investors]

    def block_columns(self, block: EventBlock) -> list[str]:
        return [c.column for c in block.components]


def _is_mark(value: Any) -> bool:
    return isinstance(value, str) and value.strip().upper() == "X"


def extract_itd(model: WorkbookModel, layout: ItdLayout) -> ItdData:
    sheet = model.sheet(layout.sheet)
    block_cols = [c.column for b in layout.event_blocks for c in b.components]
    totals_cols = [b.total_column for b in layout.event_blocks if b.total_column]
    marks: dict[str, list[str]] = {}
    for column in block_cols:
        marks[column] = [cat for cat, row in layout.classification_rows.model_dump().items()
                         if row is not None and _is_mark(sheet.value(f"{column}{row}"))]
    overlay_marks = {column: [o.name for o in layout.overlay_rows if _is_mark(sheet.value(f"{column}{o.row}"))]
                     for column in block_cols}
    cum_cols = layout.cumulative_columns.model_dump()
    all_cols = block_cols + totals_cols
    vehicles = []
    for vrows in layout.vehicles:
        gp_rows = set(vrows.gp_rows)
        rows = [r for r in rows_between(vrows.investor_rows) if is_text(sheet.value(f"{layout.investor_column}{r}"))]
        rows += sorted(gp_rows)
        investors = []
        for row in rows:
            investors.append(ItdInvestor(
                name=str(sheet.value(f"{layout.investor_column}{row}")).strip(),
                row=row,
                vehicle=vrows.name,
                is_gp=row in gp_rows,
                cumulative={k: (to_decimal(sheet.value(f"{c}{row}")) if c else None) for k, c in cum_cols.items()},
                values={c: to_money(sheet.value(f"{c}{row}")) for c in all_cols},
                formulas={c: (sheet.cell(f"{c}{row}").formula if sheet.cell(f"{c}{row}") else None) for c in all_cols},
            ))
        vehicles.append(ItdVehicle(
            name=vrows.name,
            investors=investors,
            subtotal_rows=vrows.subtotal_rows.model_dump(),
            totals=_row_values(sheet, vrows.subtotal_rows.total, all_cols + [c for c in cum_cols.values() if c]),
        ))
    return ItdData(sheet=sheet, layout=layout, event_blocks=list(layout.event_blocks), vehicles=vehicles,
                   marks=marks, overlay_marks=overlay_marks)


# --- Summary --------------------------------------------------------------------------------


@dataclass
class SummaryLineData:
    label: str | None
    amount: Decimal
    component_type: str
    side: str
    amount_cell: str


@dataclass
class SummaryData:
    sheet: SheetModel
    layout: SummaryLayout
    title: str | None
    notice_date: dt.date | None
    due_date: dt.date | None
    fund_commitment: Decimal | None
    lines: list[SummaryLineData]
    event_total: Decimal
    check_values: list[Decimal]
    check_cells: list[str]


def extract_summary(model: WorkbookModel, layout: SummaryLayout) -> SummaryData:
    sheet = model.sheet(layout.sheet)
    lines = [
        SummaryLineData(
            label=str(sheet.value(line.label_cell)) if line.label_cell and sheet.value(line.label_cell) else None,
            amount=to_money(sheet.value(line.amount_cell)),
            component_type=line.component_type,
            side=line.side,
            amount_cell=line.amount_cell,
        )
        for line in layout.component_lines
    ]
    return SummaryData(
        sheet=sheet,
        layout=layout,
        title=str(sheet.value(layout.title_cell)) if layout.title_cell and sheet.value(layout.title_cell) else None,
        notice_date=to_date(sheet.value(layout.notice_date_cell), allow_serial=True) if layout.notice_date_cell else None,
        due_date=to_date(sheet.value(layout.due_date_cell), allow_serial=True) if layout.due_date_cell else None,
        fund_commitment=to_decimal(sheet.value(layout.fund_commitment_cell)) if layout.fund_commitment_cell else None,
        lines=lines,
        event_total=to_money(sheet.value(layout.event_total_cell)),
        check_values=[to_money(sheet.value(cell)) for cell in layout.check_cells],
        check_cells=list(layout.check_cells),
    )


# --- Merge ----------------------------------------------------------------------------------


@dataclass
class MergeRow:
    name: str
    row: int
    short_name: Any
    investor_id: Any
    fund_id: Any
    file_name: Any
    letter_date: Any
    due_date: Any
    commitment: Decimal | None
    amounts: dict[str, Decimal]
    event_total: Decimal | None


@dataclass
class MergeData:
    sheet: SheetModel
    layout: MergeLayout
    vehicle: str | None
    rows: list[MergeRow]


def _id(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def extract_merge(model: WorkbookModel, layout: MergeLayout) -> MergeData:
    sheet = model.sheet(layout.sheet)
    cols = layout.columns

    def val(col: str | None, row: int) -> Any:
        return sheet.value(f"{col}{row}") if col else None

    rows = []
    for row in range(layout.first_data_row, layout.last_data_row + 1):
        name = val(cols.investor, row)
        if not is_text(name):
            continue
        rows.append(MergeRow(
            name=str(name).strip(),
            row=row,
            short_name=val(cols.short_name, row),
            investor_id=_id(val(cols.investor_id, row)),
            fund_id=_id(val(cols.fund_id, row)),
            file_name=val(cols.file_name, row),
            letter_date=val(cols.letter_date, row),
            due_date=val(cols.due_date, row),
            commitment=to_decimal(val(cols.commitment, row)),
            amounts={c.column: to_money(val(c.column, row)) for c in layout.component_columns},
            event_total=to_decimal(val(cols.event_total, row)),
        ))
    return MergeData(sheet=sheet, layout=layout, vehicle=layout.vehicle, rows=rows)


# --- Mgmt fee -------------------------------------------------------------------------------


@dataclass
class FeeRow:
    name: str
    row: int
    vehicle: Any
    affiliate_flag: Any
    commitment: Decimal | None
    fees: dict[str, Decimal]
    formulas: dict[str, str | None]
    is_gp: bool


@dataclass
class FeeData:
    sheet: SheetModel
    layout: MgmtFeeLayout
    rows: list[FeeRow]
    rates: list[Decimal]
    period_fractions: list[Decimal]
    period_labels: list[str]
    totals: dict[str, Decimal]

    @property
    def total(self) -> Decimal:
        return sum(self.totals.values(), Decimal("0"))


def extract_fee(model: WorkbookModel, layout: MgmtFeeLayout) -> FeeData:
    sheet = model.sheet(layout.sheet)
    cols = layout.columns
    fee_cols = [f.column for f in layout.fee_columns]
    gp_rows = set(layout.gp_rows)
    rows = []
    for row in list(rows_between(layout.investor_rows)) + sorted(gp_rows):
        name = sheet.value(f"{cols.investor}{row}")
        if not is_text(name):
            continue
        rows.append(FeeRow(
            name=str(name).strip(),
            row=row,
            vehicle=sheet.value(f"{cols.vehicle}{row}") if cols.vehicle else None,
            affiliate_flag=sheet.value(f"{cols.affiliate_flag}{row}") if cols.affiliate_flag else None,
            commitment=to_decimal(sheet.value(f"{cols.commitment}{row}")) if cols.commitment else None,
            fees={c: to_money(sheet.value(f"{c}{row}")) for c in fee_cols},
            formulas={c: (sheet.cell(f"{c}{row}").formula if sheet.cell(f"{c}{row}") else None) for c in fee_cols},
            is_gp=row in gp_rows,
        ))
    total_row = layout.subtotal_rows.total
    totals = _row_values(sheet, total_row, fee_cols) if total_row else {
        c: sum((r.fees[c] for r in rows), Decimal("0")) for c in fee_cols}
    labels = []
    for fee in layout.fee_columns:
        labels.append(fee.period_label or str(sheet.value(f"{fee.column}{layout.header_row}") or "").strip())
    return FeeData(
        sheet=sheet,
        layout=layout,
        rows=rows,
        rates=[to_money(sheet.value(c)) for c in layout.rate_cells],
        period_fractions=[to_money(sheet.value(c)) for c in layout.period_fraction_cells],
        period_labels=labels,
        totals=totals,
    )


# --- Investor data / holidays ---------------------------------------------------------------


@dataclass
class InvestorDataRow:
    name: str
    row: int
    investor_id: Any
    fund_id: Any
    fund_name: Any


@dataclass
class InvestorData:
    sheet: SheetModel
    layout: InvestorDataLayout
    rows: list[InvestorDataRow]

    @property
    def by_investor_id(self) -> dict[Any, InvestorDataRow]:
        return {r.investor_id: r for r in self.rows if r.investor_id is not None}

    @property
    def by_name(self) -> dict[str, InvestorDataRow]:
        return {r.name: r for r in self.rows}


def extract_investor_data(model: WorkbookModel, layout: InvestorDataLayout) -> InvestorData:
    sheet = model.sheet(layout.sheet)
    cols = layout.columns
    rows = []
    for row in range(layout.first_data_row, layout.last_data_row + 1):
        name = sheet.value(f"{cols.investor_name}{row}")
        if not is_text(name):
            continue
        rows.append(InvestorDataRow(
            name=str(name).strip(),
            row=row,
            investor_id=_id(sheet.value(f"{cols.investor_id}{row}")) if cols.investor_id else None,
            fund_id=_id(sheet.value(f"{cols.fund_id}{row}")) if cols.fund_id else None,
            fund_name=sheet.value(f"{cols.fund_name}{row}") if cols.fund_name else None,
        ))
    return InvestorData(sheet=sheet, layout=layout, rows=rows)


def extract_holidays(model: WorkbookModel, layout: HolidayCalendarLayout) -> list[dt.date]:
    sheet = model.sheet(layout.sheet)
    days = [to_date(sheet.value(f"{layout.date_column}{r}")) for r in range(layout.first_row, layout.last_row + 1)]
    return [d for d in days if d is not None]


# --- Workbook ---------------------------------------------------------------------------------


@dataclass
class WorkbookData:
    model: WorkbookModel
    layouts: dict[str, BaseModel]
    allocation: AllocationData | None = None
    itd: ItdData | None = None
    summary: SummaryData | None = None
    merges: list[MergeData] = field(default_factory=list)
    mgmt_fee: FeeData | None = None
    investor_data: InvestorData | None = None
    holidays: list[dt.date] = field(default_factory=list)
    has_holiday_calendar: bool = False
    extraction_errors: dict[str, str] = field(default_factory=dict)  # role -> reason

    @property
    def processed_sheets(self) -> list[str]:
        return list(self.layouts)


def extract_workbook_data(model: WorkbookModel, layouts: dict[str, BaseModel]) -> WorkbookData:
    data = WorkbookData(model=model, layouts=dict(layouts))
    for name, layout in layouts.items():
        try:
            if isinstance(layout, AllocationLayout):
                data.allocation = extract_allocation(model, layout)
            elif isinstance(layout, ItdLayout):
                data.itd = extract_itd(model, layout)
            elif isinstance(layout, SummaryLayout):
                data.summary = extract_summary(model, layout)
            elif isinstance(layout, MergeLayout):
                data.merges.append(extract_merge(model, layout))
            elif isinstance(layout, MgmtFeeLayout):
                data.mgmt_fee = extract_fee(model, layout)
            elif isinstance(layout, InvestorDataLayout):
                data.investor_data = extract_investor_data(model, layout)
            elif isinstance(layout, HolidayCalendarLayout):
                data.holidays = extract_holidays(model, layout)
                data.has_holiday_calendar = True
        except Exception as exc:  # a bad layout must not take the whole run down
            data.extraction_errors[getattr(layout, "role", name)] = f"{type(exc).__name__}: {exc}"
    return data
