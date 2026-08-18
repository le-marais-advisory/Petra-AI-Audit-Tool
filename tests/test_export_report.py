from __future__ import annotations

import io

import pdfplumber

from src.api.routers.export import (
    _NOT_RUN_LABEL,
    _ReportPdf,
    _add_findings_section,
    _build_report_groups,
    _format_page_list,
    _humanize_token,
    _rule_meta,
    _verdict_label,
)
from src.schemas.export import ExportPdfRequest
from src.schemas.validation import (
    DocumentAnalysisSchema,
    PageRuleAssessmentSchema,
    RuleAssessmentSchema,
)


def _rule(
    rule_id: str = "BS-FMT",
    *,
    name: str = "Balance Sheet Formatting",
    verdict: str = "fail",
    group: str | None = "balance_sheet",
    execution_status: str = "completed",
    scope: str = "page",
    summary: str = "",
    matched_pages: list[int] | None = None,
    analysis_type: str = "text",
) -> RuleAssessmentSchema:
    return RuleAssessmentSchema(
        rule_id=rule_id,
        rule_name=name,
        analysis_type=analysis_type,
        scope=scope,
        execution_status=execution_status,
        verdict=verdict,
        summary=summary,
        group=group,
        matched_pages=matched_pages or [],
    )


def _page_result(
    page: int,
    *,
    rule_id: str = "BS-FMT",
    name: str = "Balance Sheet Formatting",
    verdict: str = "pass",
    summary: str = "",
    execution_status: str = "completed",
    scope: str = "page",
    analysis_type: str = "text",
) -> PageRuleAssessmentSchema:
    return PageRuleAssessmentSchema(
        page=page,
        rule_id=rule_id,
        rule_name=name,
        analysis_type=analysis_type,
        scope=scope,
        execution_status=execution_status,
        verdict=verdict,
        summary=summary,
    )


def _analysis(**kwargs) -> DocumentAnalysisSchema:
    return DocumentAnalysisSchema(**kwargs)


# --------------------------------------------------------------------------- #
# Labels
# --------------------------------------------------------------------------- #


def test_humanize_token_produces_sentence_case():
    assert _humanize_token("not_applicable") == "Not applicable"
    assert _humanize_token("needs_review") == "Needs review"
    assert _humanize_token("text") == "Text"
    assert _humanize_token("") == "Unknown"


def test_verdict_labels_are_sentence_case():
    assert _verdict_label("pass") == "Pass"
    assert _verdict_label("fail") == "Fail"
    assert _verdict_label("needs_review") == "Needs review"
    assert _verdict_label("not_applicable") == "Not applicable"


def test_verdict_label_falls_back_to_humanized_token():
    assert _verdict_label("some_new_verdict") == "Some new verdict"


def test_format_page_list_collapses_runs():
    assert _format_page_list([2, 6, 7, 8]) == "Pages 2, 6-8"
    assert _format_page_list([4]) == "Page 4"
    assert _format_page_list([]) == ""


# --------------------------------------------------------------------------- #
# Filtering
# --------------------------------------------------------------------------- #


def test_passing_rule_is_omitted_entirely():
    analysis = _analysis(
        rule_assessments=[_rule(verdict="pass")],
        text_page_results=[_page_result(2, verdict="pass"), _page_result(3, verdict="not_applicable")],
    )
    assert _build_report_groups(analysis) == []


def test_group_is_dropped_when_none_of_its_rules_report():
    analysis = _analysis(
        rule_assessments=[
            _rule("BS-FMT", verdict="fail"),
            _rule("BS-EQ", name="Balance Sheet Equation", verdict="pass"),
            _rule("SOI-FMT", name="SOI Formatting", verdict="pass", group="schedule_of_investments"),
        ],
        text_page_results=[
            _page_result(2, rule_id="BS-FMT", verdict="fail", summary="Missing double underline."),
            _page_result(3, rule_id="BS-EQ", verdict="pass"),
            _page_result(4, rule_id="SOI-FMT", verdict="pass"),
        ],
    )
    groups = _build_report_groups(analysis)

    assert [group.title for group in groups] == ["Balance Sheet"]
    assert [rule.assessment.rule_id for rule in groups[0].rules] == ["BS-FMT"]


def test_only_offending_pages_are_listed():
    analysis = _analysis(
        rule_assessments=[_rule(verdict="fail", matched_pages=[2, 3, 6, 7, 8])],
        text_page_results=[
            _page_result(2, verdict="fail", summary="Page two failed."),
            _page_result(3, verdict="pass"),
            _page_result(6, verdict="not_applicable"),
            _page_result(7, verdict="needs_review", summary="Page seven is unclear."),
            _page_result(8, verdict="pass"),
        ],
    )
    section = _build_report_groups(analysis)[0].rules[0]

    assert [(o.locator, o.status_label) for o in section.occurrences] == [
        ("Page 2", "Fail"),
        ("Page 7", "Needs review"),
    ]
    assert _rule_meta(section) == "Text  |  Page 2: Fail  |  Page 7: Needs review"


def test_single_occurrence_rule_omits_the_redundant_roster():
    # The occurrence heading below the meta line already names the page and verdict.
    analysis = _analysis(
        rule_assessments=[_rule(verdict="fail")],
        text_page_results=[_page_result(2, verdict="fail", summary="Broken."), _page_result(3, verdict="pass")],
    )
    section = _build_report_groups(analysis)[0].rules[0]

    assert _rule_meta(section) == "Text"
    assert (section.occurrences[0].locator, section.occurrences[0].status_label) == ("Page 2", "Fail")


