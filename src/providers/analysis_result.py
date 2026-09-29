from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AnalysisCitation(BaseModel):
    page: int
    evidence: str = ""
    sheet: str | None = None  # workbook documents: sheet name (page = 1-based sheet index)
    cell: str | None = None  # workbook documents: cell or range, e.g. "H7" or "H7:H19"


class AnalysisRuleResult(BaseModel):
    rule_id: str
    rule_name: str
    verdict: Literal["pass", "fail", "needs_review", "not_applicable"]
    summary: str
    reasoning: str
    findings: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"]
    citations: list[AnalysisCitation] = Field(default_factory=list)


RULE_RESULT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "rule_id": {"type": "string"},
        "rule_name": {"type": "string"},
        "reasoning": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {"type": "string"},
        },
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer"},
                    "evidence": {"type": "string"},
                    "sheet": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "cell": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
                "required": ["page", "evidence", "sheet", "cell"],
                "additionalProperties": False,
            },
        },
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "summary": {"type": "string"},
        "verdict": {"type": "string", "enum": ["pass", "fail", "needs_review", "not_applicable"]},
    },
    "required": ["rule_id", "rule_name", "reasoning", "findings", "citations", "confidence", "summary", "verdict"],
    "additionalProperties": False,
}


def build_vector_data_text(page_image: dict) -> str:
    if "double_underline_hints" not in page_image:
        return ""
    hints = page_image["double_underline_hints"]
    if hints is None:
        return ""
    if hints:
        positions = ", ".join(str(h["y_fraction"]) for h in hints)
        return (
            "VECTOR DATA (extracted from PDF drawing commands — treat as authoritative):\n"
            f"Double underlines detected at the following page positions (fraction of page height from top): [{positions}]\n"
            "Any total row whose underline falls near these y-positions has a confirmed double underline.\n\n"
        )
    return (
        "VECTOR DATA (extracted from PDF drawing commands — treat as authoritative):\n"
        "No double underline line pairs detected in the PDF drawing commands for this page.\n\n"
    )


def compact_rule_payload(rule: dict) -> str:
    return (
        f"RULE NAME: {rule.get('name', '')}\n"
        f"RULE QUERY: {rule.get('query', '')}\n"
        f"RULE ACCEPTANCE CRITERIA: {rule.get('acceptance_criteria', '')}\n"
    )
