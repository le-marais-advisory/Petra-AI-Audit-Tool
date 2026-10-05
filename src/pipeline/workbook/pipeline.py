"""Capital-event workbook pipeline.

  1. load the workbook (formulas + cached values) and inventory every sheet
  2. assign sheet roles (heuristic proposal, confirmed by one small LLM call)
  3. keep only the sheets the user-selected event type needs
  4. map and validate one layout per relevant sheet (LLM + deterministic validator)
  5. extract typed data from the validated layouts
  6. run deterministic rules in code; run hybrid rules through the LLM with a
     computed-facts block and skeletons of the sheets each rule needs
  7. assemble a DocumentValidationResponse-compatible result (one unit per sheet)
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from concurrent.futures import as_completed
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from src.core.llm_usage import ContextThreadPoolExecutor, run_cache_primed, track_usage, usage_stage
from src.pipeline.result_builder import build_document_result
from src.pipeline.workbook.checks import run_deterministic_checks
from src.pipeline.workbook.checks._common import ROLE_LABELS
from src.pipeline.workbook.extract import WorkbookData, extract_workbook_data
from src.pipeline.workbook.facts import build_facts, render_facts
from src.pipeline.workbook.inventory import build_inventory
from src.pipeline.workbook.layout_mapper import map_sheet_layout
from src.pipeline.workbook.layout_validator import validate_layout
from src.pipeline.workbook.loader import WorkbookModel, load_workbook_model
from src.pipeline.workbook.roles import llm_role_assigner, propose_roles
from src.pipeline.workbook.selection import relevant_sheets
from src.pipeline.workbook.skeleton import build_skeleton
from src.providers.analysis_result import AnalysisRuleResult

logger = logging.getLogger("petra.pipeline")

DOCUMENT_TYPE = "capital_event_workbook"
PRIOR_ROLES = {"allocation", "itd"}  # what the cross-event rules read from the prior workbook
_PROMPT_PATH = Path(__file__).resolve().parents[3] / "config" / "workbook_analysis_system_prompt.md"

LayoutMapper = Callable[[WorkbookModel, str, str], BaseModel | None]
ProgressCallback = Callable[..., None]


def _timestamp_id() -> str:
    return time.strftime("%Y%m%dT%H%M%S")


class WorkbookPipeline:
    def __init__(self, text_provider=None, role_assigner=None, layout_mapper: LayoutMapper | None = None,
                 layout_concurrency: int | None = None, hybrid_concurrency: int | None = None,
                 prompt_cache: bool | None = None) -> None:
        self._provider = text_provider
        self._role_assigner = role_assigner
        self._layout_mapper = layout_mapper
        app_cfg = None
        if layout_concurrency is None or hybrid_concurrency is None or prompt_cache is None:
            try:
                from src.core.config import load_app_yaml

                app_cfg = load_app_yaml()
            except Exception:
                app_cfg = None
        workbook_cfg = app_cfg.workbook if app_cfg else None
        self.layout_concurrency = layout_concurrency or (workbook_cfg.layout_concurrency if workbook_cfg else 6)
        self.hybrid_concurrency = hybrid_concurrency or (workbook_cfg.hybrid_concurrency if workbook_cfg else 6)
        self.prompt_cache = prompt_cache if prompt_cache is not None else (
            app_cfg.pipeline.prompt_cache if app_cfg else True)

    # -- collaborators -------------------------------------------------------------------------

    @property
    def provider(self):
        if self._provider is None:
            from src.core.config import get_settings
            from src.providers.text.factory import build_text_provider

            self._provider = build_text_provider(get_settings())
        return self._provider

    def _assign_roles(self, model: WorkbookModel) -> dict[str, str]:
        inventory = build_inventory(model)
        proposed = propose_roles(inventory)
        assigner = self._role_assigner or llm_role_assigner(self.provider)
        try:
            with usage_stage("Sheet roles"):
                return assigner(model, inventory, proposed)
        except Exception:
            logger.exception("Role assignment failed; using the heuristic proposal")
            return proposed

    def _map_layout(self, model: WorkbookModel, sheet: str, role: str, event_type: str | None) -> BaseModel | None:
        if self._layout_mapper is not None:
            return self._layout_mapper(model, sheet, role)
        with usage_stage("Layout mapping"):
            layout, _ = map_sheet_layout(model, sheet, role, provider=self.provider, event_type=event_type)
        return layout

    # -- run ------------------------------------------------------------------------------------

    def run(self, file_path: str, *args, **kwargs) -> dict:
        with track_usage() as meter:
            response = self._run(file_path, *args, **kwargs)
        response["llm_usage"] = meter.summary()
        return response

    def _run(
        self,
        file_path: str,
        rules: list[dict] | None = None,
        options: dict | None = None,
        source_filename: str | None = None,
        on_progress: ProgressCallback | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        prior_file_path: str | None = None,
        prior_source_filename: str | None = None,
    ) -> dict:
        t0 = time.perf_counter()
        options = dict(options or {})
        event_type = options.get("event_type")
        rules = rules or []
        cancelled = is_cancelled or (lambda: False)
        progress = on_progress or (lambda *a, **k: None)
        source_filename = source_filename or Path(file_path).name
        total_steps = 2 + len(rules)

        progress("Reading workbook", 0, total_steps)
        model = load_workbook_model(file_path, file_name=source_filename)
        roles = self._assign_roles(model)
        selected = relevant_sheets(roles, event_type) if event_type else [s for s, r in roles.items() if r != "other"]
        logger.info("Workbook %s: %d sheets, processing %s", source_filename, len(model.sheets), selected)

        progress("Mapping sheet layouts", 1, total_steps)
        layouts, layout_errors = self._map_layouts(model, roles, selected, event_type)
        prior = None
        if prior_file_path and not options.get("first_event"):
            progress("Reading the prior event's workbook", 1, total_steps)
            prior = self._prior_data(prior_file_path, prior_source_filename)
        data = extract_workbook_data(model, layouts, prior=prior)
        for role, reason in _errors_by_role(layout_errors, roles).items():
            data.extraction_errors.setdefault(role, reason)
        if model.formulas_missing_cache:
            logger.warning("Workbook %s has formulas without cached values", source_filename)

        results: dict[str, AnalysisRuleResult] = {}
        durations: dict[str, float] = {}
        deterministic = [r for r in rules if r.get("evaluator") == "deterministic"]
        llm_rules = [r for r in rules if r.get("evaluator") != "deterministic"]
        if not cancelled():
            started = time.perf_counter()
            results.update(run_deterministic_checks(model, data, deterministic, options))
            for rule in deterministic:
                durations[rule["id"]] = (time.perf_counter() - started) * 1000 / max(1, len(deterministic))
        done = 2 + len(results)
        progress("Evaluating LLM-judged rules", done, total_steps)
        if llm_rules and not cancelled():
            for rule_id, result, elapsed in self._run_hybrid(model, data, llm_rules, options, roles, cancelled):
                results[rule_id] = result
                durations[rule_id] = elapsed
                done += 1
                progress(f"Evaluated {result.rule_name}", done, total_steps)

        elapsed = time.perf_counter() - t0
        logger.info("Workbook pipeline complete: file=%s sheets=%d rules=%d elapsed=%s", source_filename,
                    len(selected), len(rules), timedelta(seconds=elapsed))
        return self._build_result(model, roles, selected, data.reference_sheets, rules, results, durations, options,
                                  source_filename, elapsed, cancelled())

    def _prior_data(self, path: str, source_filename: str | None) -> WorkbookData | None:
        """Data from the prior event's workbook: only the sheets the cross-event rules compare."""
        try:
            model = load_workbook_model(path, file_name=source_filename or Path(path).name)
            with usage_stage("Prior workbook"):
                roles = self._assign_roles(model)
                wanted = [s for s, r in roles.items() if r in PRIOR_ROLES]
                layouts, errors = self._map_layouts(model, roles, wanted, None)
            data = extract_workbook_data(model, layouts)
            data.extraction_errors.update(_errors_by_role(errors, roles))
            return data
        except Exception:
            logger.exception("Could not read the prior event's workbook %s", source_filename)
            return None

    def _map_layouts(self, model, roles, selected, event_type) -> tuple[dict[str, BaseModel], dict[str, str]]:
        """Validated layouts by sheet, and the reason for every sheet that could not be mapped (by sheet,
        so a second Merge tab's failure does not overwrite the first's)."""
        mappable = [s for s in selected if roles.get(s, "other") != "other"]
        layouts: dict[str, BaseModel] = {}
        errors: dict[str, str] = {}
        with ContextThreadPoolExecutor(max_workers=max(1, min(self.layout_concurrency, len(mappable) or 1))) as pool:
            futures = {pool.submit(self._map_layout, model, s, roles[s], event_type): s for s in mappable}
            for future in as_completed(futures):
                sheet = futures[future]
                try:
                    layout = future.result()
                except Exception as exc:
                    logger.exception("Layout mapping failed for %s", sheet)
                    errors[sheet] = f"Layout mapping for '{sheet}' failed: {type(exc).__name__}: {exc}"
                    continue
                if layout is None:
                    errors[sheet] = f"The layout of '{sheet}' could not be mapped and validated."
                    continue
                issues = validate_layout(model, layout)
                if issues:
                    errors[sheet] = (f"The layout of '{sheet}' was rejected by the validator: "
                                     + "; ".join(f"{i.code}: {i.message}" for i in issues[:3]))
                    continue
                layouts[sheet] = layout
        ordered = {s: layouts[s] for s in selected if s in layouts}
        return ordered, errors

    # -- hybrid rules ----------------------------------------------------------------------------

    def _rule_content(self, model: WorkbookModel, data: WorkbookData, rule: dict, options: dict,
                      roles: dict[str, str]) -> tuple[str, str]:
        """(sheet excerpts, computed facts). The excerpts depend only on the rule's required roles, so
        rules needing the same sheets send the same excerpts and can share them as a cached prefix;
        the facts are the rule's own and follow them."""
        facts = build_facts(rule["id"], model, data, options)
        needed = set(rule.get("required_roles") or [])
        sheets = [name for name in data.processed_sheets if not needed or roles.get(name) in needed]
        parts = []
        for name in sheets:
            sheet = model.sheet(name)
            parts.append(f'<sheet name="{name}" index="{sheet.index}" role="{roles.get(name)}">\n'
                         f"{build_skeleton(sheet)}</sheet>")
        return "\n".join(parts), render_facts(rule["id"], facts)

    def _run_hybrid(self, model, data, rules, options, roles, cancelled):
        system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")
        runnable, blocked = [], []
        for rule in rules:
            missing = [r for r in rule.get("required_roles") or [] if not _role_present(data, r)]
            (blocked if missing else runnable).append((rule, missing))
        for rule, missing in blocked:
            labels = ", ".join(ROLE_LABELS.get(r, r) for r in missing)
            reasons = [data.extraction_errors[r] for r in missing if r in data.extraction_errors]
            yield rule["id"], _result(rule, "needs_review",
                                      f"The {labels} sheet layout could not be mapped or validated, so this rule "
                                      "could not be evaluated.", reasons), 0.0

        def sheet_set(rule) -> tuple[str, ...]:
            return tuple(sorted(rule.get("required_roles") or []))

        shared = Counter(sheet_set(rule) for rule, _ in runnable) if self.prompt_cache else Counter()

        def evaluate(rule):
            started = time.perf_counter()
            sheets, facts = self._rule_content(model, data, rule, options, roles)
            with usage_stage("Hybrid rules"):
                raw = self.provider.evaluate_rule(sheets, rule, system_prompt, rule_context=facts,
                                                  cache_content=shared[sheet_set(rule)] > 1)
            try:
                parsed = AnalysisRuleResult(**{**raw, "rule_id": rule["id"], "rule_name": rule.get("name", rule["id"])})
            except ValidationError as exc:
                parsed = _result(rule, "needs_review", "The model returned an invalid result.", [str(exc)[:300]])
            return parsed, (time.perf_counter() - started) * 1000

        # With prompt_cache on, one rule per sheet set runs first and the rest of its group follows
        # once it has written the cache.
        cache_key = sheet_set if self.prompt_cache else (lambda rule: None)
        to_run = [rule for rule, _ in runnable] if not cancelled() else []
        with ContextThreadPoolExecutor(max_workers=max(1, min(self.hybrid_concurrency, len(runnable) or 1))) as pool:
            for rule, future in run_cache_primed(pool, to_run, evaluate, cache_key, cancelled):
                try:
                    parsed, elapsed = future.result()
                except Exception as exc:
                    logger.exception("Hybrid rule %s failed", rule["id"])
                    parsed, elapsed = _result(rule, "needs_review", f"The rule could not be evaluated: "
                                                                    f"{type(exc).__name__}.", [str(exc)[:300]]), 0.0
                yield rule["id"], parsed, elapsed

    # -- result ------------------------------------------------------------------------------------

    def _build_result(self, model, roles, selected, reference_sheets, rules, results, durations, options,
                      source_filename, elapsed, cancelled) -> dict:
        pages = []
        units = [(name, roles.get(name, "other")) for name in selected]
        units += [(name, "reference") for name in reference_sheets if name not in selected]
        for name, role in units:
            sheet = model.sheet(name)
            text = build_skeleton(sheet)
            pages.append({"page": sheet.index, "label": name, "text": text, "tables": [], "char_count": len(text),
                          "page_type": [role]})
        index_by_name = {s.name: s.index for s in model.sheets}
        default_page = pages[0]["page"] if pages else 1
        assessments, page_results = [], []
        for rule in rules:
            result = results.get(rule["id"])
            if result is None:
                result = _result(rule, "needs_review", "Analysis stopped before this rule ran." if cancelled
                                 else "No result was produced for this rule.")
            citations = [c.model_dump() for c in result.citations]
            for citation in citations:
                if citation.get("sheet") in index_by_name:
                    citation["page"] = index_by_name[citation["sheet"]]
            matched = sorted({c["page"] for c in citations})
            base = {
                "rule_id": rule["id"],
                "rule_name": rule.get("name", rule["id"]),
                "analysis_type": "text",
                "scope": rule.get("scope", "page"),
                "execution_status": "completed" if rule["id"] in results else ("cancelled" if cancelled else "completed"),
                "verdict": result.verdict,
                "summary": result.summary,
                "reasoning": result.reasoning,
                "findings": result.findings,
                "citations": citations,
                "notes": [f"Evaluated {'in code' if rule.get('evaluator') == 'deterministic' else 'by the LLM with computed facts'}."],
                "duration_ms": round(durations.get(rule["id"], 0.0), 1),
            }
            assessments.append({**base, "matched_pages": matched})
            page = matched[0] if matched else default_page
            label = next((p["label"] for p in pages if p["page"] == page), None)
            page_results.append({**base, "page": page, "label": label})
        response = build_document_result(
            document_id=Path(source_filename).stem + f"_{_timestamp_id()}",
            pages=pages,
            source_filename=source_filename,
            selected_rules=rules,
            rule_assessments=assessments,
            text_page_results=page_results,
            visual_page_results=[],
            elapsed_seconds=elapsed,
        )
        response["document_type"] = DOCUMENT_TYPE
        response["options"] = options
        response["analysis"]["overview"] = _overview(model, selected, rules, elapsed) + [
            m for m in response["analysis"]["overview"] if m["label"] in ("Selected Rules", "Rules Bypassed",
                                                                         "Slowest Rule")]
        return response