def test_summary_is_carried_through_untruncated():
    body = "A. " + ("long finding text " * 200)
    analysis = _analysis(
        rule_assessments=[_rule(verdict="fail")],
        text_page_results=[_page_result(2, verdict="fail", summary=body)],
    )
    section = _build_report_groups(analysis)[0].rules[0]

    assert section.occurrences[0].body == body


# --------------------------------------------------------------------------- #
# Execution status
# --------------------------------------------------------------------------- #


def test_errored_rule_shows_not_run_instead_of_its_fallback_verdict():
    # The analyzers stamp `needs_review` on rules that crashed; reporting that as a
    # verdict would present an infrastructure failure as an audit finding.
    analysis = _analysis(
        rule_assessments=[
            _rule(verdict="needs_review", execution_status="error", summary="Text analysis failed: timeout")
        ],
        text_page_results=[
            _page_result(2, verdict="needs_review", execution_status="error", summary="Text analysis failed: timeout")
        ],
    )
    section = _build_report_groups(analysis)[0].rules[0]

    assert section.not_run is True
    assert [o.status_label for o in section.occurrences] == [_NOT_RUN_LABEL]
    assert "Fail" not in _rule_meta(section)
    assert "Needs review" not in _rule_meta(section)


def test_errored_rule_collapses_to_a_single_line():
    analysis = _analysis(
        rule_assessments=[_rule(verdict="needs_review", execution_status="error", summary="Boom")],
        text_page_results=[
            _page_result(page, verdict="needs_review", execution_status="error", summary="Boom")
            for page in (2, 3, 4)
        ],
    )
    section = _build_report_groups(analysis)[0].rules[0]

    assert len(section.occurrences) == 1


def test_not_applicable_rule_is_settled_and_omitted():
    analysis = _analysis(
        rule_assessments=[_rule(verdict="not_applicable", execution_status="not_applicable")],
    )
    assert _build_report_groups(analysis) == []


# --------------------------------------------------------------------------- #
# Scope
# --------------------------------------------------------------------------- #


def test_broad_scope_rule_is_not_labelled_with_its_synthetic_page():
    # `_analyze_broad_scope_rule` pins the synthetic page result to the first
    # gathered page; labelling it "Page 3" would misreport a document-wide finding.
    analysis = _analysis(
        rule_assessments=[_rule(verdict="fail", scope="document", matched_pages=[3, 4, 5])],
        text_page_results=[
            _page_result(3, verdict="fail", scope="document", summary="Dates are inconsistent across the document.")
        ],
    )
    section = _build_report_groups(analysis)[0].rules[0]

    assert section.occurrences[0].locator == "Whole document"


def test_broad_scope_rule_without_page_results_still_reports():
    analysis = _analysis(
        rule_assessments=[
            _rule(verdict="fail", scope="document", summary="Document-wide failure.", matched_pages=[])
        ],
        text_page_results=[],
    )
    section = _build_report_groups(analysis)[0].rules[0]

    assert section.occurrences[0].body == "Document-wide failure."
    assert section.occurrences[0].status_label == "Fail"


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _render(analysis: DocumentAnalysisSchema) -> pdfplumber.PDF:
    pdf = _ReportPdf(orientation="P", unit="mm", format="A4")
    pdf.alias_nb_pages()
    pdf.set_margins(10.0, 10.0, 10.0)
    pdf.set_auto_page_break(auto=True, margin=20)
    _add_findings_section(pdf, ExportPdfRequest(document_id="doc", analysis=analysis))
    return pdfplumber.open(io.BytesIO(pdf.output()))


def test_long_summary_renders_compactly_and_in_full():
    # Regression: a ~2,000 character summary previously drove a computed row height
    # taller than the page, spilling one table column onto each of four pages.
    tail = "and this is the very last sentence of the summary."
    body = ("The statement of operations carries a single rule beneath its final total. " * 25) + tail
    analysis = _analysis(
        rule_assessments=[_rule("FMT-DOUBLE-UNDERLINE", name="Double Underline", verdict="fail", analysis_type="vision")],
        visual_page_results=[
            _page_result(3, rule_id="FMT-DOUBLE-UNDERLINE", name="Double Underline", verdict="fail", summary=body, analysis_type="vision")
        ],
    )

    with _render(analysis) as doc:
        assert len(doc.pages) <= 2
        rendered = " ".join(" ".join((page.extract_text() or "").split()) for page in doc.pages)

    assert tail in rendered


def test_many_findings_paginate_without_orphaned_content():
    analysis = _analysis(
        rule_assessments=[
            _rule(f"RULE-{i}", name=f"Rule Number {i}", verdict="fail", group=f"group_{i % 3}")
            for i in range(12)
        ],
        text_page_results=[
            _page_result(2, rule_id=f"RULE-{i}", name=f"Rule Number {i}", verdict="fail", summary="Finding text. " * 40)
            for i in range(12)
        ],
    )

    with _render(analysis) as doc:
        page_count = len(doc.pages)
        rendered = " ".join(" ".join((page.extract_text() or "").split()) for page in doc.pages)

    assert page_count <= 8
    for i in range(12):
        assert f"Rule Number {i}" in rendered


def test_report_states_the_filter_it_applied():
    analysis = _analysis(
        rule_assessments=[_rule(verdict="fail")],
        text_page_results=[_page_result(2, verdict="fail", summary="Broken.")],
    )
    with _render(analysis) as doc:
        rendered = " ".join(" ".join((page.extract_text() or "").split()) for page in doc.pages)

    assert "Only rules with a failing or needs-review result are listed" in rendered
