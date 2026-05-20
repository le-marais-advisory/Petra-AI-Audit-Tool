from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

from src.core.config import AppYaml, Settings
from src.core.prompting import load_prompt
from src.pipeline.page_classifier import rule_applies_to_page, SECTION_TO_KEY
from src.providers.text.factory import build_text_provider

logger = logging.getLogger("petra.pipeline.text")


TEXT_PROMPT_PATH = "config/text_analysis_system_prompt.md"


def _page_blob(page: dict) -> str:
    text = (page.get("text") or "").strip() or "No text extracted."
    return f"## Page {page.get('page', '?')}\nPlain Extracted Text:\n{text}"


def _tables_blob(page: dict) -> str:
    tables = page.get("tables") or []
    if not tables:
        return "No extracted tables."

    rendered_tables: list[str] = []
    for table in tables[:3]:
        rows = table.get("rows") or []
        rendered_rows: list[str] = []
        for row in rows[:12]:
            normalized_cells = [str(cell or "").strip() or "-" for cell in row]
            rendered_rows.append(" | ".join(normalized_cells))
        if len(rows) > 12:
            rendered_rows.append(f"... {len(rows) - 12} additional row(s) omitted.")
        rendered_tables.append(
            f"Table {table.get('index', '?')}:\n" + ("\n".join(rendered_rows) if rendered_rows else "No extracted rows.")
        )

    if len(tables) > 3:
        rendered_tables.append(f"... {len(tables) - 3} additional table(s) omitted.")
    return "\n\n".join(rendered_tables)


_NUMERIC_RULE_ID_PREFIXES = ("NUM-", "BS-", "OPS-", "SCF-", "ARITH-")


def _rule_prefers_table_numbers(rule: dict) -> bool:
    rule_id = str(rule.get("id", "")).upper()
    return any(rule_id.startswith(prefix) for prefix in _NUMERIC_RULE_ID_PREFIXES)


def _rule_needs_layout_context(rule: dict) -> bool:
    rule_id = str(rule.get("id", "")).upper()
    if rule_id == "FMT-HEADINGS":
        return True

    haystack = " ".join(
        [
            str(rule.get("name", "")),
            str(rule.get("query", "")),
            str(rule.get("description", "")),
            str(rule.get("acceptance_criteria", "")),
        ]
    ).lower()
    keywords = (
        "header",
        "heading",
        "center",
        "centred",
        "centered",
        "align",
        "alignment",
        "layout",
        "spacing",
        "cut off",
        "cutoff",
        "misprint",
    )
    return any(keyword in haystack for keyword in keywords)


def _layout_blob(page: dict) -> str:
    layout_summary = page.get("layout_summary") or {}
    top_lines = layout_summary.get("top_lines") or []
    if not top_lines:
        return "No positional line metadata available."

    return json.dumps(
        {
            "page_width": layout_summary.get("page_width", 0.0),
            "page_height": layout_summary.get("page_height", 0.0),
            "alignment_reference": layout_summary.get("alignment_reference", {}),
            "top_lines": top_lines,
        },
        indent=2,
        ensure_ascii=True,
    )


def _serialize_page_content(page: dict, rule: dict) -> str:
    sections = [
        _page_blob(page),
        "Extracted Tables:\n" + _tables_blob(page),
    ]
    if _rule_needs_layout_context(rule):
        sections.append("Layout Metadata:\n" + _layout_blob(page))
    if _rule_prefers_table_numbers(rule):
        sections.append(
            "Arithmetic note: when verifying totals or cross-footing, source all numeric values from "
            "the Extracted Tables block above rather than the Plain Extracted Text. "
            "The plain text uses layout-preserved spacing that can cause a single number to appear split "
            "across tokens; the table cells contain each value as a single parsed string."
        )
    return "\n\n".join(section for section in sections if section.strip())


def _build_skipped_result(rule: dict, message: str, execution_status: str = "skipped", page: int | None = None) -> dict:
    return {
        "page": page,
        "rule_id": rule.get("id", ""),
        "rule_name": rule.get("name", rule.get("id", "")),
        "analysis_type": "text",
        "execution_status": execution_status,
        "verdict": "needs_review",
        "summary": message,
        "reasoning": message,
        "findings": [],
        "citations": [],
        "matched_pages": [],
        "notes": [message],
    }


