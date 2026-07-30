from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from fpdf import FPDF

from src.schemas.export import ExportPdfRequest
from src.schemas.validation import PageRuleAssessmentSchema, RuleAssessmentSchema


router = APIRouter(prefix="/export", tags=["export"])


_VERDICT_LABELS = {
    "pass": "PASS",
    "fail": "FAIL",
    "needs_review": "NEEDS REVIEW",
    "not_applicable": "N/A",
    "skipped": "SKIPPED",
}

_UNCATEGORIZED_KEY = "__uncategorized__"

# Only these verdicts get the full write-up (reasoning + findings + citations +
# notes) in the per-page sections. Everything else collapses to a one-line
# summary, mirroring the frontend's collapsed-by-default cards and keeping the
# PDF from ballooning past the length of the audited document.
_DETAIL_VERDICTS = {"fail", "needs_review"}


def _humanize_group(key: str | None) -> str:
    if not key:
        return "Uncategorized"
    parts = [p for p in key.replace("-", " ").replace("_", " ").split() if p]
    return " ".join(part[:1].upper() + part[1:] for part in parts) or "Uncategorized"


def _group_key(item: Any) -> str:
    group = getattr(item, "group", None)
    return group.strip() if isinstance(group, str) and group.strip() else _UNCATEGORIZED_KEY

_PDF_CHAR_REPLACEMENTS = str.maketrans(
    {
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2022": "-",
        "\u2026": "...",
        "\u00a0": " ",
    }
)


class _ReportPdf(FPDF):
    """Thin FPDF subclass that adds a header/footer to every page."""

    doc_title: str = "Petra Vision Audit Report"

    def header(self) -> None:
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(100, 116, 139)  # slate-500
        self.cell(0, 8, self.doc_title, align="L")
        self.ln(10)
        self.set_draw_color(226, 232, 240)  # slate-200
        self.line(10, self.get_y(), self.w - 10, self.get_y())
        self.ln(4)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(148, 163, 184)  # slate-400
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")


def _verdict_label(verdict: str) -> str:
    return _VERDICT_LABELS.get(verdict, verdict.upper())


def _pdf_text(value: Any, fallback: str = "") -> str:
    if value is None:
        text = fallback
    else:
        text = str(value)
    sanitized = text.translate(_PDF_CHAR_REPLACEMENTS)
    return sanitized.encode("latin-1", errors="replace").decode("latin-1")


def _add_cover_sheet(pdf: _ReportPdf, req: ExportPdfRequest) -> None:
    pdf.add_page()

    # Large title
    pdf.set_font("Helvetica", "B", 28)
    pdf.set_text_color(15, 23, 42)  # slate-950
    pdf.ln(30)
    pdf.cell(0, 14, "Petra Vision", align="C")
    pdf.ln(12)
    pdf.set_font("Helvetica", "", 18)
    pdf.set_text_color(71, 85, 105)  # slate-600
    pdf.cell(0, 10, "Audit Report", align="C")
    pdf.ln(20)

    # Meta info
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

    # Cover sheet free text
    if req.cover_sheet_text.strip():
        pdf.ln(12)
        pdf.set_draw_color(226, 232, 240)
        pdf.line(30, pdf.get_y(), pdf.w - 30, pdf.get_y())
        pdf.ln(8)
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(0, 8, "Auditor Notes", align="L")
        pdf.ln(10)
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(51, 65, 85)  # slate-700
        pdf.multi_cell(0, 6, _pdf_text(req.cover_sheet_text.strip()))


def _add_summary_section(pdf: _ReportPdf, req: ExportPdfRequest) -> None:
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 10, "Executive Summary")
    pdf.ln(12)

    analysis = req.analysis

    # Overview metrics
    if analysis.overview:
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(71, 85, 105)
        pdf.cell(0, 7, "Overview Metrics")
        pdf.ln(8)
        pdf.set_font("Helvetica", "", 10)
        for metric in analysis.overview:
            detail = f"  ({_pdf_text(metric.detail)})" if metric.detail else ""
            pdf.cell(0, 6, _pdf_text(f"{metric.label}: {metric.value}{detail}"))
            pdf.ln(6)
        pdf.ln(6)

    # Rule assessment summary — grouped
    assessments = analysis.rule_assessments
    if assessments:
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(0, 7, "Group Summary")
        pdf.ln(8)
        _add_group_summary_table(pdf, assessments)
        pdf.ln(4)

        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(0, 7, "Rule Assessments by Group")
        pdf.ln(8)
        _add_grouped_assessment_tables(pdf, assessments)


