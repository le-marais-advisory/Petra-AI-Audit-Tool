from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from fpdf import FPDF

from src.schemas.export import ExportPdfRequest
from src.schemas.validation import DocumentAnalysisSchema, PageRuleAssessmentSchema, RuleAssessmentSchema


router = APIRouter(prefix="/export", tags=["export"])


# Sentence case, mirroring VERDICT_LABELS in frontend/src/utils/verdicts.ts so the
# web UI and the exported PDF never disagree. One deliberate divergence: the
# frontend renders `not_applicable` as "N/A" because it sits in a badge; the PDF
# has room for the full label.
_VERDICT_LABELS = {
    "pass": "Pass",
    "fail": "Fail",
    "needs_review": "Needs review",
    "not_applicable": "Not applicable",
    "skipped": "Skipped",
}

# Verdicts a reviewer must act on. A page result with any other verdict is filtered
# out of the report; a rule with no surviving pages is dropped, and a group with no
# surviving rules is dropped with it.
_ACTION_VERDICTS = {"fail", "needs_review"}

# Statuses meaning "the rule reached a deliberate terminal state". Everything else —
# including values we have never seen — counts as incomplete. Mirrors
# SETTLED_EXECUTION_STATUSES in verdicts.ts. This guard matters: the analyzers stamp
# a fallback verdict of `needs_review` on rules that errored or were skipped (see
# `_build_skipped_result` in src/pipeline/text_rule_analyzer.py), so filtering on the
# verdict alone would report a crashed rule as an audit finding.
_SETTLED_EXECUTION_STATUSES = {"completed", "not_applicable"}

_NOT_RUN_LABEL = "Not run"
_UNCATEGORIZED_KEY = "__uncategorized__"

_MARGIN = 10.0
_ENTRY_INDENT = 6.0

_COLOR_HEADING = (15, 23, 42)  # slate-950
_COLOR_BODY = (51, 65, 85)  # slate-700
_COLOR_MUTED = (100, 116, 139)  # slate-500
_COLOR_FAINT = (148, 163, 184)  # slate-400
_COLOR_FAIL = (190, 18, 60)  # rose-700
_COLOR_REVIEW = (146, 64, 14)  # amber-700
_COLOR_RULE_LINE = (203, 213, 225)  # slate-300

_PDF_CHAR_REPLACEMENTS = str.maketrans(
    {
        "‒": "-",
        "–": "-",
        "—": "-",
        "―": "-",
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "•": "-",
        "…": "...",
        " ": " ",
    }
)


# --------------------------------------------------------------------------- #
# Label helpers
# --------------------------------------------------------------------------- #


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def _humanize_token(value: Any) -> str:
    """``needs_review`` -> ``Needs review``. Port of humanizeToken in verdicts.ts."""
    words = [word for word in re.split(r"[_\s-]+", str(value or "")) if word]
    if not words:
        return "Unknown"
    first, *rest = words
    return " ".join([first[:1].upper() + first[1:].lower(), *(word.lower() for word in rest)])


def _verdict_label(verdict: Any) -> str:
    return _VERDICT_LABELS.get(_normalize(verdict)) or _humanize_token(verdict)


def _is_execution_incomplete(status: Any) -> bool:
    return _normalize(status) not in _SETTLED_EXECUTION_STATUSES


def _humanize_group(key: str | None) -> str:
    """Title case for group headings - ``balance_sheet`` -> ``Balance Sheet``."""
    if not key or key == _UNCATEGORIZED_KEY:
        return "Uncategorized"
    parts = [p for p in key.replace("-", " ").replace("_", " ").split() if p]
    return " ".join(part[:1].upper() + part[1:] for part in parts) or "Uncategorized"


def _group_key(item: Any) -> str:
    group = getattr(item, "group", None)
    return group.strip() if isinstance(group, str) and group.strip() else _UNCATEGORIZED_KEY


def _format_page_list(pages: Iterable[Any]) -> str:
    """``[2, 6, 7, 8]`` -> ``Pages 2, 6-8``. Empty input yields an empty string."""
    numbers = sorted({int(page) for page in pages if isinstance(page, int) and page > 0})
    if not numbers:
        return ""
    runs: list[tuple[int, int]] = []
    start = previous = numbers[0]
    for number in numbers[1:]:
        if number == previous + 1:
            previous = number
            continue
        runs.append((start, previous))
        start = previous = number
    runs.append((start, previous))
    label = "Page" if len(numbers) == 1 else "Pages"
    return f"{label} " + ", ".join(str(lo) if lo == hi else f"{lo}-{hi}" for lo, hi in runs)


