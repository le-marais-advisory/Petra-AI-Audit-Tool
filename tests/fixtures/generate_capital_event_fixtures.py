"""Generate synthetic capital-event workbooks for the workbook test and eval suites.

The workbooks mirror the structure of a real fund capital-event roll-forward model
(Allocation / ITD Capital Activity / Summary / Merge / DX Investor Data / Mgmt Fee
Calc / bank holidays, plus legacy sheets), with fictional investors and amounts. No
real client data is used.

Every fixture is described by a ``FixtureSpec`` (event type x layout variant x an
optional seeded defect) and ``build_fixture`` returns a ``FixtureManifest`` carrying
the ground truth the tests assert against:

  - ``sheet_roles``      sheet name -> role (allocation, itd, summary, merge, ...)
  - ``layouts``          sheet name -> golden layout dict (the contract the LLM
                         layout mapper must reproduce and the extractor consumes)
  - ``truth``            key numbers per sheet, for extraction tests
  - ``expected_verdicts`` rule id -> verdict for the deterministic rules
                         (``None`` = not asserted until the FA calibration answer)
  - ``facts``            expectations for the hybrid-rule facts pre-pass

openpyxl cannot write a formula together with its cached value, so the generator
computes every value in Python (following the same data flow Excel would) and injects
the cached ``<v>`` values into the saved sheet XML.

Run from the repo root to write the standard set used by the eval suite:
    python tests/fixtures/generate_capital_event_fixtures.py
which writes to tests/fixtures/documents/capital_event/.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.utils import get_column_letter

OUT_DIR = Path(__file__).resolve().parent / "documents" / "capital_event"

MONEY_FMT = '_(* #,##0.00_);_(* \\(#,##0.00\\);_(* "-"??_);_(@_)'
PCT_FMT = "0.0000%"
DATE_LONG_FMT = "mmmm d, yyyy"
DATE_SHORT_FMT = "m/d/yyyy"

EXCEL_EPOCH = dt.date(1899, 12, 30)
CENT = Decimal("0.01")

FUND_NAME = "Example Growth Fund III, LP"
FUND_SHORT = "egf-iii"
MGMT_FEE_RATE = Decimal("0.02")
FEE_PERIOD_FRACTION = Decimal("0.25")
CARRY_RATE = Decimal("0.20")

EVENT_TYPES = ("capital_call", "distribution", "net_event")

# Deterministic rules (plan section 4). Hybrid rules are covered by the facts tests.
DETERMINISTIC_RULE_IDS = (
    "CE-ALLOC-PER-LP-FORMULAS",
    "CE-ALLOC-VEHICLE-TIE",
    "CE-ALLOC-GROSS-TIE",
    "CE-ALLOC-REFOOT",
    "CE-ALLOC-PRO-RATA-PARITY",
    "CE-ALLOC-PLUG-DISCIPLINE",
    "CE-ALLOC-COMMITMENTS",
    "CE-ALLOC-ROUNDING",
    "CE-ALLOC-MERGED-CELLS",
    "CE-RF-FOOTING",
    "CE-RF-CURRENT-CALL-LINK",
    "CE-ITD-PRIOR-FROZEN",
    "CE-ITD-CUMULATIVE",
    "CE-ITD-EVENT-SEQUENCE",
    "CE-SUM-CHECKS-ZERO",
    "CE-DATE-VALIDITY",
    "CE-DATE-ORDER",
    "CE-FMT-DATE-DISPLAY",
    "CE-FMT-ACCOUNTING",
    "CE-FMT-NO-FORMULA-ERRORS",
    "CE-WB-NO-HIDDEN-DATA",
    "CE-WB-PAGE-BREAK-VIEW",
    "CE-WB-FILE-NAMING",
    "CE-TIE-MGMT-FEE",
    "CE-TIE-ITD-ALLOCATION",
    "CE-TIE-ITD-COMMITMENTS",
    "CE-ID-INVESTOR-KEYS",
    "CE-DIST-ROC-LIMIT",
)

HYBRID_RULE_IDS = (
    "CE-WB-SHEETS-PRESENT",
    "CE-WB-MERGE-TABS",
    "CE-WB-NO-PLACEHOLDERS",
    "CE-ALLOC-FEE-TIERS",
    "CE-ALLOC-COMPONENT-PARTICIPATION",
    "CE-ALLOC-STALE-COMPONENTS",
    "CE-ALLOC-SIGNAGE",
    "CE-ITD-EVENT-BLOCK",
    "CE-TIE-SUMMARY",
    "CE-DATE-CONSISTENCY",
    "CE-NET-EVENT-STRUCTURE",
    "CE-DIST-CARRY-SPLIT",
)

ROLES = ("allocation", "itd", "summary", "merge", "mgmt_fee", "investor_data", "holiday_calendar", "other")

# Sheet roles each event type needs (mirrors config/document_types/capital_event.yaml).
RELEVANT_ROLES = {
    "capital_call": {"allocation", "itd", "summary", "merge", "mgmt_fee", "investor_data", "holiday_calendar"},
    "distribution": {"allocation", "itd", "summary", "merge", "investor_data", "holiday_calendar"},
    "net_event": {"allocation", "itd", "summary", "merge", "mgmt_fee", "investor_data", "holiday_calendar"},
}

HOLIDAYS_2026 = [
    ("New Year's Day", dt.date(2026, 1, 1)),
    ("Martin Luther King, Jr. Day", dt.date(2026, 1, 19)),
    ("Presidents' Day", dt.date(2026, 2, 16)),
    ("Good Friday", dt.date(2026, 4, 3)),
    ("Memorial Day", dt.date(2026, 5, 25)),
    ("Juneteenth", dt.date(2026, 6, 19)),
    ("Independence Day (observed)", dt.date(2026, 7, 3)),
    ("Labor Day", dt.date(2026, 9, 7)),
    ("Columbus Day", dt.date(2026, 10, 12)),
    ("Veterans Day", dt.date(2026, 11, 11)),
    ("Thanksgiving Day", dt.date(2026, 11, 26)),
    ("Christmas Day", dt.date(2026, 12, 25)),
]
NOTICE_DATE = dt.date(2026, 5, 27)


# ---------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LayoutVariant:
    name: str
    row_offset: int = 0
    col_offset: int = 0
    vehicles: int = 1
    alt_headers: bool = False
    sheet_names: tuple[tuple[str, str], ...] = ()

    def sheet_name(self, role: str) -> str:
        return dict(self.sheet_names).get(role, _DEFAULT_SHEET_NAMES[role])


_DEFAULT_SHEET_NAMES = {
    "allocation": "Allocation",
    "itd": "ITD Capital Activity",
    "summary": "Summary",
    "mgmt_fee": "Mgmt Fee Calc",
    "investor_data": "DX Investor Data",
    "holiday_calendar": "Bank Holidays",
}

VARIANTS: dict[str, LayoutVariant] = {
    # Mirrors the reference sample's geometry.
    "standard": LayoutVariant(name="standard"),
    # Same model with shifted anchors, renamed sheets, alternative header wording and a
    # Merge tab named after the fund (as in the reference sample).
    "shifted": LayoutVariant(
        name="shifted",
        row_offset=3,
        col_offset=2,
        alt_headers=True,
        sheet_names=(
            ("allocation", "Capital Allocation"),
            ("itd", "ITD"),
            ("summary", "Call Summary"),
            ("mgmt_fee", "MF"),
            ("holiday_calendar", "JPM Holidays"),
        ),
    ),
    # Main fund + parallel vehicle, one Merge tab per vehicle.
    "two_vehicles": LayoutVariant(name="two_vehicles", vehicles=2),
}


@dataclass(frozen=True)
class DefectSpec:
    name: str
    description: str
    event_types: tuple[str, ...]
    verdicts: dict[str, str | None]  # overrides applied on top of the clean baseline
    facts: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending_calibration: int | None = None  # FA calibration item number, when relevant


def _d(name, description, event_types=("capital_call",), verdicts=None, facts=None, pending=None) -> DefectSpec:
    return DefectSpec(name, description, tuple(event_types), dict(verdicts or {}), dict(facts or {}), pending)


DEFECTS: dict[str, DefectSpec] = {
    d.name: d
    for d in [
        _d("hardcoded_lp_cell", "A per-LP investment cell holds a typed number instead of the cascade formula.",
           verdicts={"CE-ALLOC-PER-LP-FORMULAS": "fail"}),
        _d("concentrated_plug", "$500 moved between two LPs via formula offsets (totals still tie).",
           verdicts={"CE-ALLOC-PRO-RATA-PARITY": "fail", "CE-ALLOC-PLUG-DISCIPLINE": "fail"}),
        _d("plug_on_affiliate", "The investment rounding plug sits on the affiliate LP.",
           verdicts={"CE-ALLOC-PLUG-DISCIPLINE": "fail"}),
        _d("short_subtotal_range", "The LP subtotal of Prior Capital Contributions omits the first LP.",
           verdicts={"CE-ALLOC-REFOOT": "fail", "CE-RF-FOOTING": "fail"}),
        _d("commitment_pct_swap", "Two LPs carry typed commitment % values off by +/-0.03 pp.",
           verdicts={"CE-ALLOC-COMMITMENTS": "fail", "CE-ALLOC-PRO-RATA-PARITY": "fail"}),
        _d("live_prior_itd_link", "A prior ITD event cell is a live formula into the Allocation sheet.",
           verdicts={"CE-ITD-PRIOR-FROZEN": "fail"}),
        _d("itd_missing_x", "A current ITD block column has no classification X.",
           verdicts={"CE-TIE-ITD-COMMITMENTS": "fail"},
           facts={"CE-ITD-EVENT-BLOCK": {"unclassified_current_columns": 1}}),
        _d("itd_current_value_mismatch", "One current ITD block cell is typed and $10 off the Allocation sheet.",
           verdicts={"CE-TIE-ITD-ALLOCATION": "fail", "CE-TIE-ITD-COMMITMENTS": "fail"}),
        _d("formula_error", "A #REF! error left on the Allocation sheet.",
           verdicts={"CE-FMT-NO-FORMULA-ERRORS": "fail"}),
        _d("weekend_due_date", "Due date typed as a Saturday instead of the WORKDAY formula.",
           verdicts={"CE-DATE-VALIDITY": "fail", "CE-DATE-ORDER": "needs_review"}),
        _d("due_before_notice", "Due date formula counts business days backwards from the notice date.",
           verdicts={"CE-DATE-ORDER": "fail"}),
        _d("investor_name_mismatch", "DX Investor Data spells one participating LP differently.",
           verdicts={"CE-ID-INVESTOR-KEYS": "fail"}),
        _d("fee_tab_value_wrong", "One LP's fee on the fee tab is typed $100 above rate x commitment.",
           verdicts={"CE-TIE-MGMT-FEE": "fail", "CE-ALLOC-PRO-RATA-PARITY": "fail"}),
        _d("stale_fee_period", "The fee tab column is still labelled for the prior quarter.",
           verdicts={"CE-TIE-MGMT-FEE": "fail"},
           facts={"CE-DATE-CONSISTENCY": {"fee_period_mismatch": True}}),
        _d("affiliate_charged_fee", "The affiliate LP is flagged fee-paying on the fee tab and charged a fee.",
           verdicts={"CE-TIE-MGMT-FEE": "fail"}),
        _d("hidden_populated_row", "A participating LP row on the Allocation sheet is hidden.",
           verdicts={"CE-WB-NO-HIDDEN-DATA": "fail"}),
        _d("normal_view", "The Allocation sheet is saved in Normal view.",
           verdicts={"CE-WB-PAGE-BREAK-VIEW": "fail"}),
        _d("generic_file_name", "The workbook is delivered as Book1.xlsx.",
           verdicts={"CE-WB-FILE-NAMING": "fail"}),
        _d("summary_line_hardcoded", "A Summary component line is typed 5 cents off the Allocation total.",
           verdicts={"CE-SUM-CHECKS-ZERO": "fail"}),
        _d("rf_over_contributed", "An LP's prior contributions exceed its commitment (negative unfunded).",
           verdicts={"CE-RF-FOOTING": "fail"}),
        _d("fmt_serial_date", "The notice date on the Allocation sheet renders as a serial number.",
           verdicts={"CE-FMT-DATE-DISPLAY": "fail"}),
        _d("fmt_general_money", "Per-LP investment amounts use the General number format.",
           verdicts={"CE-FMT-ACCOUNTING": "fail"}),
        _d("rf_current_call_stale", "One LP's Current Capital Call still holds the prior event's amount.",
           verdicts={"CE-RF-CURRENT-CALL-LINK": "fail", "CE-TIE-ITD-COMMITMENTS": "fail"}),
        _d("itd_event_number_gap", "The current event is numbered #5 (consistently on every tab) after #3.",
           verdicts={"CE-ITD-EVENT-SEQUENCE": "fail"},
           facts={"CE-DATE-CONSISTENCY": {"distinct_event_numbers": [5]}}),
        _d("stale_component", "An inactive component column still carries prior-event amounts.",
           verdicts={"CE-ALLOC-VEHICLE-TIE": "fail", "CE-ALLOC-GROSS-TIE": "fail"},
           facts={"CE-ALLOC-STALE-COMPONENTS": {"stale_columns": 1}}),
        _d("placeholder_left", "A Merge tab short name still holds a template token.",
           facts={"CE-WB-NO-PLACEHOLDERS": {"placeholder_count": 1}}),
        _d("merged_component_header", "Two component header cells are merged on the header row.",
           verdicts={"CE-ALLOC-MERGED-CELLS": "fail"}),
        _d("unrounded_amount", "One LP's investment formula omits ROUND(...,2).",
           verdicts={"CE-ALLOC-ROUNDING": "fail"}),
        _d("over_returned_capital", "A never-funded LP receives $5,000 of return of capital.",
           event_types=("distribution",),
           verdicts={"CE-DIST-ROC-LIMIT": "fail", "CE-ALLOC-PRO-RATA-PARITY": "fail",
                     "CE-ALLOC-PLUG-DISCIPLINE": "fail"}),
        _d("carry_split_wrong", "The GP takes 25% of the carry component although the rate cell says 20%.",
           event_types=("distribution",),
           facts={"CE-DIST-CARRY-SPLIT": {"gp_share_matches_rate": False}}),
        # --- calibration probes: patterns seen in the reference sample whose verdict
        # depends on the pending FA answers (plan, rule calibration list).
        _d("probe_multi_plug_tied_largest", "Two LPs tie for the largest commitment; residual plugs spread over three LPs.",
           verdicts={"CE-ALLOC-PLUG-DISCIPLINE": None}, pending=1),
        _d("probe_whole_dollar_fees", "Fees rounded to whole dollars while other components are in cents.",
           verdicts={"CE-ALLOC-ROUNDING": None}, pending=2),
        _d("probe_itd_overlay_rows", "ITD band carries overlay rows (Mgmt Fees, Late Interest) marking fee columns twice.",
           facts={"CE-ITD-EVENT-BLOCK": {"overlay_rows": 2}}, pending=3),
        _d("probe_transfer_block", "A non-event 'Transfers' block sits between two ITD events.",
           verdicts={"CE-ITD-EVENT-SEQUENCE": None}, pending=4),
        _d("probe_inactive_investor_na", "A transferred-out LP row on the Merge tab shows #N/A.",
           verdicts={"CE-FMT-NO-FORMULA-ERRORS": None}, pending=11),
        _d("probe_legacy_hidden_errors", "A hidden legacy sheet carries #REF! errors.",
           verdicts={"CE-FMT-NO-FORMULA-ERRORS": None}, pending=14),
        _d("probe_gp_zero_live_link", "Prior ITD blocks keep live Allocation links on the $0 GP row.",
           verdicts={"CE-ITD-PRIOR-FROZEN": None}, pending=10),
        _d("probe_tbd_placeholder", "A 'TBD' wire date on the portfolio tracker for the current deal.",
           facts={"CE-WB-NO-PLACEHOLDERS": {"tbd_cells": 1}}, pending=12),
    ]
}


@dataclass(frozen=True)
class FixtureSpec:
    event_type: str = "capital_call"
    variant: str = "standard"
    defect: str | None = None

    @property
    def fixture_id(self) -> str:
        return "__".join([self.event_type, self.variant, self.defect or "clean"])

    def __post_init__(self) -> None:
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event type {self.event_type!r}")
        if self.variant not in VARIANTS:
            raise ValueError(f"unknown layout variant {self.variant!r}")
        if self.defect is not None:
            defect = DEFECTS[self.defect]
            if self.event_type not in defect.event_types:
                raise ValueError(f"defect {self.defect!r} does not apply to {self.event_type!r}")


@dataclass
class FixtureManifest:
    spec: FixtureSpec
    path: Path
    sheet_roles: dict[str, str]
    relevant_sheets: list[str]
    layouts: dict[str, dict[str, Any]]
    truth: dict[str, Any]
    expected_verdicts: dict[str, str | None]
    facts: dict[str, dict[str, Any]]

    def to_json(self) -> dict[str, Any]:
        return {
            "fixture_id": self.spec.fixture_id,
            "event_type": self.spec.event_type,
            "variant": self.spec.variant,
            "defect": self.spec.defect,
            "file_name": self.path.name,
            "sheet_roles": self.sheet_roles,
            "relevant_sheets": self.relevant_sheets,
            "layouts": self.layouts,
            "truth": self.truth,
            "expected_verdicts": self.expected_verdicts,
            "facts": self.facts,
        }


def clean_verdicts(event_type: str) -> dict[str, str | None]:
    verdicts: dict[str, str | None] = {rule_id: "pass" for rule_id in DETERMINISTIC_RULE_IDS}
    # The Allocation and fee tabs carry no Investor ID column, so by the rule's current
    # wording identity is compared by name only -> needs_review (calibration item 16).
    verdicts["CE-ID-INVESTOR-KEYS"] = "needs_review"
    if event_type == "capital_call":
        verdicts["CE-DIST-ROC-LIMIT"] = "not_applicable"
    if event_type == "distribution":
        verdicts["CE-TIE-MGMT-FEE"] = "not_applicable"
    if event_type == "net_event":
        # Does 'Net Capital Call #4' share the call counter? (calibration item 4)
        verdicts["CE-ITD-EVENT-SEQUENCE"] = None
    return verdicts


def default_specs() -> list[FixtureSpec]:
    """Clean fixtures for every event type x layout variant, plus every defect."""
    specs = [FixtureSpec(event_type=e, variant=v) for e in EVENT_TYPES for v in VARIANTS]
    for defect in DEFECTS.values():
        specs.append(FixtureSpec(event_type=defect.event_types[0], variant="standard", defect=defect.name))
    return specs


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------


@dataclass
class Investor:
    name: str
    vehicle: str
    commitment: Decimal
    affiliate: bool = False
    is_gp: bool = False
    investor_id: int = 0
    fund_id: int = 0
    late_closer: bool = False  # admitted after the last prior call; nothing contributed yet


@dataclass(frozen=True)
class ComponentType:
    key: str
    header: str
    component_type: str  # investment | org_expense | mgmt_fee | placement_fee | return_of_capital | realized_gain | carry | late_interest
    side: str  # call | distribution
    classification: str  # ITD band category


C_INVESTMENT = ComponentType("investment", "Investment", "investment", "call", "investment_contributions")
C_EXPENSES = ComponentType("expenses", "Partnership Expenses / Org Costs", "org_expense", "call", "cost_contributions")
C_FEE = ComponentType("mgmt_fee", "Mgmt Fees", "mgmt_fee", "call", "cost_contributions")
C_PLACEMENT = ComponentType("placement", "Placement Fees", "placement_fee", "call", "cost_contributions")
C_ROC = ComponentType("roc", "Return of Capital", "return_of_capital", "distribution", "non_recallable_distributions")
C_GAIN = ComponentType("gain", "Realized Gain", "realized_gain", "distribution", "non_recallable_distributions")
C_CARRY = ComponentType("carry", "Realized Carry", "carry", "distribution", "non_recallable_distributions")

CALL_COMPONENTS = (C_INVESTMENT, C_EXPENSES, C_FEE, C_PLACEMENT)
DIST_COMPONENTS = (C_ROC, C_GAIN, C_CARRY)


@dataclass
class EventDef:
    kind: str  # capital_call | distribution | net_event
    number: int
    date: dt.date  # due / payment date shown in the ITD header
    drivers: dict[str, Decimal]  # component key -> fund-level amount (distributions negative)
    fee_period: str | None = None

    @property
    def word(self) -> str:
        return {"capital_call": "Capital Call", "distribution": "Distribution", "net_event": "Net Capital Call"}[self.kind]

    @property
    def short_label(self) -> str:
        return f"{self.word} #{self.number}"

    @property
    def itd_label(self) -> str:
        return f"{self.short_label} - {self.date:%m.%d.%Y}"

    def header_for(self, comp: ComponentType) -> str:
        if comp.key == "mgmt_fee" and self.fee_period:
            return f"{self.fee_period} Mgmt Fees"
        return comp.header


def _investors(variant: LayoutVariant, defect: str | None) -> list[Investor]:
    main = [
        ("Northgate Family Trust", "12500000"),
        ("Crescent Ridge Investments LP", "10000000"),
        ("Harborview Capital Partners, LLC", "25000000"),
        ("Alder & Finch Holdings, LLC", "7750000"),
        ("Meridian Endowment Fund", "6300000"),
        ("Juniper Hollow Partners", "5000000"),
        ("Pemberton Street FLP", "3333333"),
        ("Oakmere Ventures, Ltd.", "2750000"),
        ("Silverline Retirement Plan", "2000000"),
        ("Tamsin Rourke", "1250000"),
        ("Wexford Lane Capital, LLC", "900000"),
    ]
    if defect == "probe_multi_plug_tied_largest":
        main[0] = ("Northgate Family Trust", "25000000")
    investors = [Investor(n, "Main Fund", Decimal(c)) for n, c in main]
    investors.append(Investor("Brightwater Affiliates Fund, LP", "Main Fund", Decimal("4000000"), affiliate=True))
    investors.append(Investor("Vireo Late Close Partners", "Main Fund", Decimal("1500000"), late_closer=True))
    investors.append(Investor("Example Growth Fund III GP, LLC", "Main Fund", Decimal("0"), is_gp=True))
    if variant.vehicles == 2:
        for n, c in [
            ("Kestrel Offshore Feeder, Ltd.", "9000000"),
            ("Lindqvist Pension Stiftung", "6500000"),
            ("Quarry Road Partners", "2250000"),
            ("Ottoline Varga", "750000"),
        ]:
            investors.append(Investor(n, "Parallel Fund", Decimal(c)))
        investors.append(Investor("EGF III Parallel GP, LLC", "Parallel Fund", Decimal("0"), is_gp=True))
    next_id = 20001
    for inv in investors:
        inv.fund_id = 901 if inv.vehicle == "Main Fund" else 902
        if not inv.is_gp:
            inv.investor_id = next_id
            next_id += 1
    return investors


def _events(event_type: str, defect: str | None) -> tuple[list[EventDef], EventDef]:
    prior = [
        EventDef("capital_call", 1, dt.date(2024, 1, 22),
                 {"investment": Decimal("20000000"), "expenses": Decimal("400000"), "mgmt_fee": Decimal("0")}, "Q1 2024"),
        EventDef("capital_call", 2, dt.date(2024, 6, 25),
                 {"investment": Decimal("10000000"), "mgmt_fee": Decimal("0")}, "Q2 2024"),
        EventDef("distribution", 1, dt.date(2025, 1, 17), {"roc": Decimal("-1500000")}),
        EventDef("capital_call", 3, dt.date(2026, 3, 9),
                 {"investment": Decimal("5000000"), "expenses": Decimal("250000"), "mgmt_fee": Decimal("0")}, "Q1 2026"),
    ]
    due = dt.date(2026, 6, 10)
    if event_type == "capital_call":
        current = EventDef("capital_call", 4, due,
                           {"investment": Decimal("8500000"), "expenses": Decimal("275000"), "mgmt_fee": Decimal("0")},
                           "Q3 2026")
    elif event_type == "distribution":
        current = EventDef("distribution", 2, due,
                           {"roc": Decimal("-3000000"), "gain": Decimal("-1200000"), "carry": Decimal("-400000")})
    else:
        current = EventDef("net_event", 4, due,
                           {"expenses": Decimal("150000"), "mgmt_fee": Decimal("0"), "roc": Decimal("-2000000")},
                           "Q3 2026")
    if defect == "itd_event_number_gap":
        current.number = 5
    return prior, current


def r2(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def r0(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _serial(value: dt.date) -> int:
    return (value - EXCEL_EPOCH).days


def _workday(start: dt.date, days: int, holidays: set[dt.date]) -> dt.date:
    step = 1 if days >= 0 else -1
    remaining = abs(days)
    current = start
    while remaining:
        current += dt.timedelta(days=step)
        if current.weekday() < 5 and current not in holidays:
            remaining -= 1
    return current


# ---------------------------------------------------------------------------
# Allocation math
# ---------------------------------------------------------------------------


@dataclass
class Allocation:
    """Per-investor amounts for one event's components, with the plug bookkeeping."""

    amounts: dict[str, dict[str, Decimal]]  # component key -> investor name -> amount
    offsets: dict[str, dict[str, Decimal]]  # component key -> investor name -> literal offset in formula
    unrounded: set[tuple[str, str]] = field(default_factory=set)  # (component, investor)


