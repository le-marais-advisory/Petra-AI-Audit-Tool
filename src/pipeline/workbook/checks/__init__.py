"""Deterministic capital-event checks.

Each check reads the typed data extracted from validated layouts and returns the same
AnalysisRuleResult shape the LLM analyzers produce, with sheet/cell citations.
"""
from __future__ import annotations

from src.pipeline.workbook.checks import allocation, rollforward_itd, ties, workbook_checks  # noqa: F401  (register)
from src.pipeline.workbook.checks._common import DETERMINISTIC_CHECKS, CheckContext, result, run_check
from src.pipeline.workbook.extract import WorkbookData
from src.pipeline.workbook.loader import WorkbookModel
from src.providers.analysis_result import AnalysisRuleResult

__all__ = ["DETERMINISTIC_CHECKS", "run_deterministic_checks"]


def run_deterministic_checks(
    model: WorkbookModel,
    data: WorkbookData,
    rules: list[dict],
    options: dict | None = None,
) -> dict[str, AnalysisRuleResult]:
    options = options or {}
    results: dict[str, AnalysisRuleResult] = {}
    for rule in rules:
        spec = DETERMINISTIC_CHECKS.get(rule["id"])
        if spec is None:
            continue
        ctx = CheckContext(model=model, data=data, rule=rule, options=options)
        try:
            results[rule["id"]] = run_check(spec, ctx)
        except Exception as exc:  # a defective check must not take down the run
            results[rule["id"]] = result(rule, "needs_review", f"The check could not complete: {type(exc).__name__}.",
                                         [str(exc)])
    return results
