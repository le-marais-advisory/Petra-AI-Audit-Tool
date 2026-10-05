from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ExtractedTableSchema(BaseModel):
    index: int = Field(..., description="1-based table index within the page.")
    rows: list[list[str]] = Field(default_factory=list, description="Normalized table rows extracted from the page.")


class PageExtractionSchema(BaseModel):
    page: int = Field(..., description="1-based page number (PDF) or sheet index (workbook).")
    label: Optional[str] = Field(default=None, description="Display label for the unit, e.g. the sheet name.")
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
    sheet: Optional[str] = None
    cell: Optional[str] = None


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
    duration_ms: Optional[float] = Field(
        default=None,
        description="Total LLM execution time attributed to this rule, in milliseconds.",
    )
    llm_model: Optional[str] = Field(default=None, description="Model the rule's LLM calls were sent to.")
    llm_effort: Optional[str] = Field(default=None, description="Effort sent with them; None is the model default.")


class PageRuleAssessmentSchema(BaseModel):
    page: int
    label: Optional[str] = None
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
    duration_ms: Optional[float] = Field(
        default=None,
        description="LLM execution time for this page-rule evaluation, in milliseconds.",
    )


class DocumentAnalysisSchema(BaseModel):
    overview: list[AnalysisMetricSchema] = Field(default_factory=list)
    selected_rule_count: int = 0
    text_rule_count: int = 0
    vision_rule_count: int = 0
    rule_assessments: list[RuleAssessmentSchema] = Field(default_factory=list)
    text_page_results: list[PageRuleAssessmentSchema] = Field(default_factory=list)
    visual_page_results: list[PageRuleAssessmentSchema] = Field(default_factory=list)
    page_observations: list[PageAnalysisSchema] = Field(default_factory=list)


class LlmUsageRowSchema(BaseModel):
    label: str
    calls: int = 0
    input_tokens: int = Field(default=0, description="All prompt tokens billed, cached or not.")
    cache_read_tokens: int = Field(default=0, description="Prompt tokens served from the cache (part of input_tokens).")
    cache_creation_tokens: int = Field(
        default=0, description="Prompt tokens written to the cache (part of input_tokens)."
    )
    output_tokens: int = Field(default=0, description="All generated tokens, thinking included.")
    reasoning_tokens: int = Field(default=0, description="Thinking share of output_tokens.")
    cost_usd: Optional[float] = Field(
        default=None, description="Priced from config/models.yaml; None when a call's model is not listed there."
    )


class LlmUsageSchema(BaseModel):
    totals: LlmUsageRowSchema = Field(default_factory=lambda: LlmUsageRowSchema(label="All calls"))
    by_stage: list[LlmUsageRowSchema] = Field(default_factory=list)
    by_model: list[LlmUsageRowSchema] = Field(default_factory=list)
    by_rule: list[LlmUsageRowSchema] = Field(default_factory=list)


class DocumentValidationResponse(BaseModel):
    document_id: str
    document_type: str = Field(default="financial_statements", description="Document type selected for the run.")
    options: dict = Field(default_factory=dict, description="Run options for the document type (e.g. event_type).")
    page_count: int = Field(default=0, description="Total number of pages processed.")
    source_filename: Optional[str] = None
    analysis: DocumentAnalysisSchema = Field(default_factory=DocumentAnalysisSchema)
    pages: list[PageExtractionSchema] = Field(default_factory=list)
    llm_usage: Optional[LlmUsageSchema] = Field(
        default=None, description="LLM token consumption for the run; set once the run has finished."
    )


class ValidationJobResponse(BaseModel):
    job_id: str
    status: str
    message: str
    progress_current: int = 0
    progress_total: int = 0
    error: str | None = None
    result: DocumentValidationResponse | None = None