def _fee(inv: Investor, affiliate_override: bool | None = None, whole_dollars: bool = False) -> Decimal:
    affiliate = inv.affiliate if affiliate_override is None else affiliate_override
    if inv.is_gp or affiliate:
        return Decimal("0")
    raw = inv.commitment * MGMT_FEE_RATE * FEE_PERIOD_FRACTION
    return r0(raw) if whole_dollars else r2(raw)


def _vehicle_split(total: Decimal, investors: list[Investor], vehicles: list[str]) -> dict[str, Decimal]:
    """Split a fund-level driver across vehicles by commitment; the first vehicle takes the residual."""
    if len(vehicles) == 1:
        return {vehicles[0]: total}
    fund_commitment = sum(i.commitment for i in investors)
    split = {v: r2(total * sum(i.commitment for i in investors if i.vehicle == v) / fund_commitment) for v in vehicles}
    split[vehicles[0]] += total - sum(split.values())
    return split


def _plug_target(investors: list[Investor]) -> Investor:
    eligible = [i for i in investors if not i.is_gp and not i.affiliate]
    return max(eligible, key=lambda i: i.commitment)  # max() keeps the topmost row on ties


def _allocate_pro_rata(
    driver: Decimal,
    investors: list[Investor],
    weights: dict[str, Decimal],
    plug: Investor | None,
    unrounded: set[str] = frozenset(),
) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    total_weight = sum(weights.values())
    amounts: dict[str, Decimal] = {}
    offsets: dict[str, Decimal] = {}
    for inv in investors:
        share = weights.get(inv.name, Decimal("0")) / total_weight if total_weight else Decimal("0")
        raw = driver * share
        amounts[inv.name] = raw if inv.name in unrounded else r2(raw)
    residual = driver - sum(amounts.values())
    if plug is not None and residual:
        amounts[plug.name] += residual
        offsets[plug.name] = residual
    return amounts, offsets


# ---------------------------------------------------------------------------
# Sheet builder
# ---------------------------------------------------------------------------


@dataclass
class CellSpec:
    value: Any = None  # literal (number, str, date) or None
    formula: str | None = None  # without leading '='
    cached: Any = None  # cached value for formula cells
    fmt: str | None = None


