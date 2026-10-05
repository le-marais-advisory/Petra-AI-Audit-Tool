"""The model comparison tool end to end, offline: a fake router answers by effort, so variants disagree."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import yaml

from src.core.llm_usage import record_anthropic_usage
from src.evaluation.llm_compare import __main__ as cli
from src.evaluation.llm_compare import runner
from src.evaluation.llm_compare.metrics import compute
from src.evaluation.llm_compare.report import merge_results, render_report
from src.evaluation.llm_compare.suite import load_suite
from src.evaluation.llm_compare.variants import load_variants, parse_variant
from src.providers.router import LlmRouter

REPO = Path(__file__).resolve().parents[1]
PDF = REPO / "tests" / "fixtures" / "documents" / "pdfplumber_number_split_scenarios.pdf"


class FakeProvider:
    """Answers pass, or fail at low effort; maps workbooks to the fixture's golden roles and layouts."""

    def __init__(self, model: str, manifest) -> None:
        self.model, self.manifest = model, manifest

    def _bill(self, rule_id):
        response = NS(model=self.model, usage=NS(input_tokens=1000, output_tokens=100))
        record_anthropic_usage(response, self.model, "Text rules", rule_id)

    def evaluate_rule(self, document_content, rule, system_prompt, *args, effort=None, **kwargs):
        self._bill(rule.get("id"))
        verdict = "fail" if effort == "low" else "pass"
        return {"rule_id": rule["id"], "rule_name": rule["name"], "verdict": verdict, "summary": f"{verdict} at {effort}",
                "reasoning": "fake", "findings": [], "confidence": "high", "citations": []}

    def complete_structured(self, system_prompt, user_content, json_schema, name="result", effort=None):
        self._bill(None)
        if name == "sheet_roles":
            return {"roles": [{"sheet": s, "role": r} for s, r in self.manifest.sheet_roles.items()]}
        sheet = json.loads(re.search(r'Map the layout of sheet (".+?")', user_content).group(1))
        return copy.deepcopy(self.manifest.layouts[sheet])


@pytest.fixture()
def comparison(tmp_path, monkeypatch, capital_event_fixtures):
    manifest = capital_event_fixtures.get("capital_call")
    suite = tmp_path / "suite.yaml"
    suite.write_text(yaml.safe_dump({"cases": [
        {"id": "pdf", "document": str(PDF), "rules": ["NUM-REFOOT", "NUM-CROSSFOOT"],
         "expected": {"NUM-REFOOT": "pass", "NUM-CROSSFOOT": "fail"}},
        {"from": str(REPO / "tests" / "evals" / "capital_event_cases.yaml"), "only": ["call_clean"]},
    ]}))
    variants = tmp_path / "variants.yaml"
    variants.write_text(yaml.safe_dump({"baseline": "current", "variants": {
        "current": {"description": "defaults"},
        "low": {"stages": {"text_rule": {"effort": "low"}, "hybrid_rule": {"effort": "low"}}},
    }}))

    class Router(LlmRouter):
        def text_provider(self, model):
            return FakeProvider(model, manifest)

    monkeypatch.setattr(runner, "LlmRouter", Router)
    out = tmp_path / "out"
    assert cli.main(["run", "--suite", str(suite), "--variants", str(variants), "--out", str(out), "--repeats", "2",
                     "--yes"]) == 0
    run_dir = next(out.iterdir())
    return json.loads((run_dir / runner.RESULTS_FILE).read_text()), run_dir


def test_runs_every_case_variant_and_repeat(comparison):
    results, run_dir = comparison
    assert len(results["runs"]) == 2 * 2 * 2
    assert {r["status"] for r in results["runs"]} == {"ok"}
    assert (run_dir / "report.html").exists()
    pdf_run = next(r for r in results["runs"] if r["case"] == "pdf" and r["variant"] == "low")
    assert pdf_run["rules"]["NUM-REFOOT"]["llm_effort"] == "low"
    assert pdf_run["usage"]["totals"]["cost_usd"] > 0


def test_scores_accuracy_missed_fails_agreement_cost_and_layouts(comparison):
    results, _ = comparison
    score = compute(results)["scorecard"]
    current, low = score["current"], score["low"]
    # current answers pass everywhere: NUM-CROSSFOOT (expected fail) is a missed fail on both repeats
    assert current["missed_fails"] == 2 and low["missed_fails"] == 0
    assert low["false_fails"] > 0 and low["accuracy"] < 1
    assert current["stability"] == 1.0 and current["agreement_with_baseline"] is None
    assert low["agreement_with_baseline"] < 1
    assert current["cost_per_run"] > 0 and current["cost_complete"]
    assert current["layout_anchor_accuracy"] == 1.0 and current["layouts_scored"] > 0
    assert set(current["accuracy_by_kind"]) >= {"text", "hybrid", "deterministic"}