def _page_locator_label(item: PageRuleAssessmentSchema) -> str:
    """Mirrors getLocatorLabel in RuleResultCard/behaviors.tsx.

    Scope wins over the page number on purpose. Broad-scope rules are pinned to an
    arbitrary page by the analyzer - `_analyze_broad_scope_rule` attributes the
    synthetic page result to the first gathered page - so a "Page N" label would
    misreport a document-wide finding as belonging to that one page.
    """
    scope = _normalize(getattr(item, "scope", "page"))
    if scope == "document":
        return "Whole document"
    if scope == "multi_page":
        return "Multiple pages"
    page = getattr(item, "page", None)
    return f"Page {page}" if isinstance(page, int) and page > 0 else ""


def _rule_locator_label(assessment: RuleAssessmentSchema) -> str:
    scope = _normalize(getattr(assessment, "scope", "page"))
    if scope == "document":
        return "Whole document"
    if scope == "multi_page":
        return "Multiple pages"
    return _format_page_list(getattr(assessment, "matched_pages", None) or [])


def _pdf_text(value: Any, fallback: str = "") -> str:
    text = fallback if value is None else str(value)
    sanitized = text.translate(_PDF_CHAR_REPLACEMENTS)
    return sanitized.encode("latin-1", errors="replace").decode("latin-1")


# --------------------------------------------------------------------------- #
# Report model
#
# The report is built from the per-page results, not from the rule-level
# `summary`. That field is a join artifact - the text analyzer joins page verdicts
# ("Page 2: fail | Page 3: pass"), the vision analyzer joins up to six page
# summaries into one run-on string - so it is neither prose nor page-attributed.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Occurrence:
    """One reportable result: a page that failed or needs review, or a rule that did not run."""

    locator: str
    status_label: str
    body: str
    not_run: bool
    verdict: str


@dataclass(frozen=True)
class _RuleSection:
    assessment: RuleAssessmentSchema
    occurrences: list[_Occurrence]
    not_run: bool


@dataclass(frozen=True)
class _GroupSection:
    title: str
    rules: list[_RuleSection]


def _select_occurrences(page_results: list[PageRuleAssessmentSchema]) -> list[_Occurrence]:
    occurrences: list[_Occurrence] = []
    for item in sorted(page_results, key=lambda i: getattr(i, "page", 0) or 0):
        if _is_execution_incomplete(item.execution_status):
            occurrences.append(
                _Occurrence(
                    locator=_page_locator_label(item),
                    status_label=_NOT_RUN_LABEL,
                    body=item.summary,
                    not_run=True,
                    verdict="",
                )
            )
        elif _normalize(item.verdict) in _ACTION_VERDICTS:
            occurrences.append(
                _Occurrence(
                    locator=_page_locator_label(item),
                    status_label=_verdict_label(item.verdict),
                    body=item.summary,
                    not_run=False,
                    verdict=_normalize(item.verdict),
                )
            )
    return occurrences


def _build_rule_section(
    assessment: RuleAssessmentSchema,
    page_results: list[PageRuleAssessmentSchema],
) -> _RuleSection | None:
    """Return the section for one rule, or None when the rule has nothing to report."""
    if _is_execution_incomplete(assessment.execution_status):
        # The rule as a whole never reached a verdict. Its page results are all
        # error records carrying the same message, so collapse to a single line.
        return _RuleSection(
            assessment=assessment,
            occurrences=[
                _Occurrence(
                    locator=_rule_locator_label(assessment),
                    status_label=_NOT_RUN_LABEL,
                    body=assessment.summary,
                    not_run=True,
                    verdict="",
                )
            ],
            not_run=True,
        )

    occurrences = _select_occurrences(page_results)

    if not occurrences and _normalize(assessment.verdict) in _ACTION_VERDICTS:
        # A broad-scope rule emits no page result at all when it gathered no pages,
        # so a purely page-driven report would drop it silently. Fall back to the
        # rule-level assessment rather than lose a finding.
        occurrences = [
            _Occurrence(
                locator=_rule_locator_label(assessment),
                status_label=_verdict_label(assessment.verdict),
                body=assessment.summary,
                not_run=False,
                verdict=_normalize(assessment.verdict),
            )
        ]

    if not occurrences:
        return None
    return _RuleSection(assessment=assessment, occurrences=occurrences, not_run=False)


def _build_report_groups(analysis: DocumentAnalysisSchema) -> list[_GroupSection]:
    page_results_by_rule: dict[str, list[PageRuleAssessmentSchema]] = {}
    for item in [*analysis.text_page_results, *analysis.visual_page_results]:
        page_results_by_rule.setdefault(item.rule_id, []).append(item)

    buckets: dict[str, list[_RuleSection]] = {}
    for assessment in analysis.rule_assessments:
        section = _build_rule_section(assessment, page_results_by_rule.get(assessment.rule_id, []))
        if section is None:
            continue
        buckets.setdefault(_group_key(assessment), []).append(section)

    keys = sorted(buckets.keys(), key=lambda k: (k == _UNCATEGORIZED_KEY, _humanize_group(k).lower()))
    return [_GroupSection(title=_humanize_group(key), rules=buckets[key]) for key in keys]


