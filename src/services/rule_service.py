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


class RuleService:
    def load_rules(self, rules_json_path: str | None = None, rules_json_str: str | None = None) -> list[dict]:
        rules_data: list[dict]
        if rules_json_str:
            rules_data = json.loads(rules_json_str)["rules"]
        elif rules_json_path:
            rules_data = json.loads(Path(rules_json_path).read_text(encoding="utf-8"))["rules"]
        else:
            rules_data = []
            for path in DEFAULT_RULE_FILES:
                p = Path(path)
                if p.exists():
                    rules_data.extend(json.loads(p.read_text(encoding="utf-8"))["rules"])
        _validate_rule_ids(rules_data)
        for rule in rules_data:
            rule.setdefault("analysis_type", "text")
            rule.setdefault("scope", "page")
        return rules_data

    def list_rules(self, rules_json_path: str | None = None) -> list[RuleSchema]:
        return [RuleSchema(**rule) for rule in self.load_rules(rules_json_path=rules_json_path)]
