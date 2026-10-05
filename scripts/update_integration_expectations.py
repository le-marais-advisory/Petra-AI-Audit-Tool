#!/usr/bin/env python3
"""Discovery helper for integration test cases.

Runs the validation pipeline for every case in tests/integration/cases.yaml
that has no `expected` entries, then prints a ready-to-paste YAML block for
each one.

Usage:
    python scripts/update_integration_expectations.py

Workflow:
    1. Add a new case block to tests/integration/cases.yaml (document + rules,
       no `expected` key yet).
    2. Run this script. It calls the live pipeline for each case that is missing
       expected entries and prints the actual verdicts.
    3. Review the output, paste the `expected` block into cases.yaml, commit.

Requires a valid .env with ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
CASES_FILE = REPO_ROOT / "tests" / "integration" / "cases.yaml"
RULES_FILE = REPO_ROOT / "rules" / "rules.json"

sys.path.insert(0, str(REPO_ROOT))


def main() -> None:
    from src.services.validation_service import ValidationService

    data = yaml.safe_load(CASES_FILE.read_text())
    cases: list[dict] = (data or {}).get("cases") or []
    all_rules: dict[str, dict] = {
        r["id"]: r for r in json.loads(RULES_FILE.read_text())["rules"]
    }

    pending = [c for c in cases if not c.get("expected")]
    if not pending:
        print("All cases already have expected entries. Nothing to do.")
        return

    service = ValidationService()

    for case in pending:
        case_id = case["id"]
        doc_path = REPO_ROOT / case["document"]

        if not doc_path.exists():
            print(f"\n[SKIP] Case '{case_id}': document not found at {doc_path}")
            continue

        rule_ids: list[str] = case.get("rules") or []
        unknown = [rid for rid in rule_ids if rid not in all_rules]
        if unknown:
            print(f"\n[SKIP] Case '{case_id}': unknown rule IDs {unknown}")
            continue

        print(f"\nRunning pipeline for case '{case_id}'...", flush=True)
        selected_rules = [all_rules[rid] for rid in rule_ids]
        result = service.validate_document(
            pdf_path=str(doc_path),
            source_filename=doc_path.name,
            rules_json_str=json.dumps({"rules": selected_rules}),
        )

        assessments = {
            a["rule_id"]: a
            for a in result.get("analysis", {}).get("rule_assessments", [])
        }

        lines = [
            f"\n  # Paste this into the '{case_id}' case in tests/integration/cases.yaml:",
            "  expected:",
        ]
        for rid in rule_ids:
            a = assessments.get(rid, {})
            verdict = a.get("verdict", "needs_review")
            matched = sorted(a.get("matched_pages") or [])
            lines.append(f"    - rule_id: {rid}")
            lines.append(f"      verdict: {verdict}")
            if matched:
                lines.append(f"      matched_pages: {matched}")
        print("\n".join(lines))

    print()


if __name__ == "__main__":
    main()