def _count_by(sections: Iterable[_RuleSection]) -> tuple[int, int, int]:
    """Return (failed, needs review, not run) rule counts."""
    sections = list(sections)
    not_run = sum(1 for section in sections if section.not_run)
    failed = sum(
        1
        for section in sections
        if not section.not_run and any(o.verdict == "fail" for o in section.occurrences)
    )
    return failed, len(sections) - failed - not_run, not_run


def _pluralize(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _headline(groups: list[_GroupSection]) -> str:
    failed, review, not_run = _count_by([rule for group in groups for rule in group.rules])
    parts = []
    if failed:
        parts.append(f"{_pluralize(failed, 'rule')} failed")
    if review:
        parts.append(f"{_pluralize(review, 'rule')} need review" if review != 1 else "1 rule needs review")
    if not_run:
        parts.append(f"{_pluralize(not_run, 'rule')} did not run")
    return ", ".join(parts) + "."


def _group_meta(group: _GroupSection) -> str:
    failed, review, not_run = _count_by(group.rules)
    parts = [_pluralize(len(group.rules), "rule")]
    if failed:
        parts.append(f"{failed} fail")
    if review:
        parts.append(f"{review} needs review")
    if not_run:
        parts.append(f"{not_run} not run")
    return " - ".join(parts)


def _rule_meta(section: _RuleSection) -> str:
    """Analysis type, then the offending locations only - passing pages never appear.

    The roster is dropped for a single-occurrence rule because the occurrence heading
    immediately below already names the same page and verdict.
    """
    parts = [_humanize_token(section.assessment.analysis_type)]
    if len(section.occurrences) > 1:
        parts.extend(
            f"{occurrence.locator}: {occurrence.status_label}" if occurrence.locator else occurrence.status_label
            for occurrence in section.occurrences
        )
    if getattr(section.assessment, "bypass", False):
        parts.append("Bypassed")
    return "  |  ".join(parts)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


class _ReportPdf(FPDF):
    """Thin FPDF subclass that adds a header/footer to every page."""

    doc_title: str = "Petra Vision Audit Report"

    def header(self) -> None:
        # Explicit x and absolute line coordinates: sections temporarily raise the
        # left margin to indent bodies, and a page break inside one must not drag
        # the header in with it.
        self.set_x(_MARGIN)
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*_COLOR_MUTED)
        self.cell(0, 8, self.doc_title, align="L")
        self.ln(10)
        self.set_draw_color(226, 232, 240)  # slate-200
        self.line(_MARGIN, self.get_y(), self.w - _MARGIN, self.get_y())
        self.ln(4)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_x(_MARGIN)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*_COLOR_FAINT)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")


def _ensure_space(pdf: _ReportPdf, needed_mm: float) -> None:
    """Start a new page rather than orphan a heading at the bottom of this one."""
    if pdf.will_page_break(needed_mm):
        pdf.add_page()


def _occurrence_color(occurrence: _Occurrence) -> tuple[int, int, int]:
    if occurrence.not_run:
        return _COLOR_MUTED  # deliberately neutral - "not run" is a status, not a verdict
    return _COLOR_FAIL if occurrence.verdict == "fail" else _COLOR_REVIEW


def _add_cover_sheet(pdf: _ReportPdf, req: ExportPdfRequest) -> None:
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 28)
    pdf.set_text_color(*_COLOR_HEADING)
    pdf.ln(30)
    pdf.cell(0, 14, "Petra Vision", align="C")
    pdf.ln(12)
    pdf.set_font("Helvetica", "", 18)
    pdf.set_text_color(71, 85, 105)  # slate-600
    pdf.cell(0, 10, "Audit Report", align="C")
    pdf.ln(20)

    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(71, 85, 105)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    bypassed_rules = [a for a in req.analysis.rule_assessments if getattr(a, "bypass", False)]
    bypassed_line = f"Rules bypassed: {len(bypassed_rules)}"
    if bypassed_rules:
        ids_preview = ", ".join(a.rule_id for a in bypassed_rules[:4])
        if len(bypassed_rules) > 4:
            ids_preview += f", +{len(bypassed_rules) - 4} more"
        bypassed_line += f"  ({ids_preview})"

    meta_lines = [
        f"Document: {_pdf_text(req.source_filename, 'Unknown')}",
        f"Document ID: {_pdf_text(req.document_id)}",
        f"Pages: {req.page_count}",
        f"Generated: {now}",
        f"Rules evaluated: {req.analysis.selected_rule_count}",
        bypassed_line,
    ]
    for line in meta_lines:
        pdf.cell(0, 7, _pdf_text(line), align="C")
        pdf.ln(7)

    if req.cover_sheet_text.strip():
        pdf.ln(12)
        pdf.set_draw_color(226, 232, 240)
        pdf.line(30, pdf.get_y(), pdf.w - 30, pdf.get_y())
        pdf.ln(8)
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(*_COLOR_HEADING)
        pdf.cell(0, 8, "Notes", align="L")
        pdf.ln(10)
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(*_COLOR_BODY)
        pdf.multi_cell(0, 6, _pdf_text(req.cover_sheet_text.strip()))


