from __future__ import annotations

import json
from pathlib import Path

from src.schemas.rule import RuleSchema

DEFAULT_RULE_FILES = [
    "rules/rules.json",
    "rules/multi_page_rules.json",
]


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
        for rule in rules_data:
            rule.setdefault("analysis_type", "text")
            rule.setdefault("scope", "page")
        return rules_data

    def list_rules(self, rules_json_path: str | None = None) -> list[RuleSchema]:
        return [RuleSchema(**rule) for rule in self.load_rules(rules_json_path=rules_json_path)]
