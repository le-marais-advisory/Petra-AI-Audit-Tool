from __future__ import annotations

import re

from src.schemas.validation import DocumentAnalysisSchema, DocumentValidationResponse


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _keywords_from_rule(rule: dict) -> list[str]:
    raw = " ".join(
        [
            rule.get("name", ""),
            rule.get("query", ""),
            rule.get("description", ""),
            rule.get("acceptance_criteria", ""),
        ]
    )
    tokens = re.findall(r"[a-zA-Z]{4,}", raw.lower())
    seen: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.append(token)
    return seen[:12]


def _fallback_rule_assessment(rule: dict, pages: list[dict]) -> dict:
    keywords = _keywords_from_rule(rule)
    matched_pages: list[int] = []
    notes: list[str] = []
    for page in pages:
        page_text = _normalize(page.get("text", ""))
        if any(keyword in page_text for keyword in keywords):
            matched_pages.append(page.get("page", 0))
    if matched_pages:
        notes.append(f"Keyword overlap found on page(s): {', '.join(str(page) for page in matched_pages)}.")
    else:
        notes.append("No obvious keyword overlap found in extracted text.")
    if keywords:
        notes.append(f"Keywords used: {', '.join(keywords[:6])}.")
    return {
        "rule_id": rule.get("id", ""),
        "rule_name": rule.get("name", rule.get("id", "")),
        "analysis_type": rule.get("analysis_type", "text"),
        "execution_status": "completed",
        "verdict": "needs_review",
        "summary": "Fallback heuristic analysis based on keyword overlap.",
        "reasoning": "No LLM assessment was supplied, so the result was derived from keyword overlap in extracted text.",
        "findings": [],
        "citations": [],
        "matched_pages": matched_pages,
        "notes": notes,
    }


def _enrich_with_rule_metadata(items: list[dict], rule_lookup: dict[str, dict]) -> list[dict]:
    """Stamp group/bypassable/bypass onto each assessment dict from the originating rule."""
    enriched: list[dict] = []
    for item in items:
        rule_id = item.get("rule_id") or ""
        meta = rule_lookup.get(rule_id, {})
        merged = dict(item)
        merged.setdefault("group", meta.get("group"))
        merged.setdefault("bypassable", bool(meta.get("bypassable", False)))
        merged.setdefault("bypass", bool(meta.get("bypass", False)))
        enriched.append(merged)
    return enriched


def _format_duration(seconds: float) -> str:
    if seconds >= 60:
        minutes, secs = divmod(int(round(seconds)), 60)
        return f"{minutes}m {secs}s"
    return f"{seconds:.1f}s"