class SheetBuilder:
    def __init__(self, name: str, role: str, row_offset: int = 0, col_offset: int = 0) -> None:
        self.name = name
        self.role = role
        self.row_offset = row_offset
        self.col_offset = col_offset
        self.cells: dict[str, CellSpec] = {}
        self.merges: list[str] = []
        self.hidden_rows: set[int] = set()
        self.hidden_cols: set[str] = set()
        self.view: str | None = None
        self.state = "visible"

    def row(self, r: int) -> int:
        return r + self.row_offset

    def col(self, c: int) -> str:
        return get_column_letter(c + self.col_offset)

    def coord(self, r: int, c: int) -> str:
        return f"{self.col(c)}{self.row(r)}"

    def put(self, r: int, c: int, value: Any = None, *, formula: str | None = None, cached: Any = None,
            fmt: str | None = None) -> str:
        coord = self.coord(r, c)
        self.put_at(coord, value, formula=formula, cached=cached, fmt=fmt)
        return coord

    def put_at(self, coord: str, value: Any = None, *, formula: str | None = None, cached: Any = None,
               fmt: str | None = None) -> str:
        self.cells[coord] = CellSpec(value=value, formula=formula, cached=cached, fmt=fmt)
        return coord

    def get(self, coord: str) -> CellSpec:
        return self.cells[coord]


def q(sheet: str) -> str:
    return sheet if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", sheet) else "'" + sheet.replace("'", "''") + "'"


def _num(value: Decimal | int | float) -> float | int:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


# ---------------------------------------------------------------------------
# Fixture assembly
# ---------------------------------------------------------------------------


