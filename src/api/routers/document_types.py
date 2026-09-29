from __future__ import annotations

from fastapi import APIRouter

from src.document_types.registry import list_document_types


router = APIRouter(prefix="/document-types", tags=["document-types"])


@router.get("")
async def get_document_types() -> dict:
    return {"document_types": [spec.to_public_dict() for spec in list_document_types()]}