def build_document_analysis(
    pages: list[dict],
    selected_rules: list[dict] | None = None,
    rule_assessments: list[dict] | None = None,
    text_page_results: list[dict] | None = None,
    visual_page_results: list[dict] | None = None,
    elapsed_seconds: float | None = None,
) -> dict:
    total_chars = sum(page.get("char_count", 0) for page in pages)
    total_tables = sum(len(page.get("tables", [])) for page in pages)
    pages_with_text = sum(1 for page in pages if (page.get("text") or "").strip())
    pages_with_tables = sum(1 for page in pages if page.get("tables"))
    selected_rules = selected_rules or []
    text_rule_count = sum(1 for rule in selected_rules if rule.get("analysis_type", "text") == "text")
    vision_rule_count = sum(1 for rule in selected_rules if rule.get("analysis_type", "text") == "vision")
    rule_assessments = rule_assessments or [_fallback_rule_assessment(rule, pages) for rule in selected_rules]

    rule_lookup = {rule.get("id", ""): rule for rule in selected_rules if rule.get("id")}
    rule_assessments = _enrich_with_rule_metadata(rule_assessments, rule_lookup)
    text_page_results = _enrich_with_rule_metadata(text_page_results or [], rule_lookup)
    visual_page_results = _enrich_with_rule_metadata(visual_page_results or [], rule_lookup)
    bypassed_rule_count = sum(1 for r in selected_rules if r.get("bypass"))

    completed_text_rule_count = sum(
        1
        for item in rule_assessments
        if item.get("analysis_type") == "text" and item.get("execution_status") == "completed"
    )

    overview = [
        {"label": "Pages", "value": str(len(pages)), "detail": "Total pages processed from the PDF."},
        {"label": "Pages With Text", "value": str(pages_with_text), "detail": "Pages where pdfplumber extracted non-empty text."},
        {"label": "Pages With Tables", "value": str(pages_with_tables), "detail": "Pages where at least one table was detected."},
        {"label": "Total Characters", "value": str(total_chars), "detail": "Combined extracted character count across all pages."},
        {"label": "Total Tables", "value": str(total_tables), "detail": "Combined number of extracted tables."},
        {"label": "Selected Rules", "value": str(len(selected_rules)), "detail": "Rules enabled for this analysis run."},
        {"label": "Text Rules", "value": str(text_rule_count), "detail": "Rules intended for text/content analysis."},
        {"label": "Vision Rules", "value": str(vision_rule_count), "detail": "Rules intended for image-based analysis."},
        {"label": "Text Rules Completed", "value": str(completed_text_rule_count), "detail": "Text rules evaluated by the LLM in this run."},
        {"label": "Rules Bypassed", "value": str(bypassed_rule_count), "detail": "Rules flagged bypassable and marked bypass for this run."},
    ]

    # Surface timing so slow rules can be identified and pruned (latency analysis).
    if elapsed_seconds is not None:
        overview.insert(
            0,
            {
                "label": "Processing Time",
                "value": _format_duration(elapsed_seconds),
                "detail": "Total wall-clock time for extraction and all rule evaluations.",
            },
        )
    timed_rules = sorted(
        (item for item in rule_assessments if item.get("duration_ms") is not None),
        key=lambda item: item.get("duration_ms") or 0,
        reverse=True,
    )
    if timed_rules:
        slowest = timed_rules[0]
        slowest_name = slowest.get("rule_name") or slowest.get("rule_id") or "unknown"
        top = ", ".join(
            f"{(item.get('rule_name') or item.get('rule_id') or 'unknown')} ({(item.get('duration_ms') or 0) / 1000:.1f}s)"
            for item in timed_rules[:3]
        )
        overview.append(
            {
                "label": "Slowest Rule",
                "value": f"{(slowest.get('duration_ms') or 0) / 1000:.1f}s",
                "detail": f"Highest total LLM time. Top rules by time: {top}.",
            }
        )

    page_observations = []
    for page in pages:
        text = (page.get("text") or "").strip()
        tables = page.get("tables", [])
        observations: list[str] = []
        if text:
            first_line = text.splitlines()[0].strip()
            if first_line:
                observations.append(f"Starts with: {first_line[:160]}")
        else:
            observations.append("No text extracted from this page.")

        if tables:
            observations.append(f"{len(tables)} table(s) detected.")
        else:
            observations.append("No tables detected.")

        page_observations.append({"page": page.get("page", 0), "observations": observations})

    return DocumentAnalysisSchema(
        overview=overview,
        selected_rule_count=len(selected_rules),
        text_rule_count=text_rule_count,
        vision_rule_count=vision_rule_count,
        rule_assessments=rule_assessments,
        text_page_results=text_page_results or [],
        visual_page_results=visual_page_results or [],
        page_observations=page_observations,
    ).model_dump()


def build_document_result(
    document_id: str,
    pages: list[dict],
    source_filename: str | None = None,
    selected_rules: list[dict] | None = None,
    rule_assessments: list[dict] | None = None,
    text_page_results: list[dict] | None = None,
    visual_page_results: list[dict] | None = None,
    elapsed_seconds: float | None = None,
) -> dict:
    return DocumentValidationResponse(
        document_id=document_id,
        page_count=len(pages),
        source_filename=source_filename,
        analysis=build_document_analysis(
            pages,
            selected_rules=selected_rules,
            rule_assessments=rule_assessments,
            text_page_results=text_page_results,
            visual_page_results=visual_page_results,
            elapsed_seconds=elapsed_seconds,
        ),
        pages=pages,
    ).model_dump()
