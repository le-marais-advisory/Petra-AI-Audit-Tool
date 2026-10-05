"""Run a suite under each variant and record verdicts, usage, cost and latency per run.

Each (case, variant, repeat) is one full validation, in-process, through ValidationService with the variant's
router. By default every run starts with a cold prompt cache: the router tags the system prompt with a run id,
so a repeat cannot read the cache an earlier run wrote, and the in-process layout cache is cleared. That keeps
cost and latency close to production, where every document is new. ``warm_cache`` turns both off.

Results are written to ``<out_dir>/results.json`` after every run, so an interrupted comparison keeps what
it finished.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from src.core.config import get_settings, load_app_yaml
from src.core.llm_usage import track_usage
from src.evaluation.llm_compare.suite import Case
from src.evaluation.llm_compare.variants import Variant
from src.providers.router import LlmRouter

RESULTS_FILE = "results.json"
_RULE_FIELDS = ("verdict", "execution_status", "summary", "reasoning", "findings", "notes", "duration_ms",
                "llm_model", "llm_effort", "analysis_type")


def rule_kind(rule: dict) -> str:
    if rule.get("evaluator") in ("deterministic", "hybrid"):
        return rule["evaluator"]
    return "vision" if rule.get("analysis_type") == "vision" else "text"


def select_rules(case: Case, rule_filter: set[str] | None) -> list[dict]:
    from src.services.rule_service import RuleService

    rules = RuleService().load_rules(document_type=case.document_type, event_type=case.options.get("event_type"))
    by_id = {r["id"]: r for r in rules}
    if case.rules is not None:
        unknown = [r for r in case.rules if r not in by_id]
        if unknown:
            raise ValueError(f"{case.id}: unknown rule ids {unknown}")
        rules = [by_id[r] for r in case.rules]
    if rule_filter:
        rules = [r for r in rules if r["id"] in rule_filter]
    return rules


@contextmanager
def _capture_layouts(case: Case) -> Iterator[dict[str, Any]]:
    """Record the layouts the workbook pipeline maps for the case's own workbook (not the prior one)."""
    mapped: dict[str, Any] = {}
    if not case.golden_layouts:
        yield mapped
        return
    from src.pipeline.workbook import pipeline as workbook_pipeline

    original = workbook_pipeline.map_sheet_layout

    def recording(model, sheet, role, *args, **kwargs):
        layout, issues = original(model, sheet, role, *args, **kwargs)
        if model.file_name == case.document.name and layout is not None:
            mapped[sheet] = layout
        return layout, issues

    workbook_pipeline.map_sheet_layout = recording
    try:
        yield mapped
    finally:
        workbook_pipeline.map_sheet_layout = original


def _layout_scores(case: Case, mapped: dict[str, Any]) -> dict[str, dict]:
    from src.evaluation.layout_scoring import score_layout

    scores = {}
    for sheet, golden in case.golden_layouts.items():
        layout = mapped.get(sheet)
        if layout is None:
            continue
        score = score_layout(layout.model_dump(mode="json"), golden)
        scores[sheet] = {"role": golden.get("role"), "anchor_accuracy": score.anchor_accuracy,
                         "field_accuracy": score.field_accuracy, "mismatches": score.mismatches}
    return scores


def run_one(case: Case, variant: Variant, repeat: int, rules: list[dict], warm_cache: bool) -> dict[str, Any]:
    from src.pipeline.workbook import layout_mapper
    from src.services.validation_service import ValidationService

    settings, app_config = get_settings(), load_app_yaml()
    salt = "" if warm_cache else f"{variant.name}-{repeat}-{uuid.uuid4().hex[:8]}"
    if not warm_cache:
        with layout_mapper._CACHE_LOCK:
            layout_mapper._CACHE.clear()
    router = LlmRouter(settings, app_config, overrides=variant.overrides, cache_salt=salt)
    record: dict[str, Any] = {"case": case.id, "variant": variant.name, "repeat": repeat, "status": "ok",
                              "error": None, "rules": {}, "page_verdicts": [], "layouts": {}}
    started = time.perf_counter()
    with track_usage() as meter, _capture_layouts(case) as mapped:
        try:
            response = ValidationService(settings, app_config, router=router).validate_document(
                file_path=str(case.document), source_filename=case.document.name,
                rules_json_str=json.dumps({"rules": rules}), document_type=case.document_type,
                options=case.options, prior_file_path=str(case.prior_document) if case.prior_document else None,
                prior_source_filename=case.prior_document.name if case.prior_document else None)
            analysis = response.get("analysis") or {}
            record["rules"] = {a["rule_id"]: {k: a.get(k) for k in _RULE_FIELDS}
                               for a in analysis.get("rule_assessments") or []}
            record["page_verdicts"] = [
                {k: p.get(k) for k in ("rule_id", "page", "verdict", "execution_status")}
                for p in (analysis.get("text_page_results") or []) + (analysis.get("visual_page_results") or [])]
        except Exception as exc:  # a failed run is a result too; the comparison goes on
            record["status"] = "error"
            record["error"] = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}"
    record["wall_s"] = round(time.perf_counter() - started, 2)
    record["usage"] = meter.summary()
    record["calls"] = [asdict(call) for call in meter.calls()]
    record["layouts"] = _layout_scores(case, mapped)
    return record


def case_header(case: Case, rules: list[dict]) -> dict[str, Any]:
    # Only checks on rules that run in this case are scored: a fixture can expect a verdict for a rule the
    # event type never loads, and --rule narrows the rules without narrowing the expectations.
    selected = {r["id"] for r in rules}
    return {"document": case.document.name, "document_type": case.document_type, "options": case.options,
            "prior_document": case.prior_document.name if case.prior_document else None, "source": case.source,
            "checks": [{"key": c.key, "rule_id": c.rule_id, "verdict": c.verdict, "pages": list(c.pages)}
                       for c in case.checks if c.rule_id in selected],
            "rules": {r["id"]: {"name": r.get("name", r["id"]), "kind": rule_kind(r),
                                "severity": r.get("severity") or "unspecified"} for r in rules},
            "has_golden_layouts": bool(case.golden_layouts)}


def write_results(out_dir: Path, results: dict[str, Any]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / RESULTS_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)
    return path


def run_comparison(cases: list[Case], variants: list[Variant], baseline: str, repeats: int, out_dir: Path,
                   rule_filter: set[str] | None = None, warm_cache: bool = False, suite_path: str = "",
                   log: Callable[[str], None] = lambda m: print(m, file=sys.stderr)) -> dict[str, Any]:
    rules_by_case = {case.id: select_rules(case, rule_filter) for case in cases}
    results: dict[str, Any] = {
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "suite": suite_path, "baseline": baseline, "repeats": repeats, "warm_cache": warm_cache,
        "default_model": get_settings().CLAUDE_TEXT_MODEL,
        "variants": {v.name: v.to_dict() for v in variants},
        "cases": {case.id: case_header(case, rules_by_case[case.id]) for case in cases},
        "runs": [],
    }
    total = len(cases) * len(variants) * repeats
    done = 0
    for repeat in range(1, repeats + 1):
        for variant in variants:
            for case in cases:
                done += 1
                log(f"[{done}/{total}] {variant.name} / {case.id} (repeat {repeat})")
                record = run_one(case, variant, repeat, rules_by_case[case.id], warm_cache)
                cost = record["usage"]["totals"].get("cost_usd")
                log(f"    {record['status']} in {record['wall_s']}s, "
                    f"{record['usage']['totals']['calls']} calls, cost {'?' if cost is None else f'${cost:.4f}'}")
                results["runs"].append(record)
                write_results(out_dir, results)
    return results
