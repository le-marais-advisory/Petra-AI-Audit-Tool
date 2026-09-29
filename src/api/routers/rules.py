from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.schemas.rule import RulesResponse
from src.services.rule_service import RuleService


router = APIRouter(prefix="/rules", tags=["rules"])
service = RuleService()


@router.get("", response_model=RulesResponse)
async def list_rules(
    rules_path: str | None = None,
    document_type: str = "financial_statements",
    event_type: str | None = None,
) -> RulesResponse:
    try:
        rules = service.list_rules(rules_json_path=rules_path, document_type=document_type, event_type=event_type)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RulesResponse(rules=rules)
