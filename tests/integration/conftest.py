from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).parent.parent.parent
_RULES_FILES = [
    _REPO_ROOT / "rules" / "rules.json",
    _REPO_ROOT / "rules" / "multi_page_rules.json",
]
_CASES_FILE = Path(__file__).parent / "cases.yaml"


def _load_cases() -> list[dict]:
    data = yaml.safe_load(_CASES_FILE.read_text())
    return (data or {}).get("cases") or []


def _load_all_rules() -> dict[str, dict]:
    rules: dict[str, dict] = {}
    for path in _RULES_FILES:
        if path.exists():
            rules.update({r["id"]: r for r in json.loads(path.read_text())["rules"]})
    return rules


@pytest.fixture(scope="session")
def pipeline_results() -> dict[str, dict]:
    """Run the validation pipeline once per test case and cache results for the session.

    Only cases that have an `expected` block are executed — discovery runs
    (cases without expected entries) are handled by the separate
    scripts/update_integration_expectations.py script.

    Results are keyed by case ID. A result dict with an `__error__` key
    indicates a setup problem (missing document, unknown rule ID) rather
    than a pipeline failure; test_pipeline.py converts those into pytest.fail.
    """
    from src.services.validation_service import ValidationService

    cases = [c for c in _load_cases() if c.get("expected")]
    if not cases:
        return {}

    all_rules = _load_all_rules()
    service = ValidationService()
    results: dict[str, dict] = {}

    for case in cases:
        case_id = case["id"]
        doc_path = _REPO_ROOT / case["document"]

        if not doc_path.exists():
            results[case_id] = {"__error__": f"Document not found: {doc_path}"}
            continue

        rule_ids: list[str] = case.get("rules") or []
        unknown = [rid for rid in rule_ids if rid not in all_rules]
        if unknown:
            results[case_id] = {"__error__": f"Unknown rule IDs in cases.yaml: {unknown}"}
            continue

        selected_rules = [all_rules[rid] for rid in rule_ids]
        results[case_id] = service.validate_document(
            pdf_path=str(doc_path),
            source_filename=doc_path.name,
            rules_json_str=json.dumps({"rules": selected_rules}),
        )

    return results
