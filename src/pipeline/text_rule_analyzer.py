from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal

from src.core.config import AppYaml, Settings
from src.core.prompting import load_prompt
from src.pipeline.page_classifier import rule_applies_to_page, SECTION_TO_KEY
from src.providers.text.base import TextAnalysisProvider
from src.providers.text.factory import build_text_provider

logger = logging.getLogger("petra.pipeline.text")


TEXT_PROMPT_PATH = "config/text_analysis_system_prompt.md"

# Hard ceiling on text-analysis worker threads, independent of app.yaml, so a typo in
# the config cannot launch hundreds of threads. Deliberately a constant rather than a
# second YAML knob.
MAX_TEXT_WORKERS = 24


@dataclass(frozen=True)
class _TextWorkItem:
    """One LLM call to make.

    ``kind`` tells the completion handler how to commit the result: page-scope results
    are aggregated per rule, broad-scope results are committed verbatim. Frozen because
    worker threads read these concurrently — every field is populated during the
    single-threaded classification pass, before any thread starts.
    """

    kind: Literal["page", "broad"]
    rule: dict
    rule_id: str
    # page items only
    page: dict | None = None
    # broad items only
    scope: str = "page"
    gathered_pages: list[dict] = field(default_factory=list)
    gathered_page_nums: list[int] = field(default_factory=list)
    group_by_section: bool = False


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


def _broad_scope_preamble(pages: list[dict], group_by_section: bool) -> str:
    """Tell the model what the payload does and does not cover.

    Without this, broad-scope calls inherit the system prompt's single-page framing and
    hedge to needs_review whenever a Table of Contents references a page they think they
    were not given — which happens routinely, because TOC entries cite printed page
    numbers while the <page number="..."> tags below carry physical file positions.

    The completeness claim is deliberately scoped to the uploaded file. A document can
    reference a separately attached external package that this pipeline never ingests
    (it accepts exactly one PDF), and TOC-PAGE-NUMBERS-MATCH / TOC-COMPLETENESS both
    depend on the model still hedging in that case.
    """
    if group_by_section:
        coverage = (
            "This is a subset of the uploaded PDF: only the pages belonging to the sections this "
            "rule covers, grouped by section below. The document has other pages; they are omitted "
            "because they are outside this rule's scope, not because they are unavailable."
        )
    else:
        page_count = len(pages)
        coverage = (
            f"This is the complete extracted content of the uploaded PDF — all {page_count} "
            f"page{'' if page_count == 1 else 's'}, in document order. No pages have been withheld."
        )

    return (
        "CONTENT SCOPE\n"
        f"{coverage}\n"
        'Numbers in the <page number="..."> tags are physical positions in the file. They may differ '
        "from the printed page numbers shown on the pages themselves or listed in a Table of Contents. "
        "A mismatch between the two is not evidence that content is missing from this payload.\n"
        "Content that lives outside this file — for example a separately attached document that the "
        "Table of Contents refers to — was never provided to this system and is not available to you. "
        "Say so explicitly when a verdict depends on it."
    )