def _build_not_applicable_rule_result(rule: dict, reason: str) -> dict:
    return {
        "rule_id": rule.get("id", ""),
        "rule_name": rule.get("name", rule.get("id", "")),
        "analysis_type": "text",
        "scope": rule.get("scope", "page"),
        "execution_status": "not_applicable",
        "verdict": "not_applicable",
        "summary": reason,
        "reasoning": reason,
        "findings": [],
        "citations": [],
        "matched_pages": [],
        "notes": [reason],
    }


def _pages_for_section(section_name: str, pages: list[dict]) -> list[dict]:
    key = SECTION_TO_KEY.get(re.sub(r"\s+", " ", section_name.lower()).strip())
    if key is None:
        return []
    return [p for p in pages if key in (p.get("page_type") or [])]


def _serialize_broad_scope_content(pages: list[dict], rule: dict, group_by_section: bool = False) -> str:
    if not group_by_section:
        parts: list[str] = []
        for page in pages:
            page_num = page.get("page", "?")
            parts.append(f'<page number="{page_num}">')
            parts.append(_serialize_page_content(page, rule))
            parts.append("</page>")
        return "\n\n".join(parts)

    sections_list = rule.get("sections") or []
    parts = []
    for section_name in sections_list:
        section_pages = _pages_for_section(section_name, pages)
        if section_pages:
            parts.append(f'<section name="{section_name}">')
            for page in section_pages:
                page_num = page.get("page", "?")
                parts.append(f'<page number="{page_num}">')
                parts.append(_serialize_page_content(page, rule))
                parts.append("</page>")
            parts.append("</section>")
    return "\n\n".join(parts)


