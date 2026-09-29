from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class RuleSchema(BaseModel):
    id: str
    name: str
    analysis_type: Literal["text", "vision"] = "text"
    scope: Literal["page", "multi_page", "document"] = "page"
    query: str
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


class RulesResponse(BaseModel):
    rules: list[RuleSchema]