def _add_group_summary_table(pdf: _ReportPdf, assessments: list[RuleAssessmentSchema]) -> None:
    """One row per group: Rules / Pass / Fail / N.R. / Bypass."""
    buckets: dict[str, list[RuleAssessmentSchema]] = {}
    for a in assessments:
        buckets.setdefault(_group_key(a), []).append(a)

    col_widths = [70, 18, 18, 18, 22, 22]
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(241, 245, 249)
    pdf.set_text_color(51, 65, 85)
    headers = ["Group", "Rules", "Pass", "Fail", "Review", "Bypass"]
    for w, label in zip(col_widths, headers):
        pdf.cell(w, 7, label, border=1, fill=True)
    pdf.ln(7)

    pdf.set_font("Helvetica", "", 9)
    # Sort: alphabetical but Uncategorized last.
    keys = sorted(buckets.keys(), key=lambda k: (k == _UNCATEGORIZED_KEY, _humanize_group(k).lower()))
    for key in keys:
        rules_in_group = buckets[key]
        total = len(rules_in_group)
        passes = sum(1 for a in rules_in_group if a.verdict == "pass")
        fails = sum(1 for a in rules_in_group if a.verdict == "fail")
        reviews = sum(1 for a in rules_in_group if a.verdict == "needs_review")
        bypassed = sum(1 for a in rules_in_group if getattr(a, "bypass", False))

        pdf.set_text_color(15, 23, 42)
        pdf.cell(col_widths[0], 6, _pdf_text(_humanize_group(key))[:40], border=1)
        pdf.cell(col_widths[1], 6, str(total), border=1, align="C")
        pdf.set_text_color(4, 120, 87) if passes else pdf.set_text_color(15, 23, 42)
        pdf.cell(col_widths[2], 6, str(passes), border=1, align="C")
        pdf.set_text_color(190, 18, 60) if fails else pdf.set_text_color(15, 23, 42)
        pdf.cell(col_widths[3], 6, str(fails), border=1, align="C")
        pdf.set_text_color(146, 64, 14) if reviews else pdf.set_text_color(15, 23, 42)
        pdf.cell(col_widths[4], 6, str(reviews), border=1, align="C")
        pdf.set_text_color(146, 64, 14) if bypassed else pdf.set_text_color(15, 23, 42)
        pdf.cell(col_widths[5], 6, str(bypassed), border=1, align="C")
        pdf.ln(6)


def _add_grouped_assessment_tables(pdf: _ReportPdf, assessments: list[RuleAssessmentSchema]) -> None:
    """Per-group sub-heading followed by its assessments table."""
    buckets: dict[str, list[RuleAssessmentSchema]] = {}
    for a in assessments:
        buckets.setdefault(_group_key(a), []).append(a)

    keys = sorted(buckets.keys(), key=lambda k: (k == _UNCATEGORIZED_KEY, _humanize_group(k).lower()))
    for key in keys:
        group_rules = buckets[key]
        if pdf.get_y() > pdf.h - 40:
            pdf.add_page()

        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(15, 23, 42)
        fails = sum(1 for a in group_rules if a.verdict == "fail")
        bypassed = sum(1 for a in group_rules if getattr(a, "bypass", False))
        heading = f"{_humanize_group(key)}  -  {len(group_rules)} rule(s)"
        if fails:
            heading += f" - {fails} fail"
        if bypassed:
            heading += f" - {bypassed} bypassed"
        pdf.cell(0, 7, _pdf_text(heading))
        pdf.ln(8)

        _add_assessment_table(pdf, group_rules)
        pdf.ln(4)


