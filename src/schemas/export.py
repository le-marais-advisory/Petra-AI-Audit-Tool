from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from src.schemas.validation import DocumentAnalysisSchema


class ExportPdfRequest(BaseModel):
    document_id: str
    source_filename: Optional[str] = None
    page_count: int = 0
    document_type: str = Field(default="financial_statements", description="Document type of the validated run.")
    options: dict = Field(default_factory=dict, description="Run options, e.g. the capital-event type.")
    cover_sheet_text: str = Field(default="", description="Free-text cover page content provided by the user.")
    analysis: DocumentAnalysisSchema = Field(default_factory=DocumentAnalysisSchema)
