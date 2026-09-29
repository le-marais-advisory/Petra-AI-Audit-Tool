from __future__ import annotations

import json
import re
from pathlib import Path

from src.schemas.rule import RuleSchema

DEFAULT_RULE_FILES = [
    "rules/rules.json",
    "rules/multi_page_rules.json",
]

# Canonical rule-id shape: uppercase alphanumerics and the separators used by
# existing ids (e.g. NUM-CROSSFOOT, ARITH-ASSETS=LIABS+CAP). Rejects lowercase
# ids (FMT-headings), empty ids, and stray free-text so malformed ids fail loudly
# on load instead of silently entering the pipeline with empty metadata.
RULE_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9=+\-]*$")


def _validate_rule_ids(rules_data: list[dict]) -> None:
    malformed: list[str] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    for rule in rules_data:
        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not RULE_ID_RE.match(rule_id):
            malformed.append(repr(rule_id))
            continue
        if rule_id in seen:
            duplicates.append(rule_id)
        seen.add(rule_id)

    problems: list[str] = []
    if malformed:
        problems.append(f"malformed rule id(s): {', '.join(malformed)}")
    if duplicates:
        problems.append(f"duplicate rule id(s): {', '.join(sorted(set(duplicates)))}")
    if problems:
        raise ValueError("Invalid rule definitions - " + "; ".join(problems))


def _rule_matches(rule: dict, document_type: str, event_type: str | None) -> bool:
    document_types = rule.get("document_types") or ["financial_statements"]
    if document_type not in document_types:
        return False
    event_types = rule.get("event_types")
    return event_type is None or event_types is None or event_type in event_types


class RuleService:
    def load_rules(
        self,
        rules_json_path: str | None = None,
        rules_json_str: str | None = None,
        document_type: str = "financial_statements",
        event_type: str | None = None,
    ) -> list[dict]:
        rules_data: list[dict]
        if rules_json_str:
            rules_data = json.loads(rules_json_str)["rules"]
        elif rules_json_path:
            rules_data = json.loads(Path(rules_json_path).read_text(encoding="utf-8"))["rules"]
        else:
            rules_data = []
            for path in _rule_files_for(document_type):
                p = Path(path)
                if p.exists():
                    rules_data.extend(json.loads(p.read_text(encoding="utf-8"))["rules"])
        _validate_rule_ids(rules_data)
        for rule in rules_data:
            rule.setdefault("analysis_type", "text")
            rule.setdefault("scope", "page")
            rule.setdefault("document_types", ["financial_statements"])
        return [rule for rule in rules_data if _rule_matches(rule, document_type, event_type)]

    def list_rules(
        self,
        rules_json_path: str | None = None,
        document_type: str = "financial_statements",
        event_type: str | None = None,
    ) -> list[RuleSchema]:
        return [
            RuleSchema(**rule)
            for rule in self.load_rules(rules_json_path=rules_json_path, document_type=document_type, event_type=event_type)
        ]


def _rule_files_for(document_type: str) -> list[str]:
    if document_type == "financial_statements":
        return DEFAULT_RULE_FILES
    from src.document_types.registry import get_document_type

    return get_document_type(document_type).rule_files