def _add_assessment_table(pdf: _ReportPdf, assessments: list[RuleAssessmentSchema]) -> None:
    # name, type, verdict, bypass, summary
    col_widths = [60, 20, 22, 18, pdf.w - 20 - 60 - 20 - 22 - 18]

    # Header
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(241, 245, 249)  # slate-100
    pdf.set_text_color(51, 65, 85)
    pdf.cell(col_widths[0], 7, "Rule", border=1, fill=True)
    pdf.cell(col_widths[1], 7, "Type", border=1, fill=True)
    pdf.cell(col_widths[2], 7, "Verdict", border=1, fill=True)
    pdf.cell(col_widths[3], 7, "Bypass", border=1, fill=True)
    pdf.cell(col_widths[4], 7, "Summary", border=1, fill=True)
    pdf.ln(7)

    # Rows
    pdf.set_font("Helvetica", "", 9)
    for a in assessments:
        pdf.set_text_color(15, 23, 42)
        y_start = pdf.get_y()

        # Calculate row height based on summary text
        summary_text = _pdf_text(a.summary or "-")
        summary_width = col_widths[4] - 2
        n_lines = max(1, len(summary_text) // int(summary_width * 0.45) + 1)
        row_h = max(7, n_lines * 5)

        pdf.cell(col_widths[0], row_h, _pdf_text(a.rule_name or a.rule_id)[:35], border=1)
        pdf.cell(col_widths[1], row_h, _pdf_text(a.analysis_type), border=1)

        # Verdict colored
        v = a.verdict
        if v == "pass":
            pdf.set_text_color(4, 120, 87)
        elif v == "fail":
            pdf.set_text_color(190, 18, 60)
        else:
            pdf.set_text_color(146, 64, 14)
        pdf.cell(col_widths[2], row_h, _pdf_text(_verdict_label(v)), border=1)

        # Bypass cell — amber "YES" if bypassed, "-" otherwise
        bypassed_flag = bool(getattr(a, "bypass", False))
        if bypassed_flag:
            pdf.set_text_color(146, 64, 14)
            bypass_text = "YES"
        else:
            pdf.set_text_color(148, 163, 184)
            bypass_text = "-"
        pdf.cell(col_widths[3], row_h, bypass_text, border=1, align="C")

        pdf.set_text_color(15, 23, 42)
        pdf.multi_cell(col_widths[4], 5, summary_text[:200], border=1)
        expected_y = y_start + row_h
        if pdf.get_y() < expected_y:
            pdf.set_y(expected_y)


def _add_page_results_section(
    pdf: _ReportPdf,
    title: str,
    items: list[PageRuleAssessmentSchema],
) -> None:
    if not items:
        return

    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 10, title)
    pdf.ln(10)

    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(148, 163, 184)
    pdf.multi_cell(
        0,
        4,
        _pdf_text(
            "Full detail (reasoning, findings, citations, notes) is shown for Fail and "
            "Needs Review rules. Passing and not-applicable rules are condensed to a summary line."
        ),
    )
    pdf.ln(4)

    # Group by page
    pages: dict[int, list[PageRuleAssessmentSchema]] = {}
    for item in items:
        pages.setdefault(item.page, []).append(item)

    for page_num in sorted(pages.keys()):
        page_items = pages[page_num]

        # Check if we need a new page (leave room)
        if pdf.get_y() > pdf.h - 60:
            pdf.add_page()

        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(0, 8, f"Page {page_num}")
        pdf.ln(8)

        for item in page_items:
            _add_single_result(pdf, item)
            pdf.ln(4)


def _add_compact_result(pdf: _ReportPdf, item: PageRuleAssessmentSchema) -> None:
    """One-line entry for passing / not-applicable rules: verdict + name + short summary.

    Keeps every rule visible in the report without the full reasoning/findings/
    citations/notes write-up, which is reserved for fail / needs-review verdicts.
    """
    if pdf.get_y() > pdf.h - 20:
        pdf.add_page()

    v = item.verdict
    if v == "pass":
        pdf.set_text_color(4, 120, 87)
    elif v == "not_applicable":
        pdf.set_text_color(100, 116, 139)
    else:
        pdf.set_text_color(146, 64, 14)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(26, 5, _pdf_text(_verdict_label(v)))

    pdf.set_text_color(15, 23, 42)
    pdf.set_font("Helvetica", "B", 9)
    name = _pdf_text(item.rule_name or item.rule_id)
    pdf.cell(0, 5, name)
    pdf.ln(5)

    if item.summary:
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(100, 116, 139)
        pdf.set_x(26)
        pdf.multi_cell(pdf.w - 20 - 26, 4, _pdf_text(item.summary)[:200])
    pdf.ln(1)