class TextRuleAnalyzer:
    def __init__(self, app_config: AppYaml, settings: Settings) -> None:
        self.settings = settings
        self.concurrent_requests = app_config.pipeline.concurrent_requests
        self.system_prompt = load_prompt(TEXT_PROMPT_PATH)

    def _aggregate_rule_results(self, rule: dict, page_results: list[dict]) -> dict:
        completed_results = [item for item in page_results if item.get("execution_status") == "completed"]
        matched_pages = sorted(
            {
                int(citation.get("page", item.get("page", 0)))
                for item in completed_results
                for citation in item.get("citations", [])
                if int(citation.get("page", item.get("page", 0))) > 0
            }
            | {
                int(item.get("page", 0))
                for item in completed_results
                if item.get("verdict") in {"pass", "fail", "needs_review"} and int(item.get("page", 0)) > 0
            }
        )
        verdict = "needs_review"
        if any(item.get("verdict") == "fail" for item in completed_results):
            verdict = "fail"
        elif completed_results and all(item.get("verdict") in {"pass", "not_applicable"} for item in completed_results):
            verdict = "pass"

        summary_parts = [
            f"Page {item.get('page')}: {item.get('verdict', 'needs_review')}"
            for item in page_results
            if item.get("page") is not None
        ]
        findings: list[str] = []
        citations: list[dict] = []
        notes: list[str] = []
        for item in page_results:
            findings.extend(item.get("findings", [])[:2])
            citations.extend(item.get("citations", [])[:2])
            notes.extend(item.get("notes", [])[:1])

        return {
            "rule_id": rule.get("id", ""),
            "rule_name": rule.get("name", rule.get("id", "")),
            "analysis_type": "text",
            "execution_status": "completed" if completed_results else "error",
            "verdict": verdict,
            "summary": " | ".join(summary_parts[:6]) or "No page-level text analysis result was produced.",
            "reasoning": "Aggregated from page-level text analysis results.",
            "findings": findings[:4],
            "citations": citations[:4],
            "matched_pages": matched_pages,
            "notes": notes[:4],
        }

    def _analyze_broad_scope_rules(
        self,
        broad_rules: list[dict],
        pages: list[dict],
        provider,
        results: dict,
        page_results: list[dict],
        lock: threading.Lock,
        on_page_result: Callable[[dict, dict[str, dict], list[dict]], None] | None,
        is_cancelled: Callable[[], bool] | None,
    ) -> None:
        for rule in broad_rules:
            if is_cancelled and is_cancelled():
                break
            rule_id = rule.get("id", "")
            scope = rule.get("scope", "page")

            if scope == "document":
                gathered_pages = pages
                if not gathered_pages:
                    with lock:
                        results[rule_id] = _build_not_applicable_rule_result(rule, "Document has no extracted pages.")
                    continue
                document_content = _serialize_broad_scope_content(gathered_pages, rule, group_by_section=False)

            elif scope == "multi_page":
                sections = rule.get("sections") or []
                if not sections:
                    with lock:
                        results[rule_id] = _build_not_applicable_rule_result(rule, "Rule has no sections defined.")
                    continue
                missing = [s for s in sections if not _pages_for_section(s, pages)]
                if missing:
                    reason = f"Sections not found in document: {', '.join(missing)}."
                    with lock:
                        results[rule_id] = _build_not_applicable_rule_result(rule, reason)
                    continue
                gathered_pages = [p for s in sections for p in _pages_for_section(s, pages)]
                document_content = _serialize_broad_scope_content(gathered_pages, rule, group_by_section=True)

            gathered_page_nums = sorted({p.get("page") for p in gathered_pages if p.get("page")})

            try:
                _t0 = time.perf_counter()
                logger.info("LLM call start: type=text scope=%s rule=%s pages=%s", scope, rule_id, gathered_page_nums)
                raw_result = provider.evaluate_rule(document_content=document_content, rule=rule, system_prompt=self.system_prompt)
                logger.info("LLM call done: type=text scope=%s rule=%s elapsed=%s", scope, rule_id, timedelta(seconds=time.perf_counter() - _t0))
                verdict = raw_result.get("verdict", "needs_review")
                summary = raw_result.get("summary", "")
                reasoning = raw_result.get("reasoning", "")
                findings = raw_result.get("findings", [])[:4]
                citations = [
                    {"page": int(c.get("page", 0) or 0), "evidence": c.get("evidence", "")}
                    for c in raw_result.get("citations", [])
                ][:4]
                notes = [f"Confidence: {raw_result.get('confidence', 'unknown')}"]

                rule_result = {
                    "rule_id": rule_id,
                    "rule_name": raw_result.get("rule_name", rule.get("name", rule_id)),
                    "analysis_type": "text",
                    "scope": scope,
                    "execution_status": "completed",
                    "verdict": verdict,
                    "summary": summary,
                    "reasoning": reasoning,
                    "findings": findings,
                    "citations": citations,
                    "matched_pages": gathered_page_nums,
                    "notes": notes,
                }
                synthetic_page_results = [
                    {
                        "page": gathered_page_nums[0],
                        "rule_id": rule_id,
                        "rule_name": rule_result["rule_name"],
                        "analysis_type": "text",
                        "scope": scope,
                        "execution_status": "completed",
                        "verdict": verdict,
                        "summary": summary,
                        "reasoning": reasoning,
                        "findings": findings,
                        "citations": citations,
                        "notes": notes,
                    }
                ] if gathered_page_nums else []
            except Exception as exc:
                rule_result = _build_skipped_result(rule, f"Text analysis failed: {exc}", execution_status="error")
                synthetic_page_results = (
                    [_build_skipped_result(rule, f"Text analysis failed: {exc}", execution_status="error", page=gathered_page_nums[0])]
                    if gathered_page_nums else []
                )

            with lock:
                results[rule_id] = rule_result
                page_results.extend(synthetic_page_results)
                if on_page_result and synthetic_page_results:
                    on_page_result(synthetic_page_results[0], dict(results), list(page_results))

    def analyze(
        self,
        pages: list[dict],
        rules: list[dict],
        on_page_result: Callable[[dict, dict[str, dict], list[dict]], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, list[dict] | dict[str, dict]]:
        text_rules = [rule for rule in rules if rule.get("analysis_type", "text") == "text"]
        if not text_rules:
            return {"rule_results": {}, "page_results": []}

        page_rules = [r for r in text_rules if r.get("scope", "page") == "page"]
        broad_rules = [r for r in text_rules if r.get("scope", "page") in ("multi_page", "document")]

        try:
            provider = build_text_provider(self.settings)
        except ValueError as exc:
            error_message = str(exc)
            skipped_page_results = [
                _build_skipped_result(rule, error_message, page=max(1, page.get("page", 1)))
                for rule in page_rules
                for page in (pages[:1] or [{"page": 1}])
            ]
            return {
                "rule_results": {
                    rule.get("id", ""): _build_skipped_result(rule, error_message)
                    for rule in text_rules
                },
                "page_results": skipped_page_results,
            }

        results: dict[str, dict] = {}
        page_results: list[dict] = []
        per_rule_page_results: dict[str, list[dict]] = {rule.get("id", ""): [] for rule in page_rules}
        lock = threading.Lock()

        # Mark rules with no applicable pages upfront; collect work items for the rest.
        applicable_pairs: list[tuple[dict, dict]] = []
        for rule in page_rules:
            rule_id = rule.get("id", "")
            matching = [p for p in pages if rule_applies_to_page(rule, p.get("page_type") or [])]
            if not matching:
                results[rule_id] = _build_not_applicable_rule_result(rule, "No pages matched this rule's section.")
            else:
                for page in matching:
                    applicable_pairs.append((rule, page))

        def _call(rule: dict, page: dict) -> dict | None:
            if is_cancelled and is_cancelled():
                return None
            rule_id = rule.get("id", "")
            page_number = int(page.get("page", 0))
            try:
                document_content = _serialize_page_content(page, rule)
                _t0 = time.perf_counter()
                logger.info("LLM call start: type=text rule=%s page=%d", rule_id, page_number)
                raw_result = provider.evaluate_rule(document_content=document_content, rule=rule, system_prompt=self.system_prompt)
                logger.info("LLM call done: type=text rule=%s page=%d elapsed=%s", rule_id, page_number, timedelta(seconds=time.perf_counter() - _t0))
                citations = raw_result.get("citations", [])
                return {
                    "page": page_number,
                    "rule_id": rule_id,
                    "rule_name": raw_result.get("rule_name", rule.get("name", rule_id)),
                    "analysis_type": "text",
                    "execution_status": "completed",
                    "verdict": raw_result.get("verdict", "needs_review"),
                    "summary": raw_result.get("summary", ""),
                    "reasoning": raw_result.get("reasoning", ""),
                    "findings": raw_result.get("findings", []),
                    "citations": [
                        {
                            "page": int(item.get("page", page_number) or page_number),
                            "evidence": item.get("evidence", ""),
                        }
                        for item in citations
                    ],
                    "notes": [f"Confidence: {raw_result.get('confidence', 'unknown')}"],
                }
            except Exception as exc:
                return _build_skipped_result(rule, f"Text analysis failed: {exc}", execution_status="error", page=page_number)

        self._analyze_broad_scope_rules(
            broad_rules=broad_rules,
            pages=pages,
            provider=provider,
            results=results,
            page_results=page_results,
            lock=lock,
            on_page_result=on_page_result,
            is_cancelled=is_cancelled,
        )

        with ThreadPoolExecutor(max_workers=self.concurrent_requests) as executor:
            future_to_rule = {executor.submit(_call, rule, page): rule for rule, page in applicable_pairs}
            for future in as_completed(future_to_rule):
                if is_cancelled and is_cancelled():
                    for f in future_to_rule:
                        f.cancel()
                    break
                rule = future_to_rule[future]
                rule_id = rule.get("id", "")
                page_result = future.result()
                if page_result is None:
                    continue
                with lock:
                    per_rule_page_results[rule_id].append(page_result)
                    page_results.append(page_result)
                    results[rule_id] = self._aggregate_rule_results(rule, per_rule_page_results[rule_id])
                    if on_page_result:
                        on_page_result(page_result, dict(results), list(page_results))

        # Any page-scope rule that had applicable pages but nothing collected (fully cancelled)
        for rule in page_rules:
            rule_id = rule.get("id", "")
            if rule_id not in results:
                results[rule_id] = self._aggregate_rule_results(rule, per_rule_page_results[rule_id])

        return {"rule_results": results, "page_results": page_results}
