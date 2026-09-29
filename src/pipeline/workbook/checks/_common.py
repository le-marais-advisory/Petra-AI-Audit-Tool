"""Registry and helpers for deterministic capital-event checks."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable

from src.pipeline.workbook.extract import WorkbookData
from src.pipeline.workbook.loader import SheetModel, WorkbookModel
from src.providers.analysis_result import AnalysisCitation, AnalysisRuleResult

MAX_FINDINGS = 10
MAX_CITATIONS = 10

ROLE_LABELS = {
    "allocation": "Allocation",
    "itd": "ITD capital activity",
    "summary": "Summary",
    "merge": "Merge",
    "mgmt_fee": "management fee",
    "investor_data": "DX investor data",
    "holiday_calendar": "holiday calendar",
}


@dataclass
class CheckContext:
    model: WorkbookModel
    data: WorkbookData
    rule: dict
    options: dict

    @property
    def event_type(self) -> str | None:
        return self.options.get("event_type") or (self.data.allocation.event.event_type if self.data.allocation else None)


@dataclass
class Outcome:
    """Accumulates findings for one rule; the worst verdict wins (fail > needs_review > pass)."""

    fails: list[str] = field(default_factory=list)
    reviews: list[str] = field(default_factory=list)
    citations: list[AnalysisCitation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def fail(self, message: str, sheet: SheetModel | None = None, cell: str | None = None) -> None:
        self.fails.append(message)
        if sheet is not None:
            self.cite(sheet, cell, message)

    def review(self, message: str, sheet: SheetModel | None = None, cell: str | None = None) -> None:
        self.reviews.append(message)
        if sheet is not None:
            self.cite(sheet, cell, message)

    def cite(self, sheet: SheetModel, cell: str | None, evidence: str) -> None:
        self.citations.append(AnalysisCitation(page=sheet.index, sheet=sheet.name, cell=cell, evidence=evidence[:300]))


CheckFn = Callable[[CheckContext, Outcome], str | None]  # returns the pass summary (or a not_applicable reason)


@dataclass
class CheckSpec:
    rule_id: str
    fn: CheckFn
    needs: tuple[str, ...]


DETERMINISTIC_CHECKS: dict[str, CheckSpec] = {}


class NotApplicable(Exception):
    pass


def check(rule_id: str, needs: tuple[str, ...] = ()) -> Callable[[CheckFn], CheckFn]:
    def register(fn: CheckFn) -> CheckFn:
        DETERMINISTIC_CHECKS[rule_id] = CheckSpec(rule_id=rule_id, fn=fn, needs=needs)
        return fn

    return register


def _present(data: WorkbookData, role: str) -> bool:
    if role == "merge":
        return bool(data.merges)
    if role == "holiday_calendar":
        return data.has_holiday_calendar
    return getattr(data, role, None) is not None


def _limited(items: list[str]) -> list[str]:
    if len(items) <= MAX_FINDINGS:
        return items
    return items[:MAX_FINDINGS] + [f"... and {len(items) - MAX_FINDINGS} more"]


def result(rule: dict, verdict: str, summary: str, findings: list[str] | None = None,
           citations: list[AnalysisCitation] | None = None, reasoning: str = "") -> AnalysisRuleResult:
    return AnalysisRuleResult(
        rule_id=rule["id"],
        rule_name=rule.get("name", rule["id"]),
        verdict=verdict,
        summary=summary,
        reasoning=reasoning or "Evaluated deterministically from the workbook's stored values and formulas.",
        findings=_limited(findings or []),
        confidence="high",
        citations=(citations or [])[:MAX_CITATIONS],
    )


def run_check(spec: CheckSpec, ctx: CheckContext) -> AnalysisRuleResult:
    missing = [role for role in spec.needs if not _present(ctx.data, role)]
    if missing:
        labels = ", ".join(ROLE_LABELS.get(r, r) for r in missing)
        reasons = [ctx.data.extraction_errors[r] for r in missing if r in ctx.data.extraction_errors]
        return result(ctx.rule, "needs_review",
                      f"The {labels} sheet layout could not be mapped or validated, so this check could not run.",
                      findings=reasons)
    outcome = Outcome()
    try:
        pass_summary = spec.fn(ctx, outcome)
    except NotApplicable as exc:
        return result(ctx.rule, "not_applicable", str(exc))
    if outcome.fails:
        return result(ctx.rule, "fail", outcome.fails[0], outcome.fails + outcome.reviews, outcome.citations)
    if outcome.reviews:
        return result(ctx.rule, "needs_review", outcome.reviews[0], outcome.reviews, outcome.citations)
    return result(ctx.rule, "pass", pass_summary or "All checks passed.", outcome.notes, outcome.citations)


# --- numeric helpers ----------------------------------------------------------------------


def money(value: Any) -> str:
    if value is None:
        return "blank"
    number = Decimal(str(value))
    text = f"{abs(number):,.2f}"
    return f"({text})" if number < 0 else text


def differs(a: Decimal | None, b: Decimal | None, tolerance: Decimal) -> bool:
    return abs((a or Decimal("0")) - (b or Decimal("0"))) > tolerance


def mag(value: Decimal | None) -> Decimal:
    return abs(value or Decimal("0"))


_QUARTER_RE = re.compile(r"\b(?:Q([1-4])|([1-4])Q)\s*'?(\d{2,4})\b", re.I)


def quarter_key(text: Any) -> str | None:
    """'Q3 2026', '3Q 2026', '3Q26' -> 'Q3-2026'."""
    match = _QUARTER_RE.search(str(text or ""))
    if not match:
        return None
    quarter = match.group(1) or match.group(2)
    year = match.group(3)
    year = f"20{year}" if len(year) == 2 else year
    return f"Q{quarter}-{year}"


def round_digits(formula: str | None) -> int | None:
    """Digits argument of the first ROUND(...) call in a formula (None if there is none)."""
    if not formula:
        return None
    start = formula.upper().find("ROUND(")
    if start < 0:
        return None
    depth, last_comma = 0, None
    for index in range(start + len("ROUND("), len(formula)):
        char = formula[index]
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                if last_comma is None:
                    return None
                try:
                    return int(formula[last_comma + 1:index].strip())
                except ValueError:
                    return None
            depth -= 1
        elif char == "," and depth == 0:
            last_comma = index
    return None


_EVENT_NUMBER_RE = re.compile(r"#\s*(\d+)")


def event_number(label: Any) -> int | None:
    match = _EVENT_NUMBER_RE.search(str(label or ""))
    return int(match.group(1)) if match else None