class _Builder:
    def __init__(self, spec: FixtureSpec) -> None:
        self.spec = spec
        self.variant = VARIANTS[spec.variant]
        self.defect = spec.defect
        self.investors = _investors(self.variant, self.defect)
        self.vehicles = list(dict.fromkeys(i.vehicle for i in self.investors))
        self.prior_events, self.current = _events(spec.event_type, self.defect)
        self.holidays = {d for _, d in HOLIDAYS_2026}
        self.sheets: list[SheetBuilder] = []
        self.layouts: dict[str, dict[str, Any]] = {}
        self.truth: dict[str, Any] = {}
        self.facts: dict[str, dict[str, Any]] = {}
        self.names = {role: self.variant.sheet_name(role) for role in _DEFAULT_SHEET_NAMES}
        if self.variant.vehicles == 1:
            self.merge_names = {"Main Fund": "Example Growth Fund III" if self.variant.name == "shifted" else "Merge"}
        else:
            self.merge_names = {v: f"Merge - {v}" for v in self.vehicles}
        self.whole_dollar_fees = self.defect == "probe_whole_dollar_fees"

    # -- helpers -----------------------------------------------------------------

    def lps(self, vehicle: str | None = None) -> list[Investor]:
        return [i for i in self.investors if not i.is_gp and (vehicle is None or i.vehicle == vehicle)]

    def in_vehicle(self, vehicle: str) -> list[Investor]:
        return [i for i in self.investors if i.vehicle == vehicle]

    def by_name(self, name: str) -> Investor:
        return next(i for i in self.investors if i.name == name)

    def fee_for(self, inv: Investor) -> Decimal:
        override = False if (self.defect == "affiliate_charged_fee" and inv.affiliate) else None
        fee = _fee(inv, affiliate_override=override, whole_dollars=self.whole_dollar_fees)
        if self.defect == "fee_tab_value_wrong" and inv.name == "Meridian Endowment Fund":
            fee += Decimal("100")
        return fee

    # -- event allocations ----------------------------------------------------------

    def allocate_event(self, event: EventDef, contributed: dict[str, Decimal], *, current: bool) -> Allocation:
        """Allocate every component of ``event``.

        Late closers were admitted after the prior events, so they are excluded from
        (and carry $0 on) every prior event.
        """
        amounts: dict[str, dict[str, Decimal]] = {}
        offsets: dict[str, dict[str, Decimal]] = {}
        unrounded: set[tuple[str, str]] = set()

        def eligible(inv: Investor) -> bool:
            return current or not inv.late_closer

        for key, fund_driver in event.drivers.items():
            amounts[key] = {i.name: Decimal("0") for i in self.investors}
            offsets[key] = {}
            if key == "mgmt_fee":
                for inv in self.investors:
                    if eligible(inv):
                        amounts[key][inv.name] = self.fee_for(inv) if current else _fee(inv)
                continue
            split = _vehicle_split(fund_driver, self.investors, self.vehicles)
            for vehicle in self.vehicles:
                members = [i for i in self.in_vehicle(vehicle) if eligible(i)]
                driver = split[vehicle]
                plug = _plug_target(members)
                if key == "carry":
                    gp = next(i for i in members if i.is_gp)
                    rate = self.gp_carry_rate if current else CARRY_RATE
                    gp_share = r2(driver * rate)
                    lps = [i for i in members if not i.is_gp]
                    weights = {i.name: contributed.get(i.name, Decimal("0")) for i in lps}
                    lp_amounts, lp_offsets = _allocate_pro_rata(driver - gp_share, lps, weights, plug)
                    amounts[key].update(lp_amounts)
                    amounts[key][gp.name] = gp_share
                    offsets[key].update(lp_offsets)
                    continue
                if C_BY_KEY[key].side == "distribution":
                    weights = {i.name: contributed.get(i.name, Decimal("0")) for i in members}
                else:
                    weights = {i.name: i.commitment for i in members}
                main = vehicle == "Main Fund"
                if current and main and key == "investment" and self.defect == "commitment_pct_swap":
                    weights = self._swapped_pct_weights(members)
                if current and main and key == "investment" and self.defect == "plug_on_affiliate":
                    plug = next(i for i in members if i.affiliate)
                skip_round: set[str] = set()
                if current and main and key == "investment" and self.defect == "unrounded_amount":
                    skip_round = {"Oakmere Ventures, Ltd."}
                    unrounded.add((key, "Oakmere Ventures, Ltd."))
                spread = current and main and key in ("investment", "expenses") \
                    and self.defect == "probe_multi_plug_tied_largest"
                vehicle_amounts, vehicle_offsets = _allocate_pro_rata(
                    driver, members, weights, None if spread else plug, skip_round)
                if spread:
                    vehicle_offsets = _spread_residual(driver, vehicle_amounts, members)
                amounts[key].update(vehicle_amounts)
                offsets[key].update(vehicle_offsets)
            if current and key == "investment" and self.defect == "concentrated_plug":
                self._move(amounts, offsets, key, "Juniper Hollow Partners", "Silverline Retirement Plan", Decimal("500"))
            if current and key == "roc" and self.defect == "over_returned_capital":
                self._move(amounts, offsets, key, "Harborview Capital Partners, LLC", "Vireo Late Close Partners",
                           Decimal("-5000"))
        return Allocation(amounts, offsets, unrounded)

    @property
    def gp_carry_rate(self) -> Decimal:
        return Decimal("0.25") if self.defect == "carry_split_wrong" else CARRY_RATE

    def _swapped_pct_weights(self, members: list[Investor]) -> dict[str, Decimal]:
        total = sum(i.commitment for i in members)
        weights = {i.name: i.commitment / total for i in members}
        weights["Tamsin Rourke"] += Decimal("0.0003")
        weights["Wexford Lane Capital, LLC"] -= Decimal("0.0003")
        self.pct_overrides = {n: weights[n] for n in ("Tamsin Rourke", "Wexford Lane Capital, LLC")}
        return weights

    @staticmethod
    def _move(amounts, offsets, key, from_name, to_name, amount: Decimal) -> None:
        amounts[key][from_name] -= amount
        amounts[key][to_name] += amount
        offsets[key][from_name] = offsets[key].get(from_name, Decimal("0")) - amount
        offsets[key][to_name] = offsets[key].get(to_name, Decimal("0")) + amount

    # -- build ------------------------------------------------------------------------

    def build(self, out_dir: Path) -> FixtureManifest:
        self.pct_overrides: dict[str, Decimal] = {}
        # Prior events: contributions accumulate event by event.
        contributed: dict[str, Decimal] = {i.name: Decimal("0") for i in self.investors}
        distributed: dict[str, Decimal] = {i.name: Decimal("0") for i in self.investors}
        self.prior_allocations: list[Allocation] = []
        for event in self.prior_events:
            members_before = {n: v for n, v in contributed.items()}
            alloc = self.allocate_event(event, members_before, current=False)
            if self.defect == "rf_over_contributed" and event.number == 1 and event.kind == "capital_call":
                alloc.amounts["investment"]["Tamsin Rourke"] += Decimal("800000")
            self.prior_allocations.append(alloc)
            for key, per_inv in alloc.amounts.items():
                for name, amount in per_inv.items():
                    if C_BY_KEY[key].side == "call":
                        contributed[name] += amount
                    else:
                        distributed[name] += amount
        self.prior_contributed = contributed
        self.prior_distributed = distributed
        self.current_alloc = self.allocate_event(self.current, contributed, current=True)

        self._build_holidays()
        self._build_allocation_plan()
        self._build_itd()
        self._build_allocation()
        self._build_summary()
        self._build_merge_tabs()
        self._build_investor_data()
        self._build_fee_tab()
        self._build_other_sheets()
        self._apply_cell_defects()

        order = [self.names["holiday_calendar"], self.names["summary"], self.names["allocation"], self.names["itd"],
                 *self.merge_names.values(), self.names["investor_data"], "Notes", "Portfolio Investment Tracker",
                 self.names["mgmt_fee"], "3rd Close Rebalance"]
        by_name = {s.name: s for s in self.sheets}
        ordered = [by_name[n] for n in order]

        file_name = self._file_name()
        target_dir = out_dir / self.spec.fixture_id
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / file_name
        _write_workbook(ordered, path)

        sheet_roles = {s.name: s.role for s in ordered}
        relevant = [s.name for s in ordered if s.role in RELEVANT_ROLES[self.spec.event_type]]
        verdicts = clean_verdicts(self.spec.event_type)
        facts = _clean_facts(self.spec.event_type)
        if self.defect:
            verdicts.update(DEFECTS[self.defect].verdicts)
            for rule_id, extra in DEFECTS[self.defect].facts.items():
                facts.setdefault(rule_id, {}).update(extra)
        return FixtureManifest(
            spec=self.spec,
            path=path,
            sheet_roles=sheet_roles,
            relevant_sheets=relevant,
            layouts={name: layout for name, layout in self.layouts.items()},
            truth=_jsonable(self.truth),
            expected_verdicts=verdicts,
            facts=facts,
        )

    def _file_name(self) -> str:
        if self.defect == "generic_file_name":
            return "Book1.xlsx"
        slug = re.sub(r"[^a-z0-9]+", "-", self.current.short_label.lower()).strip("-")
        return f"{FUND_SHORT}_{self.current.number:03d}_{slug}_allocation_summary.xlsx"

    # -- Bank holidays --------------------------------------------------------------------

    def _build_holidays(self) -> None:
        sheet = SheetBuilder(self.names["holiday_calendar"], "holiday_calendar")
        sheet.put(1, 1, "Holiday")
        sheet.put(1, 2, "Date")
        for idx, (label, day) in enumerate(HOLIDAYS_2026, start=2):
            sheet.put(idx, 1, label)
            sheet.put(idx, 2, day, fmt=DATE_SHORT_FMT)
        self.holiday_range = f"{q(sheet.name)}!$B$2:$B${len(HOLIDAYS_2026) + 1}"
        self.sheets.append(sheet)
        self.layouts[sheet.name] = {
            "role": "holiday_calendar",
            "sheet": sheet.name,
            "header_row": 1,
            "date_column": "B",
            "first_row": 2,
            "last_row": len(HOLIDAYS_2026) + 1,
        }

    # -- Allocation geometry (planned first: the ITD sheet references it) ---------------

    def _build_allocation_plan(self) -> None:
        v = self.variant
        sheet = SheetBuilder(self.names["allocation"], "allocation", v.row_offset, v.col_offset)
        self.alloc_sheet = sheet
        event = self.current
        has_dist_basis = event.kind in ("distribution", "net_event")
        cols: dict[str, int] = {}
        c = 2
        for key in ("close", "investor", "affiliate", "commitment", "commitment_pct"):
            cols[key] = c
            c += 1
        if has_dist_basis:
            cols["contributed"] = c
            cols["contributed_pct"] = c + 1
            c += 2
        c += 1
        self.call_comp_cols: dict[str, int] = {}
        for comp in CALL_COMPONENTS:
            self.call_comp_cols[comp.key] = c
            c += 1
        cols["call_total"] = c
        c += 1
        cols["stale"] = c  # spare column right of the call total (used by the stale_component defect)
        c += 2
        self.dist_comp_cols: dict[str, int] = {}
        for comp in DIST_COMPONENTS:
            self.dist_comp_cols[comp.key] = c
            c += 1
        cols["dist_total"] = c
        c += 2
        cols["late_interest"] = c
        c += 1
        cols["cash_due"] = c
        c += 2
        for key in ("rf_commitment", "rf_prior", "rf_prior_recallable", "rf_current_call", "rf_current_recallable",
                    "rf_remaining"):
            cols[key] = c
            c += 1
        c += 1
        for key in ("prior_gross_dist", "current_dist", "total_gross_dist"):
            cols[key] = c
            c += 1
        c += 1
        cols["received"] = c
        cols["received_date"] = c + 1
        cols["error_cell"] = c + 3
        self.acols = cols
        self.comp_col = {**self.call_comp_cols, **self.dist_comp_cols}

        # Row plan
        rows: dict[str, Any] = {"fund_name": 2, "notice": 3, "due": 4, "driver": 5, "header": 6}
        r = 7
        blocks = []
        for vehicle in self.vehicles:
            block: dict[str, Any] = {"name": vehicle}
            if len(self.vehicles) > 1:
                block["title_row"] = r
                block["driver_row"] = r + 1
                r += 2
            else:
                block["driver_row"] = rows["driver"]
            block["investor_rows"] = {}
            for inv in self.in_vehicle(vehicle):
                if inv.is_gp:
                    continue
                block["investor_rows"][inv.name] = r
                r += 1
            r += 1
            block["lp_subtotal"] = r
            r += 2
            gp = next(i for i in self.in_vehicle(vehicle) if i.is_gp)
            block["gp_row"] = r
            block["gp_name"] = gp.name
            r += 1
            block["gp_subtotal"] = r
            r += 2
            block["total_row"] = r
            r += 2 if len(self.vehicles) > 1 else 1
            blocks.append(block)
        if len(self.vehicles) > 1:
            rows["grand_total"] = r
            r += 1
        else:
            rows["grand_total"] = blocks[0]["total_row"]
        rows["check"] = r
        self.arows = rows
        self.ablocks = blocks
        self.investor_row = {n: sheet.row(rr) for b in blocks for n, rr in b["investor_rows"].items()}
        for b in blocks:
            self.investor_row[b["gp_name"]] = sheet.row(b["gp_row"])

    # -- ITD ----------------------------------------------------------------------------

    def _itd_blocks(self) -> list[tuple[EventDef, Allocation, bool]]:
        return [(e, a, False) for e, a in zip(self.prior_events, self.prior_allocations)] + \
            [(self.current, self.current_alloc, True)]

    def _build_itd(self) -> None:
        v = self.variant
        sheet = SheetBuilder(self.names["itd"], "itd", v.row_offset, v.col_offset)
        self.itd_sheet = sheet
        alloc = self.alloc_sheet
        band = ["investment_contributions", "cost_contributions", "recallable_distributions",
                "non_recallable_distributions", "tax_withholding"]
        band_labels = ["Investment Contributions", "Cost Contributions", "Recallable Distributions",
                       "Non-Recallable Distributions", "Tax Withholding"]
        band_rows = {cat: i + 1 for i, cat in enumerate(band)}
        overlay_rows: dict[str, int] = {}
        for cat, label in zip(band, band_labels):
            sheet.put(band_rows[cat], 1, label)
        if self.defect == "probe_itd_overlay_rows":
            overlay_rows = {"late_interest": 6, "mgmt_fees": 7}
            sheet.put(6, 1, "Late Interest")
            sheet.put(7, 1, "Mgmt Fees")
        fund_row, event_row, sub_row = 9, 10, 11
        sheet.put(fund_row, 1, formula=f"{q(alloc.name)}!{alloc.coord(self.arows['fund_name'], 2)}", cached=FUND_NAME)

        cum_keys = ["commitment", "investment_contributions", "cost_contributions", "total_contributions",
                    "unfunded", "recallable_distributions", "non_recallable_distributions", "total_distributions"]
        cum_headers = ["Commitment", "Investment Contributions", "Cost Contributions",
                       "Total Paid-In Capital" if v.alt_headers else "Total Contributions", "Unfunded",
                       "Recallable Distributions", "Non-Recallable Distributions", "Total Distributions"]
        cum_cols = {k: i + 2 for i, k in enumerate(cum_keys)}
        sheet.put(sub_row, 1, "Partner" if v.alt_headers else "Investor")
        for k, h in zip(cum_keys, cum_headers):
            sheet.put(sub_row, cum_cols[k], h)

        # Column plan for event blocks.
        c = cum_cols["total_distributions"] + 2
        first_block_col = c
        blocks_plan = []
        itd_blocks = self._itd_blocks()
        if self.defect == "probe_transfer_block":
            itd_blocks.insert(2, (EventDef("transfer", 0, dt.date(2024, 9, 30), {"investment": Decimal("0")}),
                                  Allocation({"investment": {i.name: Decimal("0") for i in self.investors}}, {}),
                                  False))
        for event, allocation, is_current in itd_blocks:
            comps = [C_BY_KEY[k] for k in event.drivers]
            plan = {"event": event, "alloc": allocation, "current": is_current, "cols": {}}
            for comp in comps:
                plan["cols"][comp.key] = c
                c += 1
            plan["total_col"] = c
            c += 2
            blocks_plan.append(plan)
        last_col = c
        band_range_first = sheet.col(first_block_col)
        band_range_last = sheet.col(last_col)

        # Row plan: investors per vehicle.
        r = sub_row + 1
        vblocks = []
        for vehicle in self.vehicles:
            vb: dict[str, Any] = {"name": vehicle, "investor_rows": {}}
            if len(self.vehicles) > 1:
                vb["title_row"] = r
                sheet.put(r, 1, vehicle)
                r += 1
            for inv in self.in_vehicle(vehicle):
                if inv.is_gp:
                    continue
                vb["investor_rows"][inv.name] = r
                r += 1
            r += 1
            vb["lp_subtotal"] = r
            r += 2
            gp = next(i for i in self.in_vehicle(vehicle) if i.is_gp)
            vb["gp_row"] = r
            vb["gp_name"] = gp.name
            r += 1
            vb["gp_subtotal"] = r
            r += 2
            vb["total_row"] = r
            r += 2
            vblocks.append(vb)
        check_row = r

        # Values per investor.
        values: dict[str, dict[str, Decimal]] = {}  # investor -> "block_index:comp" -> amount
        for inv in self.investors:
            values[inv.name] = {}
            for bi, plan in enumerate(blocks_plan):
                for key in plan["cols"]:
                    amount = plan["alloc"].amounts.get(key, {}).get(inv.name, Decimal("0"))
                    if plan["current"] and self.defect == "itd_current_value_mismatch" \
                            and key == self._primary_current_key() and inv.name == "Crescent Ridge Investments LP":
                        amount += Decimal("10")
                    values[inv.name][f"{bi}:{key}"] = amount

        # Classification marks.
        marks: dict[int, str] = {}  # column index -> category
        for bi, plan in enumerate(blocks_plan):
            if plan["event"].kind == "transfer":
                continue
            for key, col in plan["cols"].items():
                marks[col] = C_BY_KEY[key].classification
        current_plan = blocks_plan[-1]
        if self.defect == "itd_missing_x":
            dropped = current_plan["cols"][self._primary_current_key()]
            marks.pop(dropped)
        for col, cat in marks.items():
            sheet.put(band_rows[cat], col, "X")
        overlay_marks: dict[int, str] = {}
        if overlay_rows:
            for plan in blocks_plan:
                for key, col in plan["cols"].items():
                    if key == "mgmt_fee":
                        sheet.put(overlay_rows["mgmt_fees"], col, "X")
                        overlay_marks[col] = "mgmt_fees"

        # Event headers + sub-headers (+ merged label ranges).
        for bi, plan in enumerate(blocks_plan):
            event = plan["event"]
            first = min(plan["cols"].values())
            label = "3Q24 Transfers" if event.kind == "transfer" else event.itd_label
            sheet.put(event_row, first, label)
            sheet.merges.append(f"{sheet.coord(event_row, first)}:{sheet.coord(event_row, plan['total_col'])}")
            for key, col in plan["cols"].items():
                sheet.put(sub_row, col, event.header_for(C_BY_KEY[key]) if event.kind != "transfer" else "Transfer")
            sheet.put(sub_row, plan["total_col"], "Total")

        # Investor rows.
        cum_values: dict[str, dict[str, Decimal]] = {}
        for vb in vblocks:
            rows_in_block = list(vb["investor_rows"].items()) + [(vb["gp_name"], vb["gp_row"])]
            for name, rr in rows_in_block:
                inv = self.by_name(name)
                arow = self.investor_row[name]
                a_inv_col = alloc.col(self.acols["investor"])
                sheet.put(rr, 1, formula=f"{q(alloc.name)}!{a_inv_col}{arow}", cached=name)
                inv_ref = f"${sheet.col(1)}{sheet.row(rr)}"
                a_commit = alloc.col(self.acols["commitment"])
                sheet.put(rr, cum_cols["commitment"], formula=(
                    f"SUMIFS({q(alloc.name)}!${a_commit}:${a_commit},{q(alloc.name)}!${a_inv_col}:${a_inv_col},{inv_ref})"),
                    cached=_num(inv.commitment), fmt=MONEY_FMT)
                for bi, plan in enumerate(blocks_plan):
                    for key, col in plan["cols"].items():
                        amount = values[name][f"{bi}:{key}"]
                        if plan["current"]:
                            a_col = alloc.col(self.comp_col[key])
                            formula = (f"SUMIFS({q(alloc.name)}!{a_col}:{a_col},{q(alloc.name)}!${a_inv_col}:${a_inv_col},"
                                       f"{inv_ref})")
                            if self.defect == "itd_current_value_mismatch" and key == self._primary_current_key() \
                                    and name == "Crescent Ridge Investments LP":
                                sheet.put(rr, col, _num(amount), fmt=MONEY_FMT)
                            else:
                                sheet.put(rr, col, formula=formula, cached=_num(amount), fmt=MONEY_FMT)
                        else:
                            sheet.put(rr, col, _num(amount), fmt=MONEY_FMT)
                    block_cols = sorted(plan["cols"].values())
                    total = sum(values[name][f"{bi}:{k}"] for k in plan["cols"])
                    sheet.put(rr, plan["total_col"], formula=(
                        f"SUM({sheet.coord(rr, block_cols[0])}:{sheet.coord(rr, block_cols[-1])})"),
                        cached=_num(total), fmt=MONEY_FMT)
                # Cumulative columns.
                cum = {cat: Decimal("0") for cat in band}
                for bi, plan in enumerate(blocks_plan):
                    for key, col in plan["cols"].items():
                        cat = marks.get(col)
                        if cat:
                            cum[cat] += values[name][f"{bi}:{key}"]
                row_ref = sheet.row(rr)

                def sumif(cat: str) -> str:
                    band_row = sheet.row(band_rows[cat])
                    return (f"SUMIF(${band_range_first}${band_row}:${band_range_last}${band_row},\"X\","
                            f"${band_range_first}{row_ref}:${band_range_last}{row_ref})")

                total_contrib = cum["investment_contributions"] + cum["cost_contributions"]
                unfunded = inv.commitment - total_contrib - cum["recallable_distributions"]
                total_dist = cum["recallable_distributions"] + cum["non_recallable_distributions"]
                cells = {
                    "investment_contributions": (sumif("investment_contributions"), cum["investment_contributions"]),
                    "cost_contributions": (sumif("cost_contributions"), cum["cost_contributions"]),
                    "total_contributions": (
                        f"SUM({sheet.coord(rr, cum_cols['investment_contributions'])}:"
                        f"{sheet.coord(rr, cum_cols['cost_contributions'])})", total_contrib),
                    "unfunded": (
                        f"{sheet.coord(rr, cum_cols['commitment'])}-{sheet.coord(rr, cum_cols['total_contributions'])}"
                        f"-{sheet.coord(rr, cum_cols['recallable_distributions'])}", unfunded),
                    "recallable_distributions": (sumif("recallable_distributions"), cum["recallable_distributions"]),
                    "non_recallable_distributions": (sumif("non_recallable_distributions"),
                                                     cum["non_recallable_distributions"]),
                    "total_distributions": (
                        f"SUM({sheet.coord(rr, cum_cols['recallable_distributions'])}:"
                        f"{sheet.coord(rr, cum_cols['non_recallable_distributions'])})", total_dist),
                }
                for key, (formula, cached) in cells.items():
                    sheet.put(rr, cum_cols[key], formula=formula, cached=_num(cached), fmt=MONEY_FMT)
                cum_values[name] = {
                    "commitment": inv.commitment,
                    "total_contributions": total_contrib,
                    "unfunded": unfunded,
                    "total_distributions": total_dist,
                    "recallable_distributions": cum["recallable_distributions"],
                    "non_recallable_distributions": cum["non_recallable_distributions"],
                }
            # Subtotals.
            lp_rows = list(vb["investor_rows"].values())
            numeric_cols = sorted(set(cum_cols.values()) - {1}) + sorted(
                col for plan in blocks_plan for col in [*plan["cols"].values(), plan["total_col"]])
            for col in numeric_cols:
                lp_sum = sum(_cached_decimal(sheet, sheet.coord(rr, col)) for rr in lp_rows)
                sheet.put(vb["lp_subtotal"], col, formula=(
                    f"SUM({sheet.coord(lp_rows[0], col)}:{sheet.coord(lp_rows[-1], col)})"),
                    cached=_num(lp_sum), fmt=MONEY_FMT)
                gp_val = _cached_decimal(sheet, sheet.coord(vb["gp_row"], col))
                sheet.put(vb["gp_subtotal"], col, formula=f"SUM({sheet.coord(vb['gp_row'], col)})",
                          cached=_num(gp_val), fmt=MONEY_FMT)
                sheet.put(vb["total_row"], col, formula=(
                    f"{sheet.coord(vb['lp_subtotal'], col)}+{sheet.coord(vb['gp_subtotal'], col)}"),
                    cached=_num(lp_sum + gp_val), fmt=MONEY_FMT)
            sheet.put(vb["lp_subtotal"], 1, "Limited Partners")
            sheet.put(vb["gp_subtotal"], 1, "General Partner")
            sheet.put(vb["total_row"], 1, f"Total {vehicle}" if len(self.vehicles) > 1 else "Total Partnership")

        # Prior-block live links on the $0 GP row (calibration probe) / on an LP (defect).
        if self.defect in ("live_prior_itd_link", "probe_gp_zero_live_link"):
            plan = blocks_plan[0]
            key = "investment"
            col = plan["cols"][key]
            target = "Harborview Capital Partners, LLC" if self.defect == "live_prior_itd_link" else vblocks[0]["gp_name"]
            rr = vblocks[0]["gp_row"] if target == vblocks[0]["gp_name"] else vblocks[0]["investor_rows"][target]
            coord = sheet.coord(rr, col)
            a_col = alloc.col(self.comp_col[key])
            a_inv_col = alloc.col(self.acols["investor"])
            cell = sheet.get(coord)
            sheet.put_at(coord, formula=(f"SUMIFS({q(alloc.name)}!{a_col}:{a_col},{q(alloc.name)}!${a_inv_col}:"
                                         f"${a_inv_col},${sheet.col(1)}{sheet.row(rr)})"),
                         cached=cell.value, fmt=MONEY_FMT)

        # Check row: current block totals vs Allocation grand totals.
        sheet.put(check_row, 1, "Check vs Allocation")
        grand_itd_rows = [vb["total_row"] for vb in vblocks]
        for key, col in current_plan["cols"].items():
            a_col = alloc.col(self.comp_col[key])
            itd_total = sum(_cached_decimal(sheet, sheet.coord(rr, col)) for rr in grand_itd_rows)
            alloc_total = sum(self.current_alloc.amounts[key].values())
            sheet.put(check_row, col, formula=(
                "+".join(sheet.coord(rr, col) for rr in grand_itd_rows) +
                f"-{q(alloc.name)}!{a_col}{alloc.row(self.arows['grand_total'])}"),
                cached=_num(itd_total - alloc_total), fmt=MONEY_FMT)

        self.itd_cum_cols = cum_cols
        self.itd_cum = cum_values
        self.itd_current_total_col = current_plan["total_col"]
        self.itd_values = values
        self.itd_blocks_plan = blocks_plan
        self.sheets.append(sheet)
        sheet.view = "pageBreakPreview"

        # Golden layout.
        layout_blocks = []
        for bi, plan in enumerate(blocks_plan):
            event = plan["event"]
            block = {
                "label": "3Q24 Transfers" if event.kind == "transfer" else event.itd_label,
                "event_type": "transfer" if event.kind == "transfer" else event.kind,
                "number": None if event.kind == "transfer" else event.number,
                "date": None if event.kind == "transfer" else event.date.isoformat(),
                "first_column": sheet.col(min(plan["cols"].values())),
                "last_column": sheet.col(max(plan["cols"].values())),
                "total_column": sheet.col(plan["total_col"]),
                "is_current": plan["current"],
                "components": [
                    {"column": sheet.col(col), "component_type": C_BY_KEY[key].component_type,
                     "side": C_BY_KEY[key].side}
                    for key, col in plan["cols"].items()
                ],
            }
            layout_blocks.append(block)
        self.layouts[sheet.name] = {
            "role": "itd",
            "sheet": sheet.name,
            "classification_rows": {cat: sheet.row(r) for cat, r in band_rows.items()},
            "overlay_rows": {cat: sheet.row(r) for cat, r in overlay_rows.items()},
            "event_header_row": sheet.row(event_row),
            "subheader_row": sheet.row(sub_row),
            "investor_column": sheet.col(1),
            "cumulative_columns": {k: sheet.col(c) for k, c in cum_cols.items()},
            "event_blocks": layout_blocks,
            "vehicles": [
                {
                    "name": vb["name"],
                    "title_row": sheet.row(vb["title_row"]) if "title_row" in vb else None,
                    "investor_rows": [sheet.row(min(vb["investor_rows"].values())),
                                      sheet.row(max(vb["investor_rows"].values()))],
                    "gp_rows": [sheet.row(vb["gp_row"])],
                    "subtotal_rows": {"limited_partners": sheet.row(vb["lp_subtotal"]),
                                      "general_partner": sheet.row(vb["gp_subtotal"]),
                                      "total": sheet.row(vb["total_row"])},
                }
                for vb in vblocks
            ],
            "check_rows": [sheet.row(check_row)],
        }
        self.truth["itd"] = {
            "current_block_label": current_plan["event"].itd_label,
            "event_labels": [b["label"] for b in layout_blocks],
            "cumulative": {n: {k: str(v) for k, v in d.items()} for n, d in cum_values.items()},
            "current_block": {
                n: {sheet.col(col): str(values[n][f"{len(blocks_plan) - 1}:{key}"])
                    for key, col in current_plan["cols"].items()}
                for n in values
            },
        }
        self.facts_itd_unclassified = 1 if self.defect == "itd_missing_x" else 0

    def _primary_current_key(self) -> str:
        return next(iter(self.current.drivers))

    # -- Allocation -----------------------------------------------------------------------

    def _build_allocation(self) -> None:
        sheet = self.alloc_sheet
        v = self.variant
        cols, rows = self.acols, self.arows
        event = self.current
        alloc = self.current_alloc
        active = {k for k in event.drivers}
        itd = self.itd_sheet
        holidays = self.holidays

        # Header area.
        sheet.put(rows["fund_name"], 2, FUND_NAME)
        sheet.merges.append(f"{sheet.coord(rows['fund_name'], 2)}:{sheet.coord(rows['fund_name'], 3)}")
        word = "Distribution" if event.kind == "distribution" else "Capital Call"
        notice_fmt = "General" if self.defect == "fmt_serial_date" else DATE_LONG_FMT
        sheet.put(rows["notice"], 2, f"{word} - Notice Date")
        notice_coord = sheet.put(rows["notice"], 3, NOTICE_DATE, fmt=notice_fmt)
        due_label = "Payment Date" if event.kind == "distribution" else "Due Date"
        sheet.put(rows["due"], 2, f"{word} - {due_label}")
        if self.defect == "weekend_due_date":
            due_date = dt.date(2026, 6, 13)
            due_coord = sheet.put(rows["due"], 3, due_date, fmt=DATE_LONG_FMT)
        else:
            days = -3 if self.defect == "due_before_notice" else 10
            due_date = _workday(NOTICE_DATE, days, holidays)
            due_coord = sheet.put(rows["due"], 3, formula=f"WORKDAY({notice_coord},{days},{self.holiday_range})",
                                  cached=_serial(due_date), fmt=DATE_LONG_FMT)
        self.due_date = due_date
        self.notice_coord, self.due_coord = notice_coord, due_coord
        if event.kind in ("distribution", "net_event"):
            sheet.put(rows["due"], cols["commitment"], "Carried Interest Rate")
            self.carry_rate_coord = sheet.put(rows["due"], cols["commitment_pct"], _num(CARRY_RATE), fmt="0.00%")
        # Decorative banners above the header row.
        call_cols = sorted(self.call_comp_cols.values())
        dist_cols = sorted(self.dist_comp_cols.values())
        sheet.put(rows["notice"], call_cols[0], "Capital Call")
        sheet.merges.append(f"{sheet.coord(rows['notice'], call_cols[0])}:{sheet.coord(rows['notice'], cols['call_total'])}")
        sheet.put(rows["notice"], dist_cols[0], "Distribution")
        sheet.merges.append(f"{sheet.coord(rows['notice'], dist_cols[0])}:{sheet.coord(rows['notice'], cols['dist_total'])}")

        # Headers.
        header = rows["header"]
        alt = v.alt_headers
        labels = {
            "close": "Close #",
            "investor": "Partner Name" if alt else "Investor",
            "affiliate": "Affiliate",
            "commitment": "Commitment" if alt else "Total Commitment",
            "commitment_pct": "% of Fund" if alt else "Commitment %",
            "contributed": "Contributed Capital",
            "contributed_pct": "Contributed Capital %",
            "late_interest": "Late Interest",
            "cash_due": "Cash Due",
            "rf_commitment": "Commitment Amount",
            "rf_prior": "Prior Capital Contributions",
            "rf_prior_recallable": "Prior Recallable Distributions",
            "rf_current_call": "Current Capital Call",
            "rf_current_recallable": "Current Recallable Distributions",
            "rf_remaining": "Partners' Remaining Commitment",
            "prior_gross_dist": "Prior Gross Distributions",
            "current_dist": "Current Distributions",
            "total_gross_dist": "Total Gross Distributions",
            "received": "Received",
            "received_date": "Date",
        }
        for key, label in labels.items():
            if key in cols:
                sheet.put(header, cols[key], label)
        current_label = event.short_label
        comp_headers: dict[str, str] = {}
        for comp in (*CALL_COMPONENTS, *DIST_COMPONENTS):
            text = event.header_for(comp)
            if comp.key not in active:
                text = f"{comp.header} (not used {current_label})"
            comp_headers[comp.key] = text
            sheet.put(header, self.comp_col[comp.key], text)
        self.comp_headers = comp_headers
        call_side_active = any(C_BY_KEY[k].side == "call" for k in active)
        dist_side_active = any(C_BY_KEY[k].side == "distribution" for k in active)
        call_total_label = current_label if call_side_active else f"Capital Call (not used {current_label})"
        dist_total_label = current_label if (dist_side_active and not call_side_active) else (
            "Distribution Total" if dist_side_active else f"Distribution (not used {current_label})")
        self.call_total_header = sheet.put(header, cols["call_total"], call_total_label)
        self.dist_total_header = sheet.put(header, cols["dist_total"], dist_total_label)
        self.event_label_coord = self.call_total_header if call_side_active else self.dist_total_header
        if self.defect == "stale_component":
            sheet.put(header, cols["stale"], "Placement Fees (Capital Call #3)")
        if self.defect == "merged_component_header":
            sheet.merges.append(f"{sheet.coord(header, self.call_comp_cols['investment'])}:"
                                f"{sheet.coord(header, self.call_comp_cols['expenses'])}")
            del sheet.cells[sheet.coord(header, self.call_comp_cols["expenses"])]
        sheet.hidden_cols.add(sheet.col(cols["late_interest"]))

        fee_sheet = self.names["mgmt_fee"]
        fee_amount_col = "F"
        fee_name_col = "B"
        fee_vehicle_col = "C"

        # Fund-level driver row.
        fund_driver_row = rows["driver"]
        drivers: dict[str, Decimal] = {}
        for comp in (*CALL_COMPONENTS, *DIST_COMPONENTS):
            if comp.key in active:
                if comp.key == "mgmt_fee":
                    drivers[comp.key] = sum(alloc.amounts["mgmt_fee"].values())
                else:
                    drivers[comp.key] = event.drivers[comp.key]
            else:
                drivers[comp.key] = Decimal("0")
        for comp_key, amount in drivers.items():
            col = self.comp_col[comp_key]
            if comp_key == "mgmt_fee" and comp_key in active:
                sheet.put(fund_driver_row, col, formula=f"{q(fee_sheet)}!${fee_amount_col}${self._fee_total_row()}",
                          cached=_num(amount), fmt=MONEY_FMT)
            else:
                sheet.put(fund_driver_row, col, _num(amount), fmt=MONEY_FMT)
        call_driver_total = sum(drivers[c.key] for c in CALL_COMPONENTS)
        dist_driver_total = sum(drivers[c.key] for c in DIST_COMPONENTS)
        sheet.put(fund_driver_row, cols["call_total"], formula=(
            f"SUM({sheet.coord(fund_driver_row, call_cols[0])}:{sheet.coord(fund_driver_row, call_cols[-1])})"),
            cached=_num(call_driver_total), fmt=MONEY_FMT)
        sheet.put(fund_driver_row, cols["dist_total"], formula=(
            f"SUM({sheet.coord(fund_driver_row, dist_cols[0])}:{sheet.coord(fund_driver_row, dist_cols[-1])})"),
            cached=_num(dist_driver_total), fmt=MONEY_FMT)
        sheet.put(fund_driver_row, cols["late_interest"], 0, fmt=MONEY_FMT)
        sheet.put(fund_driver_row, cols["cash_due"], formula=(
            f"SUM({sheet.coord(fund_driver_row, cols['call_total'])},{sheet.coord(fund_driver_row, cols['dist_total'])},"
            f"{sheet.coord(fund_driver_row, cols['late_interest'])})"),
            cached=_num(call_driver_total + dist_driver_total), fmt=MONEY_FMT)
        if self.defect == "stale_component":
            sheet.put(fund_driver_row, cols["stale"], 0, fmt=MONEY_FMT)

        # Vehicle blocks.
        itd_inv = f"{q(itd.name)}!${itd.col(1)}:${itd.col(1)}"
        itd_total_contrib = itd.col(self.itd_cum_cols["total_contributions"])
        itd_total_dist = itd.col(self.itd_cum_cols["total_distributions"])
        truth_investors: dict[str, dict[str, Any]] = {}
        vehicle_layouts = []
        grand: dict[int, Decimal] = {}
        for block in self.ablocks:
            vehicle = block["name"]
            members = self.in_vehicle(vehicle)
            lps = [i for i in members if not i.is_gp]
            vdriver_row = block["driver_row"]
            vehicle_commitment = sum(i.commitment for i in members)
            if len(self.vehicles) > 1:
                sheet.put(block["title_row"], cols["investor"], vehicle)
                sheet.put(vdriver_row, cols["investor"], f"{vehicle} Allocation")
                for comp in (*CALL_COMPONENTS, *DIST_COMPONENTS):
                    col = self.comp_col[comp.key]
                    amount = sum(alloc.amounts.get(comp.key, {}).get(i.name, Decimal("0")) for i in members) \
                        if comp.key in active else Decimal("0")
                    if comp.key == "mgmt_fee" and comp.key in active:
                        sheet.put(vdriver_row, col, formula=(
                            f"SUMIFS({q(fee_sheet)}!${fee_amount_col}:${fee_amount_col},{q(fee_sheet)}!"
                            f"${fee_vehicle_col}:${fee_vehicle_col},\"{vehicle}\")"), cached=_num(amount), fmt=MONEY_FMT)
                    else:
                        sheet.put(vdriver_row, col, _num(amount), fmt=MONEY_FMT)
            total_row_commit = sheet.coord(block["total_row"], cols["commitment"])
            contributed_total = sum(self.prior_contributed[i.name] for i in members)
            rows_for_block = [(i, block["investor_rows"][i.name]) for i in lps] + \
                             [(next(i for i in members if i.is_gp), block["gp_row"])]
            col_sums_lp: dict[int, Decimal] = {}
            col_sums_gp: dict[int, Decimal] = {}
            for inv, rr in rows_for_block:
                row_vals: dict[int, Decimal] = {}

                def put(col_key_or_idx, value=None, formula=None, cached=None, fmt=MONEY_FMT):
                    col = cols[col_key_or_idx] if isinstance(col_key_or_idx, str) else col_key_or_idx
                    sheet.put(rr, col, value, formula=formula, cached=cached, fmt=fmt)
                    num = value if formula is None else cached
                    if isinstance(num, (int, float, Decimal)) and not isinstance(num, bool):
                        row_vals[col] = Decimal(str(num))

                put("close", 1 if not inv.late_closer else 5, fmt="General")
                sheet.put(rr, cols["investor"], inv.name)
                sheet.put(rr, cols["affiliate"], "GP" if inv.is_gp else ("Y" if inv.affiliate else "N"))
                put("commitment", _num(inv.commitment))
                pct = (inv.commitment / vehicle_commitment) if vehicle_commitment else Decimal("0")
                if inv.name in self.pct_overrides:
                    put("commitment_pct", float(self.pct_overrides[inv.name]), fmt=PCT_FMT)
                else:
                    put("commitment_pct", formula=f"{sheet.coord(rr, cols['commitment'])}/{_abs_row(total_row_commit)}",
                        cached=float(pct), fmt=PCT_FMT)
                pct_ref = f"${sheet.col(cols['commitment_pct'])}{sheet.row(rr)}"
                if "contributed" in cols:
                    contributed = self.prior_contributed[inv.name]
                    put("contributed", formula=f"-{sheet.coord(rr, cols['rf_prior'])}", cached=_num(contributed))
                    cpct = contributed / contributed_total if contributed_total else Decimal("0")
                    put("contributed_pct", formula=(
                        f"{sheet.coord(rr, cols['contributed'])}/{_abs_row(sheet.coord(block['total_row'], cols['contributed']))}"),
                        cached=float(cpct), fmt=PCT_FMT)
                    cpct_ref = f"${sheet.col(cols['contributed_pct'])}{sheet.row(rr)}"
                row_amounts: dict[str, Decimal] = {}
                for comp in (*CALL_COMPONENTS, *DIST_COMPONENTS):
                    col = self.comp_col[comp.key]
                    driver_ref = f"{sheet.col(col)}${sheet.row(vdriver_row)}"
                    amount = alloc.amounts.get(comp.key, {}).get(inv.name, Decimal("0")) if comp.key in active \
                        else Decimal("0")
                    row_amounts[comp.key] = amount
                    offset = alloc.offsets.get(comp.key, {}).get(inv.name)
                    basis_ref = cpct_ref if comp.side == "distribution" and "contributed_pct" in cols else pct_ref
                    if comp.key == "mgmt_fee" and comp.key in active:
                        formula = (f"SUMIFS({q(fee_sheet)}!${fee_amount_col}:${fee_amount_col},{q(fee_sheet)}!"
                                   f"${fee_name_col}:${fee_name_col},${sheet.col(cols['investor'])}{sheet.row(rr)})")
                    elif comp.key == "carry" and comp.key in active and inv.is_gp:
                        rate_ref = "0.25" if self.defect == "carry_split_wrong" else _abs(self.carry_rate_coord)
                        formula = f"ROUND({driver_ref}*{rate_ref},2)"
                    elif comp.key == "carry" and comp.key in active:
                        formula = (f"ROUND(({driver_ref}-{sheet.col(col)}${sheet.row(block['gp_row'])})*{basis_ref},2)")
                    elif (comp.key, inv.name) in alloc.unrounded:
                        formula = f"{driver_ref}*{basis_ref}"
                    else:
                        formula = f"ROUND({driver_ref}*{basis_ref},2)"
                    if offset:
                        formula += f"{'+' if offset > 0 else '-'}{_fmt_offset(abs(offset))}"
                    fmt = "General" if (self.defect == "fmt_general_money" and comp.key == "investment") else MONEY_FMT
                    if self.defect == "hardcoded_lp_cell" and comp.key == "investment" \
                            and inv.name == "Alder & Finch Holdings, LLC":
                        put(col, _num(amount), fmt=fmt)
                    else:
                        put(col, formula=formula, cached=_num(amount), fmt=fmt)
                call_total = sum(row_amounts[c.key] for c in CALL_COMPONENTS)
                dist_total = sum(row_amounts[c.key] for c in DIST_COMPONENTS)
                put("call_total", formula=(f"SUM({sheet.coord(rr, call_cols[0])}:{sheet.coord(rr, call_cols[-1])})"),
                    cached=_num(call_total))
                put("dist_total", formula=(f"SUM({sheet.coord(rr, dist_cols[0])}:{sheet.coord(rr, dist_cols[-1])})"),
                    cached=_num(dist_total))
                if self.defect == "stale_component" and not inv.is_gp:
                    stale = r2(Decimal("60000") * inv.commitment / vehicle_commitment)
                    put("stale", _num(stale))
                put("late_interest", formula=f"ROUND({sheet.col(cols['late_interest'])}${sheet.row(fund_driver_row)}*{pct_ref},2)",
                    cached=0)
                put("cash_due", formula=(
                    f"{sheet.coord(rr, cols['call_total'])}+{sheet.coord(rr, cols['dist_total'])}+"
                    f"{sheet.coord(rr, cols['late_interest'])}"), cached=_num(call_total + dist_total))
                # Roll-forward.
                itd_e = self.itd_cum[inv.name]["total_contributions"]
                itd_dist = self.itd_cum[inv.name]["total_distributions"]
                prior = itd_e - call_total  # AJ = -(ITD E) + call total  -> -prior
                put("rf_commitment", formula=sheet.coord(rr, cols["commitment"]), cached=_num(inv.commitment))
                inv_ref = f"${sheet.col(cols['investor'])}{sheet.row(rr)}"
                put("rf_prior", formula=(f"-SUMIFS({q(itd.name)}!${itd_total_contrib}:${itd_total_contrib},{itd_inv},"
                                         f"{inv_ref})+{sheet.coord(rr, cols['call_total'])}"), cached=_num(-prior))
                put("rf_prior_recallable", 0)
                current_call = -call_total
                if self.defect == "rf_current_call_stale" and inv.name == "Pemberton Street FLP":
                    stale_amount = -sum(self.prior_allocations[-1].amounts[k].get(inv.name, Decimal("0"))
                                        for k in self.prior_allocations[-1].amounts if C_BY_KEY[k].side == "call")
                    current_call = stale_amount
                    put("rf_current_call", _num(current_call))
                else:
                    put("rf_current_call", formula=f"-{sheet.coord(rr, cols['call_total'])}", cached=_num(current_call))
                put("rf_current_recallable", 0)
                remaining = inv.commitment - prior + current_call
                put("rf_remaining", formula=(
                    f"SUM({sheet.coord(rr, cols['rf_commitment'])}:{sheet.coord(rr, cols['rf_current_recallable'])})"),
                    cached=_num(remaining))
                prior_dist = itd_dist - dist_total
                put("prior_gross_dist", formula=(
                    f"SUMIFS({q(itd.name)}!${itd_total_dist}:${itd_total_dist},{itd_inv},{inv_ref})-"
                    f"{sheet.coord(rr, cols['dist_total'])}"), cached=_num(prior_dist))
                put("current_dist", formula=sheet.coord(rr, cols["dist_total"]), cached=_num(dist_total))
                put("total_gross_dist", formula=(
                    f"{sheet.coord(rr, cols['prior_gross_dist'])}+{sheet.coord(rr, cols['current_dist'])}"),
                    cached=_num(prior_dist + dist_total))
                target = col_sums_gp if inv.is_gp else col_sums_lp
                for col, val in row_vals.items():
                    target[col] = target.get(col, Decimal("0")) + val
                truth_investors[inv.name] = {
                    "vehicle": vehicle,
                    "row": sheet.row(rr),
                    "commitment": str(inv.commitment),
                    "is_gp": inv.is_gp,
                    "affiliate": inv.affiliate,
                    "amounts": {sheet.col(self.comp_col[k]): str(a) for k, a in row_amounts.items() if k in active},
                    "call_total": str(call_total),
                    "dist_total": str(dist_total),
                    "prior_contributions": str(prior),
                    "remaining_commitment": str(remaining),
                }
                if self.defect == "hidden_populated_row" and inv.name == "Juniper Hollow Partners":
                    sheet.hidden_rows.add(sheet.row(rr))

            # Subtotals: LP, GP, vehicle total.
            numeric_cols = sorted(set(col_sums_lp) | set(col_sums_gp))
            first_lp = min(block["investor_rows"].values())
            last_lp = max(block["investor_rows"].values())
            sheet.put(block["lp_subtotal"], cols["investor"], "Limited Partners")
            sheet.put(block["gp_subtotal"], cols["investor"], "General Partner")
            sheet.put(block["total_row"], cols["investor"],
                      f"Total {vehicle}" if len(self.vehicles) > 1 else "Total Partnership")
            for col in numeric_cols:
                if col == cols["close"]:
                    continue
                lp_first = first_lp
                lp_value = col_sums_lp.get(col, Decimal("0"))
                if self.defect == "short_subtotal_range" and col == cols["rf_prior"] and block is self.ablocks[0]:
                    lp_first = first_lp + 1  # the subtotal skips the first LP
                    lp_value -= Decimal(str(sheet.get(sheet.coord(first_lp, col)).cached))
                fmt = PCT_FMT if col in (cols["commitment_pct"], cols.get("contributed_pct")) else MONEY_FMT
                sheet.put(block["lp_subtotal"], col, formula=(
                    f"SUM({sheet.coord(lp_first, col)}:{sheet.coord(last_lp, col)})"), cached=_num(lp_value), fmt=fmt)
                gp_value = col_sums_gp.get(col, Decimal("0"))
                sheet.put(block["gp_subtotal"], col, formula=f"SUM({sheet.coord(block['gp_row'], col)})",
                          cached=_num(gp_value), fmt=fmt)
                sheet.put(block["total_row"], col, formula=(
                    f"{sheet.coord(block['lp_subtotal'], col)}+{sheet.coord(block['gp_subtotal'], col)}"),
                    cached=_num(lp_value + gp_value), fmt=fmt)
                grand[col] = grand.get(col, Decimal("0")) + lp_value + gp_value
            vehicle_layouts.append({
                "name": vehicle,
                "title_row": sheet.row(block["title_row"]) if "title_row" in block else None,
                "driver_row": sheet.row(vdriver_row),
                "investor_rows": [sheet.row(first_lp), sheet.row(last_lp)],
                "gp_rows": [sheet.row(block["gp_row"])],
                "subtotal_rows": {"limited_partners": sheet.row(block["lp_subtotal"]),
                                  "general_partner": sheet.row(block["gp_subtotal"]),
                                  "total": sheet.row(block["total_row"])},
            })

        if len(self.vehicles) > 1:
            gt = rows["grand_total"]
            sheet.put(gt, cols["investor"], "Grand Total")
            for col, value in grand.items():
                if col == cols["close"]:
                    continue
                fmt = PCT_FMT if col in (cols["commitment_pct"], cols.get("contributed_pct")) else MONEY_FMT
                sheet.put(gt, col, formula="+".join(sheet.coord(b["total_row"], col) for b in self.ablocks),
                          cached=_num(value), fmt=fmt)

        # Check row: grand totals minus fund drivers.
        check = rows["check"]
        sheet.put(check, cols["investor"], "Check")
        for key in [*self.comp_col, "call_total", "dist_total", "cash_due"]:
            col = self.comp_col.get(key) or cols[key]
            total = grand.get(col, Decimal("0"))
            driver_cached = sheet.get(sheet.coord(fund_driver_row, col))
            driver_value = Decimal(str(driver_cached.cached if driver_cached.formula else driver_cached.value))
            sheet.put(check, col, formula=(
                f"ROUND({sheet.coord(rows['grand_total'], col)}-{sheet.coord(fund_driver_row, col)},2)"),
                cached=_num(r2(total - driver_value)), fmt=MONEY_FMT)

        if self.defect == "formula_error":
            sheet.put(self.ablocks[0]["lp_subtotal"], cols["error_cell"], formula="#REF!", cached="#REF!")

        sheet.view = "normal" if self.defect == "normal_view" else "pageBreakPreview"
        self.sheets.append(sheet)
        self.alloc_grand = grand

        components_layout = []
        for comp in (*CALL_COMPONENTS, *DIST_COMPONENTS):
            components_layout.append({
                "column": sheet.col(self.comp_col[comp.key]),
                "header": comp_headers[comp.key],
                "component_type": comp.component_type,
                "side": comp.side,
                "active": comp.key in active,
            })
        if self.defect == "stale_component":
            components_layout.append({"column": sheet.col(cols["stale"]), "header": "Placement Fees (Capital Call #3)",
                                      "component_type": "placement_fee", "side": "call", "active": False})
        column_map = {
            "investor": sheet.col(cols["investor"]),
            "affiliate_flag": sheet.col(cols["affiliate"]),
            "commitment": sheet.col(cols["commitment"]),
            "commitment_pct": sheet.col(cols["commitment_pct"]),
            "late_interest": sheet.col(cols["late_interest"]),
            "cash_due": sheet.col(cols["cash_due"]),
            "received": sheet.col(cols["received"]),
            "received_date": sheet.col(cols["received_date"]),
        }
        if "contributed_pct" in cols:
            column_map["distribution_basis"] = sheet.col(cols["contributed"])
            column_map["distribution_basis_pct"] = sheet.col(cols["contributed_pct"])
        self.layouts[sheet.name] = {
            "role": "allocation",
            "sheet": sheet.name,
            "header_row": sheet.row(header),
            "fund_driver_row": sheet.row(fund_driver_row),
            "event": {
                "event_type": event.kind,
                "label": event.short_label,
                "label_cell": self.event_label_coord,
                "notice_date_cell": notice_coord,
                "due_date_cell": due_coord,
                "carried_interest_rate_cell": getattr(self, "carry_rate_coord", None),
            },
            "columns": column_map,
            "components": components_layout,
            "event_total_columns": [
                {"column": sheet.col(cols["call_total"]), "side": "call"},
                {"column": sheet.col(cols["dist_total"]), "side": "distribution"},
            ],
            "roll_forward": {
                "commitment": sheet.col(cols["rf_commitment"]),
                "prior_contributions": sheet.col(cols["rf_prior"]),
                "prior_recallable": sheet.col(cols["rf_prior_recallable"]),
                "current_call": sheet.col(cols["rf_current_call"]),
                "current_recallable": sheet.col(cols["rf_current_recallable"]),
                "remaining_commitment": sheet.col(cols["rf_remaining"]),
            },
            "vehicles": vehicle_layouts,
            "grand_total_row": sheet.row(rows["grand_total"]),
            "check_rows": [sheet.row(check)],
        }
        self.truth["allocation"] = {
            "investors": truth_investors,
            "drivers": {sheet.col(self.comp_col[k]): str(a) for k, a in drivers.items() if k in active},
            "event_gross": str(sum(drivers[k] for k in active)),
            "notice_date": NOTICE_DATE.isoformat(),
            "due_date": due_date.isoformat(),
        }

    # -- Summary --------------------------------------------------------------------------

    def _build_summary(self) -> None:
        v = self.variant
        sheet = SheetBuilder(self.names["summary"], "summary", v.row_offset, v.col_offset)
        alloc = self.alloc_sheet
        a = q(alloc.name)
        event = self.current
        cols = self.acols
        gt = alloc.row(self.arows["grand_total"])
        sheet.put(2, 2, formula=f"{a}!{alloc.coord(self.arows['fund_name'], 2)}", cached=FUND_NAME)
        word = "Distribution - payable " if event.kind == "distribution" else "Capital Call - due "
        title = f"{word}{self.due_date:%B} {self.due_date.day}, {self.due_date.year}"
        title_coord = sheet.put(3, 2, formula=f"\"{word}\"&TEXT({a}!{self.due_coord},\"mmmm d, yyyy\")", cached=title)
        sheet.put(5, 3, "Notice Date")
        notice_cell = sheet.put(5, 4, formula=f"{a}!{self.notice_coord}", cached=_serial(NOTICE_DATE), fmt=DATE_LONG_FMT)
        sheet.put(6, 3, "Payment Date" if event.kind == "distribution" else "Due Date")
        due_cell = sheet.put(6, 4, formula=f"{a}!{self.due_coord}", cached=_serial(self.due_date), fmt=DATE_LONG_FMT)
        sheet.put(8, 2, "Total Commitments" if v.alt_headers else "Total Fund Commitments")
        total_commit = self.alloc_grand[cols["commitment"]]
        commit_cell = sheet.put(8, 4, formula=f"{a}!{alloc.col(cols['commitment'])}{gt}", cached=_num(total_commit),
                                fmt=MONEY_FMT)
        sheet.put(8, 5, "% of commitment")
        r = 10
        lines = []
        sections = []
        for side, title_text in (("call", "Current Capital Call:"), ("distribution", "Current Distribution:")):
            keys = [k for k in event.drivers if C_BY_KEY[k].side == side]
            if not keys:
                continue
            sheet.put(r, 3, title_text)
            r += 1
            first = r
            for key in keys:
                col = alloc.col(self.comp_col[key])
                amount = self.alloc_grand[self.comp_col[key]]
                label = self.comp_headers[key]
                sheet.put(r, 3, formula=f"{a}!{col}{alloc.row(self.arows['header'])}", cached=label)
                if self.defect == "summary_line_hardcoded" and key == "expenses":
                    amount = amount - Decimal("0.05")
                    amount_cell = sheet.put(r, 4, _num(amount), fmt=MONEY_FMT)
                else:
                    amount_cell = sheet.put(r, 4, formula=f"{a}!{col}{gt}", cached=_num(amount), fmt=MONEY_FMT)
                sheet.put(r, 5, formula=f"{amount_cell}/{_abs(commit_cell)}", cached=float(amount / total_commit),
                          fmt=PCT_FMT)
                lines.append({"label_cell": sheet.coord(r, 3), "amount_cell": amount_cell,
                              "component_type": C_BY_KEY[key].component_type, "side": side})
                r += 1
            total = sum(Decimal(str(sheet.get(line["amount_cell"]).cached if sheet.get(line["amount_cell"]).formula
                                    else sheet.get(line["amount_cell"]).value))
                        for line in lines if line["side"] == side)
            label = "Total Current Capital Call" if side == "call" else "Total Current Distribution"
            sheet.put(r, 3, label)
            total_cell = sheet.put(r, 4, formula=f"SUM({sheet.coord(first, 4)}:{sheet.coord(r - 1, 4)})",
                                   cached=_num(total), fmt=MONEY_FMT)
            sections.append({"side": side, "total_cell": total_cell, "total": total})
            r += 2
        net = sum(s["total"] for s in sections)
        sheet.put(r, 3, "Total Net Cash Due" if len(sections) > 1 or sections[0]["side"] == "call"
                  else "Total Cash to LPs")
        net_cell = sheet.put(r, 4, formula="+".join(s["total_cell"] for s in sections), cached=_num(net), fmt=MONEY_FMT)
        sheet.put(r, 6, "check")
        alloc_cash = self.alloc_grand[cols["cash_due"]]
        check_cell = sheet.put(r, 7, formula=f"{a}!{alloc.col(cols['cash_due'])}{gt}-{net_cell}",
                               cached=_num(alloc_cash - net), fmt=MONEY_FMT)
        self.sheets.append(sheet)
        self.layouts[sheet.name] = {
            "role": "summary",
            "sheet": sheet.name,
            "title_cell": title_coord,
            "notice_date_cell": notice_cell,
            "due_date_cell": due_cell,
            "fund_commitment_cell": commit_cell,
            "component_lines": lines,
            "section_totals": [{"side": s["side"], "cell": s["total_cell"]} for s in sections],
            "event_total_cell": net_cell,
            "check_cells": [check_cell],
        }
        self.truth["summary"] = {"event_total": str(net), "check": str(alloc_cash - net)}

    # -- Merge tabs -----------------------------------------------------------------------

    def _build_merge_tabs(self) -> None:
        alloc = self.alloc_sheet
        a = q(alloc.name)
        event = self.current
        cols = self.acols
        alt_ids = self.variant.name == "shifted"
        active = [k for k in event.drivers]
        label_cached = alloc.get(self.event_label_coord).value
        for vehicle, tab in self.merge_names.items():
            sheet = SheetBuilder(tab, "merge")
            headers = ["Investor", "Short Name", "Letter Date", "Due (Wire) Date",
                       "DX Investor ID" if alt_ids else "Investor ID", "DX Fund ID" if alt_ids else "Fund ID",
                       "File Name", "Commitment Amount", "Commitment %"]
            comp_cols: dict[str, int] = {}
            for i, h in enumerate(headers, start=1):
                sheet.put(1, i, h)
            c = len(headers) + 1
            for key in active:
                comp_cols[key] = c
                sheet.put(1, c, formula=f"{a}!{alloc.col(self.comp_col[key])}{alloc.row(self.arows['header'])}",
                          cached=self.comp_headers[key])
                c += 1
            total_col = c
            sheet.put(1, total_col, "Cash Due")
            check_col = c + 1
            sheet.put(1, check_col, "Check")
            sheet.put(2, 1, formula=f"{a}!{alloc.coord(self.arows['fund_name'], 2)}", cached=FUND_NAME)
            r = 4
            first_row = r
            lps = [i for i in self.in_vehicle(vehicle) if not i.is_gp]
            totals: dict[int, Decimal] = {}
            for inv in lps:
                arow = self.investor_row[inv.name]
                a_inv = alloc.col(cols["investor"])
                sheet.put(r, 1, formula=f"{a}!{a_inv}{arow}", cached=inv.name)
                short = "{Investor Short Name}" if (self.defect == "placeholder_left" and inv.name == "Tamsin Rourke") \
                    else inv.name
                if short != inv.name:
                    sheet.put(r, 2, short)
                else:
                    sheet.put(r, 2, formula=f"A{r}", cached=inv.name)
                sheet.put(r, 3, formula=f"{a}!{_abs(self.notice_coord)}", cached=_serial(NOTICE_DATE), fmt=DATE_LONG_FMT)
                sheet.put(r, 4, formula=f"{a}!{_abs(self.due_coord)}", cached=_serial(self.due_date), fmt=DATE_LONG_FMT)
                sheet.put(r, 5, inv.investor_id)
                sheet.put(r, 6, inv.fund_id)
                file_name = f"{inv.fund_id}_{inv.investor_id}_{FUND_NAME}_{label_cached}"
                sheet.put(r, 7, formula=f"$F{r}&\"_\"&$E{r}&\"_\"&$A$2&\"_\"&{a}!{_abs(self.event_label_coord)}",
                          cached=file_name)
                sheet.put(r, 8, formula=f"{a}!{alloc.col(cols['commitment'])}{arow}", cached=_num(inv.commitment),
                          fmt=MONEY_FMT)
                pct_cell = alloc.get(f"{alloc.col(cols['commitment_pct'])}{arow}")
                sheet.put(r, 9, formula=f"{a}!{alloc.col(cols['commitment_pct'])}{arow}",
                          cached=pct_cell.cached if pct_cell.formula else pct_cell.value, fmt=PCT_FMT)
                row_total = Decimal("0")
                for key, col in comp_cols.items():
                    amount = self.current_alloc.amounts[key].get(inv.name, Decimal("0"))
                    sheet.put(r, col, formula=f"{a}!{alloc.col(self.comp_col[key])}{arow}", cached=_num(amount),
                              fmt=MONEY_FMT)
                    totals[col] = totals.get(col, Decimal("0")) + amount
                    row_total += amount
                sheet.put(r, total_col, formula=f"SUM({sheet.coord(r, comp_cols[active[0]])}:"
                                                f"{sheet.coord(r, comp_cols[active[-1]])})",
                          cached=_num(row_total), fmt=MONEY_FMT)
                totals[total_col] = totals.get(total_col, Decimal("0")) + row_total
                a_cash = alloc.col(cols["cash_due"])
                sheet.put(r, check_col, formula=(f"-{sheet.coord(r, total_col)}+SUMIFS({a}!${a_cash}:${a_cash},"
                                                 f"{a}!${a_inv}:${a_inv},$A{r})"), cached=0, fmt=MONEY_FMT)
                r += 1
            last_row = r - 1
            if self.defect == "probe_inactive_investor_na" and vehicle == "Main Fund":
                sheet.put(r, 1, "Harlan Transfer Trust (transferred)")
                sheet.put(r, 5, formula="INDEX('DX Investor Data'!E:E,MATCH(A%d,'DX Investor Data'!D:D,0))" % r,
                          cached="#N/A")
                sheet.put(r, 7, formula=f"$F{r}&\"_\"&$E{r}", cached="#N/A")
                last_row = r
                r += 1
            r += 1
            total_row = r
            sheet.put(total_row, 1, "TOTAL:")
            for col, value in totals.items():
                sheet.put(total_row, col, formula=f"SUM({sheet.coord(first_row, col)}:{sheet.coord(last_row, col)})",
                          cached=_num(value), fmt=MONEY_FMT)
            self.sheets.append(sheet)
            self.layouts[sheet.name] = {
                "role": "merge",
                "sheet": sheet.name,
                "vehicle": vehicle,
                "header_row": 1,
                "first_data_row": first_row,
                "last_data_row": last_row,
                "columns": {
                    "investor": "A", "short_name": "B", "letter_date": "C", "due_date": "D",
                    "investor_id": "E", "fund_id": "F", "file_name": "G", "commitment": "H", "commitment_pct": "I",
                    "event_total": get_column_letter(total_col), "check": get_column_letter(check_col),
                },
                "component_columns": [
                    {"column": get_column_letter(col), "component_type": C_BY_KEY[key].component_type,
                     "side": C_BY_KEY[key].side}
                    for key, col in comp_cols.items()
                ],
                "total_row": total_row,
            }

    # -- DX Investor Data -------------------------------------------------------------------

    def _build_investor_data(self) -> None:
        sheet = SheetBuilder(self.names["investor_data"], "investor_data")
        for i, h in enumerate(["Fund Name", "Fund ID", "Fund Tax ID", "Investor Name", "Investor ID",
                               "Investor Tax ID"], start=1):
            sheet.put(1, i, h)
        r = 2
        for inv in self.investors:
            if inv.is_gp:
                continue
            name = inv.name
            if self.defect == "investor_name_mismatch" and name == "Crescent Ridge Investments LP":
                name = "Crescent Ridge Investments, L.P."
            sheet.put(r, 1, FUND_NAME)
            sheet.put(r, 2, inv.fund_id)
            sheet.put(r, 4, name)
            sheet.put(r, 5, inv.investor_id)
            r += 1
        # A transferred-out investor that no longer participates (out of scope for identity checks).
        sheet.put(r, 1, FUND_NAME)
        sheet.put(r, 2, 901)
        sheet.put(r, 4, "Harlan Transfer Trust")
        sheet.put(r, 5, 29999)
        self.sheets.append(sheet)
        self.layouts[sheet.name] = {
            "role": "investor_data",
            "sheet": sheet.name,
            "header_row": 1,
            "first_data_row": 2,
            "last_data_row": r,
            "columns": {"fund_name": "A", "fund_id": "B", "investor_name": "D", "investor_id": "E"},
        }

    # -- Mgmt fee tab -----------------------------------------------------------------------

    def _fee_rows(self) -> dict[str, Any]:
        lps = [i for i in self.investors if not i.is_gp]
        gps = [i for i in self.investors if i.is_gp]
        first = 9
        lp_rows = {inv.name: first + idx for idx, inv in enumerate(lps)}
        lp_sub = first + len(lps) + 1
        gp_rows = {inv.name: lp_sub + 2 + idx for idx, inv in enumerate(gps)}
        gp_sub = lp_sub + 2 + len(gps) + 1
        return {"lp_rows": lp_rows, "lp_sub": lp_sub, "gp_rows": gp_rows, "gp_sub": gp_sub, "total": gp_sub + 1,
                "check": gp_sub + 2}

    def _fee_total_row(self) -> int:
        return self._fee_rows()["total"]

    def _build_fee_tab(self) -> None:
        sheet = SheetBuilder(self.names["mgmt_fee"], "mgmt_fee")
        alloc = self.alloc_sheet
        a = q(alloc.name)
        cols = self.acols
        event = self.current
        period = event.fee_period or self.prior_events[-1].fee_period
        if self.defect == "stale_fee_period":
            period = "Q2 2026"
        fee_is_current = "mgmt_fee" in event.drivers
        rows = self._fee_rows()
        sheet.put(2, 2, formula=f"{a}!{alloc.coord(self.arows['fund_name'], 2)}", cached=FUND_NAME)
        sheet.put(3, 2, "Management Fee Calculation")
        sheet.put(5, 5, "Mgmt Fee %")
        rate_cell = sheet.put(5, 6, _num(MGMT_FEE_RATE), fmt="0.00%")
        sheet.put(6, 5, "% of year")
        frac_cell = sheet.put(6, 6, _num(FEE_PERIOD_FRACTION), fmt="0.00")
        for i, h in enumerate(["Investor", "Vehicle", "Affiliate?", "Commitment Amount",
                               f"{period} Mgmt Fees (2.0%)"], start=2):
            sheet.put(8, i, h)
        round_digits = 0 if self.whole_dollar_fees else 2
        lp_total = Decimal("0")
        commit_total = Decimal("0")
        for inv in self.investors:
            if inv.is_gp:
                continue
            rr = rows["lp_rows"][inv.name]
            arow = self.investor_row[inv.name]
            sheet.put(rr, 2, formula=f"{a}!{alloc.col(cols['investor'])}{arow}", cached=inv.name)
            sheet.put(rr, 3, inv.vehicle)
            flag = "Y" if inv.affiliate and self.defect != "affiliate_charged_fee" else "N"
            sheet.put(rr, 4, flag)
            sheet.put(rr, 5, formula=f"{a}!{alloc.col(cols['commitment'])}{arow}", cached=_num(inv.commitment),
                      fmt=MONEY_FMT)
            fee = self.fee_for(inv) if fee_is_current else _fee(inv)
            if self.defect == "fee_tab_value_wrong" and inv.name == "Meridian Endowment Fund":
                sheet.put(rr, 6, _num(fee), fmt=MONEY_FMT)
            else:
                sheet.put(rr, 6, formula=f"IF($D{rr}=\"N\",ROUND($E{rr}*F$5*F$6,{round_digits}),0)", cached=_num(fee),
                          fmt=MONEY_FMT)
            lp_total += fee
            commit_total += inv.commitment
        first_lp = min(rows["lp_rows"].values())
        last_lp = max(rows["lp_rows"].values())
        sheet.put(rows["lp_sub"], 2, "Limited Partners")
        sheet.put(rows["lp_sub"], 5, formula=f"SUM(E{first_lp}:E{last_lp})", cached=_num(commit_total), fmt=MONEY_FMT)
        sheet.put(rows["lp_sub"], 6, formula=f"SUM(F{first_lp}:F{last_lp})", cached=_num(lp_total), fmt=MONEY_FMT)
        for inv in self.investors:
            if not inv.is_gp:
                continue
            rr = rows["gp_rows"][inv.name]
            arow = self.investor_row[inv.name]
            sheet.put(rr, 2, formula=f"{a}!{alloc.col(cols['investor'])}{arow}", cached=inv.name)
            sheet.put(rr, 3, inv.vehicle)
            sheet.put(rr, 4, "GP")
            sheet.put(rr, 5, formula=f"{a}!{alloc.col(cols['commitment'])}{arow}", cached=0, fmt=MONEY_FMT)
            sheet.put(rr, 6, 0, fmt=MONEY_FMT)
        gp_first, gp_last = min(rows["gp_rows"].values()), max(rows["gp_rows"].values())
        sheet.put(rows["gp_sub"], 2, "General Partner (non paying)")
        sheet.put(rows["gp_sub"], 5, formula=f"SUM(E{gp_first}:E{gp_last})", cached=0, fmt=MONEY_FMT)
        sheet.put(rows["gp_sub"], 6, formula=f"SUM(F{gp_first}:F{gp_last})", cached=0, fmt=MONEY_FMT)
        sheet.put(rows["total"], 2, "Total")
        sheet.put(rows["total"], 5, formula=f"E{rows['lp_sub']}+E{rows['gp_sub']}", cached=_num(commit_total),
                  fmt=MONEY_FMT)
        sheet.put(rows["total"], 6, formula=f"F{rows['lp_sub']}+F{rows['gp_sub']}", cached=_num(lp_total),
                  fmt=MONEY_FMT)
        sheet.put(7, 6, formula=f"F{rows['total']}", cached=_num(lp_total), fmt=MONEY_FMT)
        gt = alloc.row(self.arows["grand_total"])
        sheet.put(rows["check"], 5, formula=f"{a}!{alloc.col(cols['commitment'])}{gt}-E{rows['total']}", cached=0,
                  fmt=MONEY_FMT)
        sheet.view = "pageBreakPreview"
        self.sheets.append(sheet)
        self.layouts[sheet.name] = {
            "role": "mgmt_fee",
            "sheet": sheet.name,
            "header_row": 8,
            "investor_rows": [first_lp, last_lp],
            "gp_rows": sorted(rows["gp_rows"].values()),
            "columns": {"investor": "B", "vehicle": "C", "affiliate_flag": "D", "commitment": "E"},
            "rate_cells": [rate_cell],
            "period_fraction_cells": [frac_cell],
            "fee_columns": [{"column": "F", "period_label": period}],
            "subtotal_rows": {"limited_partners": rows["lp_sub"], "general_partner": rows["gp_sub"],
                              "total": rows["total"]},
            "check_rows": [rows["check"]],
        }
        self.truth["mgmt_fee"] = {"total": str(lp_total), "period": period}

    # -- Other sheets -------------------------------------------------------------------------

    def _build_other_sheets(self) -> None:
        notes = SheetBuilder("Notes", "other")
        notes.put(1, 1, "Support and notes for the capital event workpapers.")
        notes.put(3, 1, "Reviewer sign-off tracked in the fund's document system.")
        self.sheets.append(notes)

        tracker = SheetBuilder("Portfolio Investment Tracker", "other")
        for i, h in enumerate(["Deal #", "Event", "Deal", "Amount Called", "Wire Date"], start=2):
            tracker.put(5, i, h)
        deals = [(1, "CC#1", "Northwind Robotics", Decimal("20000000"), dt.date(2024, 1, 25)),
                 (2, "CC#2", "Solace Diagnostics", Decimal("10000000"), dt.date(2024, 6, 27)),
                 (3, "CC#3", "Tidepool Logistics", Decimal("5000000"), dt.date(2026, 3, 11))]
        if self.current.kind == "capital_call":
            wire = "TBD" if self.defect == "probe_tbd_placeholder" else dt.date(2026, 6, 12)
            deals.append((4, "CC#4", "Ember Grid Storage", Decimal("8500000"), wire))
        for r, (n, ev, deal, amount, wire) in enumerate(deals, start=6):
            tracker.put(r, 2, n)
            tracker.put(r, 3, ev)
            tracker.put(r, 4, deal)
            tracker.put(r, 5, _num(amount), fmt=MONEY_FMT)
            tracker.put(r, 6, wire, fmt=DATE_SHORT_FMT if isinstance(wire, dt.date) else None)
        self.sheets.append(tracker)

        legacy = SheetBuilder("3rd Close Rebalance", "other")
        legacy.state = "hidden"
        legacy.put(1, 1, "3rd Close Rebalance (2024)")
        legacy.put(3, 1, "Investor")
        legacy.put(3, 2, "Rebalance Amount")
        for r, inv in enumerate(self.lps()[:4], start=4):
            legacy.put(r, 1, inv.name)
            legacy.put(r, 2, _num(r2(inv.commitment * Decimal("0.0021"))), fmt=MONEY_FMT)
        if self.defect == "probe_legacy_hidden_errors":
            legacy.put(9, 2, formula="#REF!*2", cached="#REF!")
        self.sheets.append(legacy)

    # -- Cell-level defects -------------------------------------------------------------------

    def _apply_cell_defects(self) -> None:
        # Most defects are data-level (applied while computing values, so every dependent
        # cached value stays consistent). Cell-level ones are applied at build time above.
        pass


