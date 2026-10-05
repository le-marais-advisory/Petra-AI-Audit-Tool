"""Turn a comparison's runs into per-variant scores and a per-rule matrix.

Accuracy counts every expected verdict on every repeat; a failed run counts as wrong. A *missed fail* is a
check expected to fail that came back pass: the most expensive error for an audit tool. *Stability* is the
share of rules whose verdict was the same on every repeat; *agreement* is the share whose majority verdict
matches the baseline variant's, which also covers rules no case has an expected verdict for.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from typing import Any

_TRUNCATED = re.compile(r"max_tokens|truncated", re.IGNORECASE)
_FALLBACK = "refusal fallback"


def _majority(verdicts: list[str]) -> str | None:
    return Counter(verdicts).most_common(1)[0][0] if verdicts else None


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return round(ordered[index], 1)


def _reaggregate(page_verdicts: list[dict], rule_id: str, pages: list[int]) -> str:
    """Verdict over a subset of pages, as tests/integration/test_pipeline.py computes it."""
    verdicts = [p["verdict"] for p in page_verdicts
                if p["rule_id"] == rule_id and p.get("page") in pages and p.get("execution_status") == "completed"]
    if not verdicts:
        return "missing"
    if "fail" in verdicts:
        return "fail"
    if all(v in ("pass", "not_applicable") for v in verdicts):
        return "pass"
    return "needs_review"


def check_verdict(run: dict, check: dict) -> str:
    if run["status"] != "ok":
        return "error"
    if check.get("pages"):
        return _reaggregate(run["page_verdicts"], check["rule_id"], check["pages"])
    result = run["rules"].get(check["rule_id"])
    return result["verdict"] if result else "missing"


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def compute(results: dict[str, Any]) -> dict[str, Any]:
    cases: dict[str, dict] = results["cases"]
    variants = list(results["variants"])
    baseline = results["baseline"]
    runs_by_variant: dict[str, list[dict]] = defaultdict(list)
    for run in results["runs"]:
        runs_by_variant[run["variant"]].append(run)

    # majority verdict per (variant, case, rule) for agreement and the matrix
    verdicts: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for run in results["runs"]:
        if run["status"] != "ok":
            continue
        for rule_id, result in run["rules"].items():
            verdicts[(run["variant"], run["case"], rule_id)].append(result["verdict"])

    scorecard: dict[str, dict[str, Any]] = {}
    for variant in variants:
        runs = runs_by_variant.get(variant, [])
        labelled = correct = missed_fails = false_fails = review_on_decided = 0
        confusion: dict[str, Counter] = defaultdict(Counter)
        by_kind: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        by_severity: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for run in runs:
            header = cases[run["case"]]
            for check in header["checks"]:
                if check["rule_id"] not in header["rules"]:  # results written before case_header filtered these
                    continue
                got = check_verdict(run, check)
                expected = check["verdict"]
                hit = got == expected
                labelled += 1
                correct += hit
                missed_fails += expected == "fail" and got == "pass"
                false_fails += expected != "fail" and got == "fail"
                review_on_decided += expected in ("pass", "fail") and got == "needs_review"
                confusion[expected][got] += 1
                meta = header["rules"].get(check["rule_id"], {})
                for bucket, key in ((by_kind, meta.get("kind", "unknown")), (by_severity, meta.get("severity", "unspecified"))):
                    bucket[key][0] += hit
                    bucket[key][1] += 1

        # stability over repeats and agreement with the baseline, per (case, rule)
        keys = [k for k in verdicts if k[0] == variant]
        repeats = results.get("repeats", 1)
        stable = sum(1 for k in keys if len(set(verdicts[k])) == 1)
        agree = total_agree = 0
        if variant != baseline:
            for _, case_id, rule_id in keys:
                other = verdicts.get((baseline, case_id, rule_id))
                if other:
                    total_agree += 1
                    agree += _majority(verdicts[(variant, case_id, rule_id)]) == _majority(other)

        rule_results = [r for run in runs if run["status"] == "ok" for r in run["rules"].values()]
        llm_results = [r for r in rule_results if r.get("llm_model")]
        text_of = lambda r: f"{r.get('summary') or ''} {r.get('reasoning') or ''}"  # noqa: E731
        costs = [run["usage"]["totals"].get("cost_usd") for run in runs]
        known_costs = [c for c in costs if c is not None]
        totals = [run["usage"]["totals"] for run in runs]
        layouts = [score for run in runs for score in (run.get("layouts") or {}).values()]
        walls_by_case: dict[str, list[float]] = defaultdict(list)
        for run in runs:
            walls_by_case[run["case"]].append(run["wall_s"])

        scorecard[variant] = {
            "runs": len(runs),
            "failed_runs": sum(run["status"] != "ok" for run in runs),
            "labelled": labelled,
            "accuracy": _ratio(correct, labelled),
            "missed_fails": missed_fails,
            "false_fails": false_fails,
            "needs_review_on_decided": review_on_decided,
            "confusion": {e: dict(g) for e, g in confusion.items()},
            "accuracy_by_kind": {k: _ratio(*v) for k, v in sorted(by_kind.items())},
            "accuracy_by_severity": {k: _ratio(*v) for k, v in sorted(by_severity.items())},
            "stability": _ratio(stable, len(keys)) if repeats > 1 else None,
            "agreement_with_baseline": _ratio(agree, total_agree) if variant != baseline else None,
            "rule_errors": sum(r.get("execution_status") in ("error", "skipped") for r in rule_results),
            "truncations": sum(bool(_TRUNCATED.search(text_of(r))) for r in llm_results
                               if r.get("execution_status") == "error" or r.get("verdict") == "needs_review"),
            "fallbacks": sum(any(_FALLBACK in n for n in (r.get("notes") or [])) for r in rule_results),
            "cost_total": round(sum(known_costs), 4) if known_costs else None,
            "cost_per_run": round(statistics.mean(known_costs), 4) if known_costs else None,
            "cost_complete": len(known_costs) == len(costs),
            "tokens_per_run": {name: round(statistics.mean(t[name] for t in totals)) if totals else 0
                               for name in ("input_tokens", "cache_read_tokens", "cache_creation_tokens",
                                            "output_tokens", "reasoning_tokens", "calls")},
            "wall_s_per_run": round(statistics.mean(run["wall_s"] for run in runs), 1) if runs else None,
            "wall_s_by_case": {c: round(statistics.mean(v), 1) for c, v in walls_by_case.items()},
            "rule_ms_p50": _percentile([r["duration_ms"] for r in llm_results if r.get("duration_ms")], 0.5),
            "rule_ms_p95": _percentile([r["duration_ms"] for r in llm_results if r.get("duration_ms")], 0.95),
            "layout_anchor_accuracy": round(statistics.mean(s["anchor_accuracy"] for s in layouts), 4) if layouts else None,
            "layout_field_accuracy": round(statistics.mean(s["field_accuracy"] for s in layouts), 4) if layouts else None,
            "layouts_scored": len(layouts),
            "models": sorted({r["llm_model"] for r in llm_results}),
        }

    return {"scorecard": scorecard, "matrix": _matrix(results, verdicts, baseline)}


def _matrix(results: dict[str, Any], verdicts: dict, baseline: str) -> list[dict[str, Any]]:
    """One row per (case, rule) with every variant's verdicts, cost and answers, for the report's grid."""
    cases = results["cases"]
    variants = list(results["variants"])
    expected = {(case_id, c["rule_id"]): c["verdict"] for case_id, h in cases.items() for c in h["checks"]
                if not c.get("pages")}
    cost: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    answers: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for run in results["runs"]:
        per_rule: dict[str, float] = defaultdict(float)
        for call in run.get("calls") or []:
            if call.get("rule_id") and call.get("cost_usd") is not None:
                per_rule[call["rule_id"]] += call["cost_usd"]
        for rule_id, amount in per_rule.items():
            cost[(run["variant"], run["case"], rule_id)].append(amount)
        for rule_id, r in run["rules"].items():
            answers[(run["variant"], run["case"], rule_id)].append({
                "repeat": run["repeat"], "verdict": r["verdict"], "status": r.get("execution_status"),
                "summary": r.get("summary") or "", "reasoning": r.get("reasoning") or "",
                "findings": r.get("findings") or [], "notes": r.get("notes") or [],
                "model": r.get("llm_model"), "effort": r.get("llm_effort"), "ms": r.get("duration_ms")})

    rows = []
    for case_id, header in cases.items():
        for rule_id, meta in header["rules"].items():
            cells = {}
            for variant in variants:
                vs = verdicts.get((variant, case_id, rule_id), [])
                spent = cost.get((variant, case_id, rule_id), [])
                cells[variant] = {"verdicts": vs, "majority": _majority(vs),
                                  "cost": round(statistics.mean(spent), 5) if spent else None,
                                  "answers": answers.get((variant, case_id, rule_id), [])}
            majorities = {c["majority"] for c in cells.values() if c["majority"]}
            base = cells.get(baseline, {}).get("majority")
            rows.append({"case": case_id, "rule_id": rule_id, "name": meta["name"], "kind": meta["kind"],
                         "severity": meta["severity"], "expected": expected.get((case_id, rule_id)),
                         "disagreement": len(majorities) > 1 or any(len(set(c["verdicts"])) > 1 for c in cells.values()),
                         "differs_from_baseline": any(c["majority"] not in (None, base) for c in cells.values()),
                         "cells": cells})
    return rows
