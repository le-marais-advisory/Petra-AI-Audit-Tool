from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ExtractedTableSchema(BaseModel):
    index: int = Field(..., description="1-based table index within the page.")
    rows: list[list[str]] = Field(default_factory=list, description="Normalized table rows extracted from the page.")


class PageExtractionSchema(BaseModel):
    page: int = Field(..., description="1-based page number from the PDF.")
    text: str = Field(default="", description="Raw text extracted from the page.")
    tables: list[ExtractedTableSchema] = Field(default_factory=list, description="Structured tables extracted from the page.")
    char_count: int = Field(default=0, description="Character count of extracted page text.")
    page_type: list[str] = Field(
        default_factory=list,
        description="Statement types detected on this page (e.g. balance_sheet). Empty means unclassified.",
    )


class AnalysisMetricSchema(BaseModel):
    label: str
    value: str
    detail: Optional[str] = None


class PageAnalysisSchema(BaseModel):
    page: int
    observations: list[str] = Field(default_factory=list)


class AnalysisCitationSchema(BaseModel):
    page: int
    evidence: str = ""


class RuleAssessmentSchema(BaseModel):
    rule_id: str
    rule_name: str
    analysis_type: Literal["text", "vision"] = "text"
    scope: Literal["page", "multi_page", "document"] = "page"
    execution_status: str = "completed"
    verdict: str = "needs_review"
    summary: str = ""
    reasoning: str = ""
    findings: list[str] = Field(default_factory=list)
    citations: list[AnalysisCitationSchema] = Field(default_factory=list)
    matched_pages: list[int] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    group: Optional[str] = None
    bypassable: bool = False
    bypass: bool = False


class PageRuleAssessmentSchema(BaseModel):
    page: int
    rule_id: str
    rule_name: str
    analysis_type: Literal["text", "vision"] = "text"
    scope: Literal["page", "multi_page", "document"] = "page"
    execution_status: str = "completed"
    verdict: str = "needs_review"
    summary: str = ""
    reasoning: str = ""
    findings: list[str] = Field(default_factory=list)
    citations: list[AnalysisCitationSchema] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    group: Optional[str] = None
    bypassable: bool = False
    bypass: bool = False


class DocumentAnalysisSchema(BaseModel):
    overview: list[AnalysisMetricSchema] = Field(default_factory=list)
    selected_rule_count: int = 0
    text_rule_count: int = 0
    vision_rule_count: int = 0
    rule_assessments: list[RuleAssessmentSchema] = Field(default_factory=list)
    text_page_results: list[PageRuleAssessmentSchema] = Field(default_factory=list)
    visual_page_results: list[PageRuleAssessmentSchema] = Field(default_factory=list)
    page_observations: list[PageAnalysisSchema] = Field(default_factory=list)


class DocumentValidationResponse(BaseModel):
    document_id: str
    page_count: int = Field(default=0, description="Total number of pages processed.")
    source_filename: Optional[str] = None
    analysis: DocumentAnalysisSchema = Field(default_factory=DocumentAnalysisSchema)
    pages: list[PageExtractionSchema] = Field(default_factory=list)


class ValidationJobResponse(BaseModel):
    job_id: str
    status: str
    message: str
    progress_current: int = 0
    progress_total: int = 0
    error: str | None = None
    result: DocumentValidationResponse | None = None