def _spread_residual(driver: Decimal, amounts: dict[str, Decimal], members: list[Investor]) -> dict[str, Decimal]:
    """Plug pattern seen in the reference sample: the rounding residual spread as cent
    offsets over the top three eligible LPs instead of a single plug."""
    top = sorted([i for i in members if not i.is_gp and not i.affiliate], key=lambda i: -i.commitment)[:3]
    residual = driver - sum(amounts.values())
    pattern = [Decimal("0.02"), Decimal("0.02"), residual - Decimal("0.04")]
    offsets: dict[str, Decimal] = {}
    for inv, off in zip(top, pattern):
        if off:
            amounts[inv.name] += off
            offsets[inv.name] = off
    return offsets


C_BY_KEY = {c.key: c for c in (*CALL_COMPONENTS, *DIST_COMPONENTS)}


def _clean_facts(event_type: str) -> dict[str, dict[str, Any]]:
    facts: dict[str, dict[str, Any]] = {
        "CE-WB-NO-PLACEHOLDERS": {"placeholder_count": 0, "tbd_cells": 0},
        "CE-ITD-EVENT-BLOCK": {"unclassified_current_columns": 0, "overlay_rows": 0},
        "CE-DATE-CONSISTENCY": {"fee_period_mismatch": False, "distinct_event_numbers": [4 if event_type != "distribution" else 2]},
        "CE-ALLOC-STALE-COMPONENTS": {"stale_columns": 0},
    }
    if event_type == "distribution":
        facts["CE-DIST-CARRY-SPLIT"] = {"gp_share_matches_rate": True}
    return facts


