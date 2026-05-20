from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


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


class RulesResponse(BaseModel):
    rules: list[RuleSchema]