def _add_single_result(pdf: _ReportPdf, item: PageRuleAssessmentSchema) -> None:
    # Passing / N/A / skipped rules get a compact one-liner; only fail and
    # needs-review get the full write-up below.
    if item.verdict not in _DETAIL_VERDICTS:
        _add_compact_result(pdf, item)
        return

    if pdf.get_y() > pdf.h - 50:
        pdf.add_page()

    # Rule name + verdict
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(15, 23, 42)
    name = _pdf_text(item.rule_name or item.rule_id)
    pdf.cell(0, 6, name)
    pdf.ln(6)

    # Verdict + type badges
    pdf.set_font("Helvetica", "B", 9)
    v = item.verdict
    if v == "pass":
        pdf.set_text_color(4, 120, 87)
    elif v == "fail":
        pdf.set_text_color(190, 18, 60)
    else:
        pdf.set_text_color(146, 64, 14)
    pdf.cell(30, 5, _pdf_text(_verdict_label(v)))

    pdf.set_text_color(100, 116, 139)
    pdf.cell(20, 5, _pdf_text(item.analysis_type.upper()))
    pdf.cell(30, 5, _pdf_text(item.execution_status.upper()))
    if getattr(item, "bypass", False):
        pdf.set_text_color(146, 64, 14)  # amber-700
        pdf.cell(30, 5, "BYPASSED")
    pdf.ln(7)

    if getattr(item, "bypass", False):
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(146, 64, 14)
        pdf.multi_cell(
            0,
            4,
            _pdf_text(
                "This rule was marked bypassable for this run - findings are surfaced but not gating."
            ),
        )
        pdf.ln(1)

    # Summary
    if item.summary:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(0, 5, "Summary:")
        pdf.ln(5)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(71, 85, 105)
        pdf.multi_cell(0, 5, _pdf_text(item.summary))
        pdf.ln(2)

    # Reasoning
    if item.reasoning:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(0, 5, "Reasoning:")
        pdf.ln(5)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(71, 85, 105)
        pdf.multi_cell(0, 5, _pdf_text(item.reasoning))
        pdf.ln(2)

    # Findings
    if item.findings:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(0, 5, "Findings:")
        pdf.ln(5)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(71, 85, 105)
        for finding in item.findings:
            pdf.multi_cell(0, 5, _pdf_text(f"  - {finding}"))
            pdf.ln(1)
        pdf.ln(2)

    # Citations
    if item.citations:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(0, 5, "Citations:")
        pdf.ln(5)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(71, 85, 105)
        for cit in item.citations:
            pdf.multi_cell(0, 5, _pdf_text(f"  Page {cit.page}: {cit.evidence}"))
            pdf.ln(1)
        pdf.ln(2)

    # Notes
    if item.notes:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(0, 5, "Notes:")
        pdf.ln(5)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(71, 85, 105)
        for note in item.notes:
            pdf.multi_cell(0, 5, _pdf_text(f"  - {note}"))
            pdf.ln(1)

    # Divider
    pdf.set_draw_color(226, 232, 240)
    pdf.line(10, pdf.get_y() + 2, pdf.w - 10, pdf.get_y() + 2)
    pdf.ln(5)


@router.post("/pdf")
async def export_pdf(req: ExportPdfRequest) -> StreamingResponse:
    pdf = _ReportPdf(orientation="P", unit="mm", format="A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)

    title = _pdf_text(f"Audit Report - {req.source_filename or req.document_id}")
    pdf.doc_title = title

    # 1. Cover sheet
    _add_cover_sheet(pdf, req)

    # 2. Executive summary with assessment table
    _add_summary_section(pdf, req)

    # 3. Text rule page-level results
    _add_page_results_section(pdf, "Text Rule Assessments By Page", req.analysis.text_page_results)

    # 4. Visual rule page-level results
    _add_page_results_section(pdf, "Visual Rule Assessments By Page", req.analysis.visual_page_results)

    # Output
    pdf_bytes = pdf.output()
    buffer = io.BytesIO(pdf_bytes)
    buffer.seek(0)

    safe_name = (req.source_filename or "report").rsplit(".", 1)[0]
    filename = f"{safe_name}-audit-report.pdf"

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