def _render_occurrence(pdf: _ReportPdf, occurrence: _Occurrence) -> None:
    _ensure_space(pdf, 20)

    heading = (
        f"{occurrence.locator} - {occurrence.status_label}" if occurrence.locator else occurrence.status_label
    )
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*_occurrence_color(occurrence))
    pdf.multi_cell(0, 5, _pdf_text(heading), new_x="LMARGIN", new_y="NEXT")

    body = (occurrence.body or "").strip()
    if body:
        pdf.set_font("Helvetica", "", 9.5)
        pdf.set_text_color(*_COLOR_BODY)
        # Full text width, never truncated. multi_cell measures and paginates the
        # text itself, so there is no row height to compute and get wrong.
        pdf.multi_cell(0, 5, _pdf_text(body), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2.5)


def _render_rule(pdf: _ReportPdf, section: _RuleSection) -> None:
    # Keep the rule heading, its meta line, and the first line of the first
    # occurrence together.
    _ensure_space(pdf, 30)

    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*_COLOR_HEADING)
    pdf.multi_cell(0, 6, _pdf_text(section.assessment.rule_name or section.assessment.rule_id), new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*_COLOR_MUTED)
    pdf.multi_cell(0, 5, _pdf_text(_rule_meta(section)), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1.5)

    pdf.set_left_margin(_MARGIN + _ENTRY_INDENT)
    pdf.set_x(_MARGIN + _ENTRY_INDENT)
    try:
        for occurrence in section.occurrences:
            _render_occurrence(pdf, occurrence)
    finally:
        pdf.set_left_margin(_MARGIN)
        pdf.set_x(_MARGIN)
    pdf.ln(3)


def _render_group(pdf: _ReportPdf, group: _GroupSection) -> None:
    _ensure_space(pdf, 34)

    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(*_COLOR_HEADING)
    pdf.cell(125, 8, _pdf_text(group.title))
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_COLOR_MUTED)
    pdf.cell(0, 8, _pdf_text(_group_meta(group)), align="R")
    pdf.ln(9)

    pdf.set_draw_color(*_COLOR_RULE_LINE)
    pdf.line(_MARGIN, pdf.get_y(), pdf.w - _MARGIN, pdf.get_y())
    pdf.ln(5)

    for section in group.rules:
        _render_rule(pdf, section)
    pdf.ln(2)


def _add_findings_section(pdf: _ReportPdf, req: ExportPdfRequest) -> None:
    pdf.add_page()
    groups = _build_report_groups(req.analysis)

    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(*_COLOR_HEADING)
    pdf.cell(0, 10, "Rules Requiring Action")
    pdf.ln(12)

    if not groups:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(71, 85, 105)
        pdf.multi_cell(
            0,
            6,
            _pdf_text("No issues requiring action - every evaluated rule passed or was not applicable."),
        )
        return

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(71, 85, 105)
    pdf.multi_cell(0, 6, _pdf_text(_headline(groups)), new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*_COLOR_FAINT)
    # State the filter explicitly: in an audit report a missing page must not be
    # read as a page that was never checked.
    pdf.multi_cell(
        0,
        5,
        _pdf_text(
            "Only rules with a failing or needs-review result are listed, and only the pages where "
            "that result occurred. Rules and pages that passed or were not applicable are omitted."
        ),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(5)

    for group in groups:
        _render_group(pdf, group)


@router.post("/pdf")
async def export_pdf(req: ExportPdfRequest) -> StreamingResponse:
    pdf = _ReportPdf(orientation="P", unit="mm", format="A4")
    pdf.alias_nb_pages()
    pdf.set_margins(_MARGIN, _MARGIN, _MARGIN)
    pdf.set_auto_page_break(auto=True, margin=20)

    pdf.doc_title = _pdf_text(f"Audit Report - {req.source_filename or req.document_id}")

    _add_cover_sheet(pdf, req)
    _add_findings_section(pdf, req)

    buffer = io.BytesIO(pdf.output())
    buffer.seek(0)

    safe_name = (req.source_filename or "report").rsplit(".", 1)[0]
    filename = f"{safe_name}-audit-report.pdf"

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
