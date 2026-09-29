"""SheetLayout models: where things are on each capital-event sheet.

The LLM layout mapper returns one of these per relevant sheet (via strict structured
output); ``layout_validator`` checks it against the actual cells before extraction
trusts it. Rows are 1-based ints, columns are letters, cells are A1 coordinates.
"""
from __future__ import annotations

import copy
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_serializer

ComponentType = Literal[
    "investment",
    "org_expense",
    "partnership_expense",
    "mgmt_fee",
    "placement_fee",
    "late_interest",
    "return_of_capital",
    "realized_gain",
    "dividend_income",
    "pref",
    "catch_up",
    "carry",
    "tax_distribution",
    "other",
]
Side = Literal["call", "distribution"]
EventType = Literal["capital_call", "distribution", "net_event", "transfer", "other"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SubtotalRows(_Base):
    limited_partners: Optional[int] = None
    general_partner: Optional[int] = None
    total: Optional[int] = None


class VehicleRows(_Base):
    name: str
    title_row: Optional[int] = None
    driver_row: Optional[int] = None
    investor_rows: list[int] = Field(..., description="[first_row, last_row] of the investor rows (inclusive)")
    gp_rows: list[int] = Field(default_factory=list)
    subtotal_rows: SubtotalRows = Field(default_factory=SubtotalRows)


# --- Allocation --------------------------------------------------------------------------


class AllocationEvent(_Base):
    event_type: Optional[EventType] = None
    label: Optional[str] = None
    label_cell: Optional[str] = None
    notice_date_cell: Optional[str] = None
    due_date_cell: Optional[str] = None
    carried_interest_rate_cell: Optional[str] = None


class AllocationColumns(_Base):
    investor: str
    affiliate_flag: Optional[str] = None
    commitment: str
    commitment_pct: str
    late_interest: Optional[str] = None
    cash_due: Optional[str] = None
    received: Optional[str] = None
    received_date: Optional[str] = None
    distribution_basis: Optional[str] = None
    distribution_basis_pct: Optional[str] = None
    mgmt_fee_rate: Optional[str] = None


class ComponentColumn(_Base):
    column: str
    header: Optional[str] = None
    component_type: ComponentType
    side: Side
    active: Optional[bool] = None


class EventTotalColumn(_Base):
    column: str
    side: Side


class RollForwardColumns(_Base):
    commitment: Optional[str] = None
    prior_contributions: Optional[str] = None
    prior_recallable: Optional[str] = None
    current_call: Optional[str] = None
    current_recallable: Optional[str] = None
    remaining_commitment: Optional[str] = None


class AllocationLayout(_Base):
    role: Literal["allocation"]
    sheet: str
    header_row: int
    fund_driver_row: int
    event: AllocationEvent = Field(default_factory=AllocationEvent)
    columns: AllocationColumns
    components: list[ComponentColumn]
    event_total_columns: list[EventTotalColumn] = Field(default_factory=list)
    roll_forward: RollForwardColumns = Field(default_factory=RollForwardColumns)
    vehicles: list[VehicleRows]
    grand_total_row: Optional[int] = None
    check_rows: list[int] = Field(default_factory=list)


# --- ITD ------------------------------------------------------------------------------------


class ClassificationRows(_Base):
    investment_contributions: Optional[int] = None
    cost_contributions: Optional[int] = None
    recallable_distributions: Optional[int] = None
    non_recallable_distributions: Optional[int] = None
    tax_withholding: Optional[int] = None


class OverlayRow(_Base):
    name: str
    row: int


class CumulativeColumns(_Base):
    commitment: Optional[str] = None
    investment_contributions: Optional[str] = None
    cost_contributions: Optional[str] = None
    total_contributions: Optional[str] = None
    unfunded: Optional[str] = None
    recallable_distributions: Optional[str] = None
    non_recallable_distributions: Optional[str] = None
    total_distributions: Optional[str] = None


class BlockComponent(_Base):
    column: str
    component_type: ComponentType
    side: Side


class EventBlock(_Base):
    label: str
    event_type: EventType
    number: Optional[int] = None
    date: Optional[str] = Field(default=None, description="ISO date from the header, if any")
    first_column: str
    last_column: str
    total_column: Optional[str] = None
    is_current: bool = False
    components: list[BlockComponent] = Field(default_factory=list)


class ItdLayout(_Base):
    role: Literal["itd"]
    sheet: str
    classification_rows: ClassificationRows
    overlay_rows: list[OverlayRow] = Field(default_factory=list)
    event_header_row: int
    subheader_row: int
    investor_column: str
    cumulative_columns: CumulativeColumns
    event_blocks: list[EventBlock]
    vehicles: list[VehicleRows]
    check_rows: list[int] = Field(default_factory=list)

    @field_validator("overlay_rows", mode="before")
    @classmethod
    def _overlay_from_mapping(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return [{"name": k, "row": v} for k, v in value.items()]
        return value

    @model_serializer(mode="wrap")
    def _dump_overlay_as_mapping(self, handler):
        data = handler(self)
        data["overlay_rows"] = {o["name"]: o["row"] for o in data.get("overlay_rows") or []}
        return data


# --- Summary / Merge / Fee / Investor data / Holidays ----------------------------------------


class SummaryLine(_Base):
    label_cell: Optional[str] = None
    amount_cell: str
    component_type: ComponentType
    side: Side


class SectionTotal(_Base):
    side: Side
    cell: str


class SummaryLayout(_Base):
    role: Literal["summary"]
    sheet: str
    title_cell: Optional[str] = None
    notice_date_cell: Optional[str] = None
    due_date_cell: Optional[str] = None
    fund_commitment_cell: Optional[str] = None
    component_lines: list[SummaryLine] = Field(default_factory=list)
    section_totals: list[SectionTotal] = Field(default_factory=list)
    event_total_cell: str
    check_cells: list[str] = Field(default_factory=list)


class MergeColumns(_Base):
    investor: str
    short_name: Optional[str] = None
    letter_date: Optional[str] = None
    due_date: Optional[str] = None
    investor_id: Optional[str] = None
    fund_id: Optional[str] = None
    file_name: Optional[str] = None
    commitment: Optional[str] = None
    commitment_pct: Optional[str] = None
    event_total: Optional[str] = None
    check: Optional[str] = None


class MergeLayout(_Base):
    role: Literal["merge"]
    sheet: str
    vehicle: Optional[str] = None
    header_row: int
    first_data_row: int
    last_data_row: int
    columns: MergeColumns
    component_columns: list[BlockComponent] = Field(default_factory=list)
    total_row: Optional[int] = None


class FeeColumns(_Base):
    investor: str
    vehicle: Optional[str] = None
    affiliate_flag: Optional[str] = None
    commitment: Optional[str] = None


class FeeColumn(_Base):
    column: str
    period_label: Optional[str] = None


class MgmtFeeLayout(_Base):
    role: Literal["mgmt_fee"]
    sheet: str
    header_row: int
    investor_rows: list[int]
    gp_rows: list[int] = Field(default_factory=list)
    columns: FeeColumns
    rate_cells: list[str] = Field(default_factory=list)
    period_fraction_cells: list[str] = Field(default_factory=list)
    fee_columns: list[FeeColumn]
    subtotal_rows: SubtotalRows = Field(default_factory=SubtotalRows)
    check_rows: list[int] = Field(default_factory=list)


class InvestorDataColumns(_Base):
    fund_name: Optional[str] = None
    fund_id: Optional[str] = None
    investor_name: str
    investor_id: Optional[str] = None


class InvestorDataLayout(_Base):
    role: Literal["investor_data"]
    sheet: str
    header_row: int
    first_data_row: int
    last_data_row: int
    columns: InvestorDataColumns


class HolidayCalendarLayout(_Base):
    role: Literal["holiday_calendar"]
    sheet: str
    header_row: Optional[int] = None
    date_column: str
    first_row: int
    last_row: int


SheetLayout = Annotated[
    Union[
        AllocationLayout,
        ItdLayout,
        SummaryLayout,
        MergeLayout,
        MgmtFeeLayout,
        InvestorDataLayout,
        HolidayCalendarLayout,
    ],
    Field(discriminator="role"),
]

LAYOUT_MODELS: dict[str, type[BaseModel]] = {
    "allocation": AllocationLayout,
    "itd": ItdLayout,
    "summary": SummaryLayout,
    "merge": MergeLayout,
    "mgmt_fee": MgmtFeeLayout,
    "investor_data": InvestorDataLayout,
    "holiday_calendar": HolidayCalendarLayout,
}

_ADAPTER = TypeAdapter(SheetLayout)


def parse_layout(raw: dict[str, Any]) -> BaseModel:
    role = raw.get("role")
    if role not in LAYOUT_MODELS:
        raise ValueError(f"Unknown layout role: {role!r}")
    return _ADAPTER.validate_python(raw)


def layout_json_schema(role: str) -> dict[str, Any]:
    """Strict-mode JSON schema for structured output (no $refs, no free-form maps)."""
    model = LAYOUT_MODELS.get(role)
    if model is None:
        raise ValueError(f"Unknown layout role: {role!r}")
    return to_strict_schema(model.model_json_schema())


def to_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            out = {k: resolve(v) for k, v in node.items() if k not in ("$defs", "title", "default")}
            if out.get("type") == "object" and "properties" in out:
                out["required"] = list(out["properties"])
                out["additionalProperties"] = False
            return out
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)