def _serialize_broad_scope_content(pages: list[dict], rule: dict, group_by_section: bool = False) -> str:
    preamble = _broad_scope_preamble(pages, group_by_section)

    if not group_by_section:
        parts: list[str] = [preamble]
        for page in pages:
            page_num = page.get("page", "?")
            parts.append(f'<page number="{page_num}">')
            parts.append(_serialize_page_content(page, rule))
            parts.append("</page>")
        return "\n\n".join(parts)

    sections_list = rule.get("sections") or []
    parts = [preamble]
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
    def __init__(
        self,
        app_config: AppYaml,
        settings: Settings,
        provider: TextAnalysisProvider | None = None,
    ) -> None:
        self.app_config = app_config
        self.settings = settings
        # Injected by tests only. Production resolves the provider lazily inside
        # analyze() so a missing API key still yields per-rule "skipped" results
        # rather than failing construction.
        self._provider = provider
        self.system_prompt = load_prompt(TEXT_PROMPT_PATH)

    def _max_workers(self) -> int:
        """Concurrency for text LLM calls.

        Mirrors VisionRuleAnalyzer._max_workers. The configured value previously
        reached ThreadPoolExecutor unchecked, so a 0 or negative in app.yaml raised
        ValueError mid-run; this floors it at 1 and caps it at MAX_TEXT_WORKERS.
        PIPELINE_CONCURRENT_REQUESTS overrides app.yaml when set.
        """
        override = self.settings.PIPELINE_CONCURRENT_REQUESTS
        configured = override if override is not None else getattr(
            self.app_config.pipeline, "concurrent_requests", 1
        )
        requested = max(1, int(configured or 1))
        return min(requested, MAX_TEXT_WORKERS)

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

        durations = [item.get("duration_ms") for item in page_results if item.get("duration_ms") is not None]
        total_duration_ms = round(sum(durations), 1) if durations else None

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
            "duration_ms": total_duration_ms,
        }

    def _classify_broad_scope_rule(
        self, rule: dict, pages: list[dict]
    ) -> tuple[_TextWorkItem | None, dict | None]:
        """Decide whether a broad-scope rule has work to do. No I/O, no lock.

        Returns exactly one of (work_item, None) or (None, not_applicable_result), so
        the caller can seed every not-applicable outcome before any thread starts.
        """
        rule_id = rule.get("id", "")
        scope = rule.get("scope", "page")

        if scope == "document":
            if not pages:
                return None, _build_not_applicable_rule_result(rule, "Document has no extracted pages.")
            gathered_pages = list(pages)
            group_by_section = False

        elif scope == "multi_page":
            sections = rule.get("sections") or []
            if not sections:
                return None, _build_not_applicable_rule_result(rule, "Rule has no sections defined.")
            missing = [s for s in sections if not _pages_for_section(s, pages)]
            if missing:
                reason = f"Sections not found in document: {', '.join(missing)}."
                return None, _build_not_applicable_rule_result(rule, reason)
            gathered_pages = [p for s in sections for p in _pages_for_section(s, pages)]
            group_by_section = True

        else:
            # Previously this branch did not exist: an unrecognised scope fell through
            # both filters in analyze() and the rule vanished from the results entirely.
            # `scope` is forced back to "page" because RuleAssessmentSchema.scope is a
            # Literal and _build_not_applicable_rule_result copies rule["scope"] through,
            # so an unknown value would fail validation later in build_document_result.
            unsupported = _build_not_applicable_rule_result(rule, f"Unsupported rule scope '{scope}'.")
            unsupported["scope"] = "page"
            return None, unsupported

        return (
            _TextWorkItem(
                kind="broad",
                rule=rule,
                rule_id=rule_id,
                scope=scope,
                gathered_pages=gathered_pages,
                gathered_page_nums=sorted({p.get("page") for p in gathered_pages if p.get("page")}),
                group_by_section=group_by_section,
            ),
            None,
        )

    def _evaluate_page_scope_rule(self, item: _TextWorkItem, provider: TextAnalysisProvider) -> dict:
        """Run one page-scope LLM call. Touches no shared state and takes no lock."""
        rule = item.rule
        rule_id = item.rule_id
        page = item.page or {}
        page_number = int(page.get("page", 0))
        try:
            document_content = _serialize_page_content(page, rule)
            _t0 = time.perf_counter()
            logger.info("LLM call start: type=text rule=%s page=%d", rule_id, page_number)
            raw_result = provider.evaluate_rule(
                document_content=document_content, rule=rule, system_prompt=self.system_prompt
            )
            _elapsed = time.perf_counter() - _t0
            logger.info(
                "LLM call done: type=text rule=%s page=%d elapsed=%s",
                rule_id,
                page_number,
                timedelta(seconds=_elapsed),
            )
            citations = raw_result.get("citations", [])
            return {
                "page": page_number,
                "rule_id": rule_id,
                "rule_name": rule.get("name", rule_id),  # canonical name; never trust the model's returned rule_name
                "analysis_type": "text",
                "execution_status": "completed",
                "duration_ms": round(_elapsed * 1000, 1),
                "verdict": raw_result.get("verdict", "needs_review"),
                "summary": raw_result.get("summary", ""),
                "reasoning": raw_result.get("reasoning", ""),
                "findings": raw_result.get("findings", []),
                "citations": [
                    {
                        "page": int(citation.get("page", page_number) or page_number),
                        "evidence": citation.get("evidence", ""),
                    }
                    for citation in citations
                ],
                "notes": [f"Confidence: {raw_result.get('confidence', 'unknown')}"],
            }
        except Exception as exc:
            return _build_skipped_result(
                rule, f"Text analysis failed: {exc}", execution_status="error", page=page_number
            )

    def _evaluate_broad_scope_rule(
        self, item: _TextWorkItem, provider: TextAnalysisProvider
    ) -> tuple[dict, list[dict]]:
        """Run one broad-scope LLM call. Touches no shared state and takes no lock.

        The returned rule result deliberately bypasses _aggregate_rule_results, which
        never sets `scope` and would recompute matched_pages from citations, losing the
        gathered page numbers.
        """
        rule = item.rule
        rule_id = item.rule_id
        scope = item.scope
        gathered_page_nums = item.gathered_page_nums
        try:
            # Serialized here rather than during classification: the whole-document
            # string is built and released within one future instead of living for the
            # lifetime of analyze(), and a serialization failure now degrades to an
            # error result instead of propagating out and killing the run.
            document_content = _serialize_broad_scope_content(
                item.gathered_pages, rule, group_by_section=item.group_by_section
            )
            _t0 = time.perf_counter()
            logger.info(
                "LLM call start: type=text scope=%s rule=%s pages=%s", scope, rule_id, gathered_page_nums
            )
            raw_result = provider.evaluate_rule(
                document_content=document_content, rule=rule, system_prompt=self.system_prompt
            )
            _elapsed = time.perf_counter() - _t0
            logger.info(
                "LLM call done: type=text scope=%s rule=%s elapsed=%s",
                scope,
                rule_id,
                timedelta(seconds=_elapsed),
            )
            _duration_ms = round(_elapsed * 1000, 1)
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
                "rule_name": rule.get("name", rule_id),  # canonical name; never trust the model's returned rule_name
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
                "duration_ms": _duration_ms,
            }
            # Attributed to the first gathered page. Integration tests re-aggregate from
            # page_results filtered by (rule_id, page), so this must stay as-is.
            synthetic_page_results = (
                [
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
                        "duration_ms": _duration_ms,
                    }
                ]
                if gathered_page_nums
                else []
            )
            return rule_result, synthetic_page_results
        except Exception as exc:
            rule_result = _build_skipped_result(
                rule, f"Text analysis failed: {exc}", execution_status="error"
            )
            synthetic_page_results = (
                [
                    _build_skipped_result(
                        rule,
                        f"Text analysis failed: {exc}",
                        execution_status="error",
                        page=gathered_page_nums[0],
                    )
                ]
                if gathered_page_nums
                else []
            )
            return rule_result, synthetic_page_results

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
        # Exhaustive partition: an unrecognised scope lands in the broad bucket and is
        # classified not_applicable, rather than being dropped from the results entirely.
        broad_rules = [r for r in text_rules if r.get("scope", "page") != "page"]

        try:
            provider = self._provider or build_text_provider(self.settings)
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
        # Keyed from page_rules only. Broad rules are never keys here — the completion
        # handler branches on item.kind before indexing this dict.
        per_rule_page_results: dict[str, list[dict]] = {rule.get("id", ""): [] for rule in page_rules}
        lock = threading.Lock()

        # Classification pass. Fully sequential with no I/O and no threads started yet,
        # so every results[...] write below is safe unlocked. Every not-applicable
        # outcome, for both rule kinds, is seeded here before anything is submitted.
        broad_items: list[_TextWorkItem] = []
        for rule in broad_rules:
            item, not_applicable = self._classify_broad_scope_rule(rule, pages)
            if not_applicable is not None:
                results[rule.get("id", "")] = not_applicable
            elif item is not None:
                broad_items.append(item)

        page_items: list[_TextWorkItem] = []
        for rule in page_rules:
            rule_id = rule.get("id", "")
            matching = [p for p in pages if rule_applies_to_page(rule, p.get("page_type") or [])]
            if not matching:
                results[rule_id] = _build_not_applicable_rule_result(rule, "No pages matched this rule's section.")
            else:
                page_items.extend(
                    _TextWorkItem(kind="page", rule=rule, rule_id=rule_id, page=page) for page in matching
                )

        # Broad items first. ThreadPoolExecutor dispatches FIFO and a broad call carries a
        # whole document, so submitting them last would leave their latency as a tail
        # after the pool drains — rebuilding the serial prologue this replaced.
        work_items = broad_items + page_items

        def _run(item: _TextWorkItem) -> tuple[dict | None, list[dict]]:
            """Returns (rule_result_override, page_results_to_append).

            (None, []) means cancelled — commit nothing. Workers never touch shared
            state and never take the lock; the completion handler is the only writer.
            """
            if is_cancelled and is_cancelled():
                return None, []
            if item.kind == "broad":
                return self._evaluate_broad_scope_rule(item, provider)
            return None, [self._evaluate_page_scope_rule(item, provider)]

        with ThreadPoolExecutor(max_workers=self._max_workers()) as executor:
            future_to_item = {executor.submit(_run, item): item for item in work_items}
            for future in as_completed(future_to_item):
                if is_cancelled and is_cancelled():
                    for f in future_to_item:
                        f.cancel()
                    break
                item = future_to_item[future]
                rule_result, new_page_results = future.result()
                if rule_result is None and not new_page_results:
                    continue
                with lock:
                    if item.kind == "page":
                        per_rule_page_results[item.rule_id].extend(new_page_results)
                        page_results.extend(new_page_results)
                        results[item.rule_id] = self._aggregate_rule_results(
                            item.rule, per_rule_page_results[item.rule_id]
                        )
                    else:
                        results[item.rule_id] = rule_result
                        page_results.extend(new_page_results)
                    if on_page_result and new_page_results:
                        on_page_result(new_page_results[0], dict(results), list(page_results))

        # Any page-scope rule that had applicable pages but nothing collected (fully
        # cancelled). Broad rules are deliberately not covered: with no entry here they
        # fall through to build_rule_assessments' "skipped" default, whereas routing them
        # through _aggregate_rule_results would strip their scope.
        for rule in page_rules:
            rule_id = rule.get("id", "")
            if rule_id not in results:
                results[rule_id] = self._aggregate_rule_results(rule, per_rule_page_results[rule_id])

        return {"rule_results": results, "page_results": page_results}