def _errors_by_role(errors_by_sheet: dict[str, str], roles: dict[str, str]) -> dict[str, str]:
    """Layout failures keyed by role, every failed sheet of the role listed."""
    by_role: dict[str, list[str]] = {}
    for sheet, reason in errors_by_sheet.items():
        by_role.setdefault(roles.get(sheet, "other"), []).append(reason)
    return {role: " ".join(reasons) for role, reasons in by_role.items()}


def _role_present(data: WorkbookData, role: str) -> bool:
    if role == "merge":
        return bool(data.merges)
    if role == "holiday_calendar":
        return data.has_holiday_calendar
    return getattr(data, role, None) is not None


def _result(rule: dict, verdict: str, summary: str, findings: list[str] | None = None) -> AnalysisRuleResult:
    return AnalysisRuleResult(rule_id=rule["id"], rule_name=rule.get("name", rule["id"]), verdict=verdict,
                              summary=summary, reasoning=summary, findings=findings or [], confidence="low",
                              citations=[])


def _overview(model: WorkbookModel, selected: list[str], rules: list[dict], elapsed: float) -> list[dict[str, Any]]:
    skipped = len(model.sheets) - len(selected)
    return [
        {"label": "Processing Time", "value": f"{elapsed:.1f}s", "detail": "Total wall-clock time for the run."},
        {"label": "Sheets", "value": str(len(model.sheets)), "detail": "Sheets in the workbook."},
        {"label": "Sheets Processed", "value": str(len(selected)),
         "detail": f"Sheets the selected event type needs; {skipped} other sheet(s) were inventoried only."},
        {"label": "Rules In Code", "value": str(sum(1 for r in rules if r.get('evaluator') == 'deterministic')),
         "detail": "Deterministic rules evaluated from stored values and formulas."},
        {"label": "Rules By LLM", "value": str(sum(1 for r in rules if r.get('evaluator') != 'deterministic')),
         "detail": "Judgment rules evaluated by the LLM with computed facts."},
    ]
