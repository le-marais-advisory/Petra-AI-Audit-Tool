from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class RuleSchema(BaseModel):
    id: str
    name: str
    analysis_type: Literal["text", "vision"] = "text"
    scope: Literal["page", "multi_page", "document"] = "page"
    # The LLM prompt for the rule. Deterministic rules are implemented in code
    # (src/pipeline/workbook/checks/) and carry no query; every other rule needs one.
    query: Optional[str] = None
    description: Optional[str] = None
    acceptance_criteria: Optional[str] = None
    severity: Optional[str] = None
    group: Optional[str] = None
    section: Optional[str] = None
    sections: Optional[list[str]] = None
    bypassable: bool = False
    bypass: bool = False  # runtime: user opted to bypass this rule for this run
    document_types: list[str] = Field(default_factory=lambda: ["financial_statements"])
    event_types: Optional[list[str]] = None  # None = applies to every event type
    required_roles: Optional[list[str]] = None  # workbook sheet roles the rule reads
    evaluator: Literal["llm", "deterministic", "hybrid"] = "llm"
    requires_documents: Optional[list[str]] = None  # reference inputs (e.g. fund_terms) - not yet supported
    # Overrides of the default model / effort for this rule's LLM calls (see config/models.yaml).
    # Taken from the server's rule files only; values sent by a client are ignored.
    model: Optional[str] = None
    effort: Optional[str] = None

    @model_validator(mode="after")
    def _query_matches_evaluator(self) -> "RuleSchema":
        problem = query_problem(self.model_dump())
        if problem:
            raise ValueError(problem)
        return self


def query_problem(rule: dict) -> str | None:
    """Why a rule's ``query`` does not fit its evaluator, or None when it does."""
    has_query = bool(str(rule.get("query") or "").strip())
    if rule.get("evaluator") == "deterministic":
        if "query" in rule and rule["query"] is not None:
            return "deterministic rules are evaluated in code and must not define a query"
        return None
    if not has_query:
        return f"{rule.get('evaluator') or 'llm'} rules need a non-empty query"
    return None


class RulesResponse(BaseModel):
    rules: list[RuleSchema]