def _abs(coord: str) -> str:
    m = re.fullmatch(r"([A-Z]+)(\d+)", coord)
    return f"${m.group(1)}${m.group(2)}"


def _abs_row(coord: str) -> str:
    m = re.fullmatch(r"([A-Z]+)(\d+)", coord)
    return f"{m.group(1)}${m.group(2)}"


def _fmt_offset(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _cached_decimal(sheet: SheetBuilder, coord: str) -> Decimal:
    cell = sheet.cells.get(coord)
    if cell is None:
        return Decimal("0")
    value = cell.cached if cell.formula else cell.value
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return Decimal(str(value))
    return Decimal("0")


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _write_workbook(sheets: list[SheetBuilder], path: Path) -> None:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet in sheets:
        ws = wb.create_sheet(sheet.name)
        ws.sheet_state = sheet.state
        if sheet.view:
            ws.sheet_view.view = sheet.view
        for coord, cell in sheet.cells.items():
            target = ws[coord]
            if cell.formula is not None:
                target.value = "=" + cell.formula
            elif isinstance(cell.value, Decimal):
                target.value = _num(cell.value)
            else:
                target.value = cell.value
            if cell.fmt:
                target.number_format = cell.fmt
        for rng in sheet.merges:
            ws.merge_cells(rng)
        for r in sheet.hidden_rows:
            ws.row_dimensions[r].hidden = True
        for c in sheet.hidden_cols:
            ws.column_dimensions[c].hidden = True
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "raw.xlsx"
        wb.save(raw)
        _inject_cached_values(raw, path, sheets)


_FORMULA_CELL_RE = re.compile(r'<c r="([A-Z]+\d+)"([^>]*)><f>(.*?)</f><v\s*/></c>', re.S)


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inject_cached_values(src: Path, dst: Path, sheets: list[SheetBuilder]) -> None:
    cached_by_index = {
        idx: {coord: cell.cached for coord, cell in sheet.cells.items() if cell.formula is not None}
        for idx, sheet in enumerate(sheets, start=1)
    }
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            m = re.fullmatch(r"xl/worksheets/sheet(\d+)\.xml", item.filename)
            if m:
                cached = cached_by_index[int(m.group(1))]

                def repl(match: re.Match) -> str:
                    coord, attrs, formula = match.group(1), match.group(2), match.group(3)
                    value = cached.get(coord)
                    if value is None:
                        return match.group(0)
                    if isinstance(value, bool):
                        return f'<c r="{coord}"{attrs} t="b"><f>{formula}</f><v>{int(value)}</v></c>'
                    if isinstance(value, str) and value.startswith("#"):
                        return f'<c r="{coord}"{attrs} t="e"><f>{formula}</f><v>{_xml_escape(value)}</v></c>'
                    if isinstance(value, str):
                        return f'<c r="{coord}"{attrs} t="str"><f>{formula}</f><v>{_xml_escape(value)}</v></c>'
                    return f'<c r="{coord}"{attrs}><f>{formula}</f><v>{_num(value)!r}</v></c>'

                data = _FORMULA_CELL_RE.sub(repl, data.decode("utf-8")).encode("utf-8")
            zout.writestr(item, data)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_fixture(spec: FixtureSpec, out_dir: Path) -> FixtureManifest:
    return _Builder(spec).build(Path(out_dir))


def main() -> None:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)
    index = []
    for spec in default_specs():
        manifest = build_fixture(spec, OUT_DIR)
        (manifest.path.parent / "manifest.json").write_text(json.dumps(manifest.to_json(), indent=2), encoding="utf-8")
        index.append({"fixture_id": spec.fixture_id, "path": str(manifest.path.relative_to(OUT_DIR))})
    (OUT_DIR / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"Wrote {len(index)} fixtures to {OUT_DIR}")


if __name__ == "__main__":
    main()
