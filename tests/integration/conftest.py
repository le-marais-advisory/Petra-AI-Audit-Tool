from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).parent.parent.parent
_RULES_FILES = [
    _REPO_ROOT / "rules" / "rules.json",
    _REPO_ROOT / "rules" / "multi_page_rules.json",
]
_CASES_FILE = Path(__file__).parent / "cases.yaml"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--rule",
        action="append",
        dest="rules",
        metavar="RULE_ID",
        default=None,
        help=(
            "Only run assertions for this rule ID (repeatable: --rule A --rule B). "
            "Skips pipeline invocations for cases with no assertions for the selected rules."
        ),
    )
    parser.addoption(
        "--case",
        action="append",
        dest="cases",
        metavar="CASE_ID",
        default=None,
        help="Only run pipeline for this case ID (repeatable: --case A --case B).",
    )
    parser.addoption(
        "--severity",
        action="append",
        dest="severities",
        metavar="SEVERITY",
        default=None,
        help=(
            "Only run rules of this severity (repeatable: --severity critical --severity major). "
            "Valid values: critical, major, minor."
        ),
    )
    parser.addoption(
        "--workers",
        type=int,
        default=4,
        dest="workers",
        help="Number of parallel pipeline workers for the integration test fixture (default: 4).",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    severity_filter = set(config.getoption("severities") or [])
    if not severity_filter:
        return
    skip = pytest.mark.skip(reason=f"not in --severity filter ({', '.join(sorted(severity_filter))})")
    _severity_names = {"critical", "major", "minor"}
    for item in items:
        item_severities = {m.name for m in item.iter_markers() if m.name in _severity_names}
        if item_severities and not item_severities & severity_filter:
            item.add_marker(skip)


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
def pipeline_results(request) -> dict[str, dict]:
    """Run the validation pipeline once per test case and cache results for the session.

    Only cases that have an `expected` block are executed — discovery runs
    (cases without expected entries) are handled by the separate
    scripts/update_integration_expectations.py script.

    Pass --rule RULE_ID (repeatable) to restrict execution to cases that
    assert on the given rule(s). Only the selected rules are sent to the
    pipeline, reducing API cost proportionally.

    Results are keyed by case ID. A result dict with an `__error__` key
    indicates a setup problem (missing document, unknown rule ID) rather
    than a pipeline failure; test_pipeline.py converts those into pytest.fail.
    """
    from src.services.validation_service import ValidationService

    rule_filter = set(request.config.getoption("rules") or [])
    case_filter = set(request.config.getoption("cases") or [])
    severity_filter = set(request.config.getoption("severities") or [])
    cases = [c for c in _load_cases() if c.get("expected")]

    all_rules = _load_all_rules()

    severity_rule_ids: set[str] = set()
    if severity_filter:
        severity_rule_ids = {
            rid for rid, r in all_rules.items()
            if r.get("severity", "major") in severity_filter
        }
        cases = [
            c for c in cases
            if any(e["rule_id"] in severity_rule_ids for e in c.get("expected", []))
        ]

    if case_filter:
        cases = [c for c in cases if c["id"] in case_filter]

    # Drop entire cases that have no expected assertions for the filtered rules
    if rule_filter:
        cases = [
            c for c in cases
            if any(e["rule_id"] in rule_filter for e in c.get("expected", []))
        ]

    if not cases:
        return {}

    service = ValidationService()
    total = len(cases)
    completed_count = 0
    counter_lock = threading.Lock()

    def _run_case(case: dict) -> tuple[str, dict]:
        case_id = case["id"]
        doc_path = _REPO_ROOT / case["document"]
        if not doc_path.exists():
            return case_id, {"__error__": f"Document not found: {doc_path}"}
        rule_ids: list[str] = case.get("rules") or []
        if severity_filter:
            rule_ids = [rid for rid in rule_ids if rid in severity_rule_ids]
        if rule_filter:
            rule_ids = [rid for rid in rule_ids if rid in rule_filter]
        unknown = [rid for rid in rule_ids if rid not in all_rules]
        if unknown:
            return case_id, {"__error__": f"Unknown rule IDs in cases.yaml: {unknown}"}
        selected_rules = [all_rules[rid] for rid in rule_ids]
        t0 = time.monotonic()
        result = service.validate_document(
            pdf_path=str(doc_path),
            source_filename=doc_path.name,
            rules_json_str=json.dumps({"rules": selected_rules}),
        )
        elapsed = time.monotonic() - t0
        return case_id, result, elapsed

    workers = request.config.getoption("workers")
    results: dict[str, dict] = {}
    print(f"\npipeline: running {total} case(s) with {workers} worker(s)")
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_run_case, case): case for case in cases}
        for future in as_completed(futures):
            case_id, result, elapsed = future.result()
            results[case_id] = result
            with counter_lock:
                completed_count += 1
                n = completed_count
            status = "error" if "__error__" in result else "done"
            print(f"  [{n}/{total}] {case_id} — {status} ({elapsed:.1f}s)")

    return results