def test_matrix_flags_disagreement_and_report_embeds_every_variant(comparison, tmp_path):
    results, _ = comparison
    rows = {(r["case"], r["rule_id"]): r for r in compute(results)["matrix"]}
    refoot = rows[("pdf", "NUM-REFOOT")]
    assert refoot["differs_from_baseline"] and refoot["expected"] == "pass"
    assert refoot["cells"]["low"]["verdicts"] == ["fail", "fail"]
    answer = refoot["cells"]["low"]["answers"][0]
    assert answer["verdict"] == "fail" and answer["effort"] == "low" and answer["model"] == "claude-sonnet-5-5"
    html = render_report(results, tmp_path / "r.html").read_text()
    assert '"low"' in html and '"current"' in html and "</script>" in html
    assert html.count("</script>") == 2  # embedded data cannot close its script element


def test_report_merges_separate_runs(comparison):
    results, _ = comparison
    extra = copy.deepcopy(results)
    extra["variants"] = {"later": {"name": "later", "description": ""}}
    for run in extra["runs"]:
        run["variant"] = "later"
    merged = merge_results([results, extra])
    assert set(merged["variants"]) == {"current", "low", "later"}
    assert set(compute(merged)["scorecard"]) == {"current", "low", "later"}


def test_bootstrap_prints_majority_verdicts(comparison, capsys):
    _, run_dir = comparison
    assert cli.main(["bootstrap", str(run_dir), "--variant", "current"]) == 0
    printed = yaml.safe_load(capsys.readouterr().out)
    pdf = next(c for c in printed["cases"] if c["id"] == "pdf")
    assert pdf["expected"]["NUM-CROSSFOOT"] == "pass"


def test_only_checks_on_rules_that_run_are_scored(tmp_path):
    from src.evaluation.llm_compare.suite import Case, Check

    case = Case(id="c", document=PDF, rules=["NUM-REFOOT"],
                checks=[Check("NUM-REFOOT", "pass"), Check("NUM-CROSSFOOT", "fail")])
    header = runner.case_header(case, runner.select_rules(case, None))
    assert [c["rule_id"] for c in header["checks"]] == ["NUM-REFOOT"]
    narrowed = runner.case_header(case, runner.select_rules(case, {"NUM-CROSSFOOT"}))
    assert narrowed["checks"] == []


def test_dry_run_sends_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(runner, "run_comparison", lambda *a, **k: pytest.fail("ran without --yes"))
    assert cli.main(["run", "--case", "number_split_formatting", "--variant", "current", "--out", str(tmp_path)]) == 0
    assert "Dry run" in capsys.readouterr().out


def test_the_committed_suite_and_variants_load():
    baseline, variants = load_variants(REPO / "evals" / "llm_variants.yaml")
    assert baseline == "current" and {"current", "sonnet-5", "sonnet-5-5-medium"} <= set(variants)
    assert {c.id for c in load_suite(REPO / "evals" / "llm_suite.yaml")} >= {"multi_page_rules", "call_clean"}


@pytest.mark.parametrize("raw,message", [
    ({"defaults": {"model": "claude-nope"}}, "not in config/models.yaml"),
    ({"stages": {"layouts": {"effort": "low"}}}, "unknown purpose"),
    ({"rule_overrides": {"R1": {"model": "claude-haiku-4-5", "effort": "low"}}}, "does not accept an effort"),
    ({"defaults": {"effort": "extreme"}}, "unknown effort"),
    ({"stage": {}}, "unknown key"),
])
def test_bad_variants_are_rejected(raw, message):
    with pytest.raises(ValueError, match=message):
        parse_variant("v", raw)


def test_suite_rejects_missing_documents_and_bad_verdicts(tmp_path):
    suite = tmp_path / "suite.yaml"
    suite.write_text(yaml.safe_dump({"cases": [
        {"id": "a", "document": "nope.pdf"},
        {"id": "b", "document": str(PDF), "expected": {"R": "passed"}},
    ]}))
    with pytest.raises(ValueError) as exc:
        load_suite(suite)
    assert "document not found" in str(exc.value) and "unknown verdict 'passed'" in str(exc.value)
