export type AnalysisType = "text" | "vision";

export type WorkspaceTabKey = "source" | "extracted" | "text-analysis" | "visual-analysis";

export type StatusTone = "neutral" | "working" | "success" | "error";
/**
 * How the last validation run ended. A cancelled run drops page results silently rather than
 * emitting error rows, so the results view needs this to explain a coverage shortfall.
 */
export type RunOutcome = "completed" | "cancelled" | "failed" | null;

export interface DocumentTypeOptionProperty {
  type: string;
  title?: string;
  enum?: string[];
  enumLabels?: string[];
  format?: string;
}

export interface DocumentTypeDefinition {
  id: string;
  label: string;
  description: string;
  /** File formats the type accepts, e.g. ["pdf"] or ["xlsx", "xlsm"]. */
  accepted_formats: string[];
  options_schema: {
    type: string;
    properties: Record<string, DocumentTypeOptionProperty>;
    required?: string[];
  };
}

export interface DocumentTypesResponse {
  document_types: DocumentTypeDefinition[];
}

export type DocumentOptions = Record<string, string>;

export interface RuleDefinition {
  id: string;
  name: string;
  analysis_type: AnalysisType;
  /** LLM prompt for the rule; absent on deterministic (code-evaluated) workbook rules. */
  query?: string | null;
  description?: string | null;
  acceptance_criteria?: string | null;
  severity?: string | null;
  group?: string | null;
  section?: string | null;
  bypassable?: boolean;
  bypass?: boolean;
  tolerance?: number | null;
  check_method?: string | null;
  steps?: string | null;
  pass_criteria?: string | null;
  fail_criteria?: string | null;
  action_if_fail?: string | null;
  rationale?: string | null;
  document_types?: string[];
  event_types?: string[] | null;
  required_roles?: string[] | null;
  evaluator?: "llm" | "deterministic" | "hybrid";
}

export interface RulesResponse {
  rules: RuleDefinition[];
}

export interface ExtractedTable {
  index: number;
  rows: string[][];
}

export interface PageExtraction {
  page: number;
  /** Workbook documents: the sheet name (``page`` is then the sheet index). */
  label?: string | null;
  page_type?: string[];
  text: string;
  tables: ExtractedTable[];
  char_count: number;
}

export interface AnalysisMetric {
  label: string;
  value: string;
  detail?: string | null;
}

export interface AnalysisCitation {
  page: number;
  evidence: string;
  /** Workbook documents: sheet name and cell/range of the evidence. */
  sheet?: string | null;
  cell?: string | null;
}

export interface RuleAssessment {
  rule_id: string;
  rule_name: string;
  analysis_type: AnalysisType;
  execution_status: string;
  verdict: string;
  summary: string;
  reasoning: string;
  findings: string[];
  citations: AnalysisCitation[];
  matched_pages: number[];
  notes: string[];
  group?: string | null;
  bypassable?: boolean;
  bypass?: boolean;
  duration_ms?: number | null;
}

export interface PageRuleAssessment {
  page: number;
  label?: string | null;
  rule_id: string;
  rule_name: string;
  analysis_type: AnalysisType;
  /** Broad-scope rows report a synthetic `page`, so the locator label keys off this instead. */
  scope?: "page" | "multi_page" | "document";
  execution_status: string;
  verdict: string;
  summary: string;
  reasoning: string;
  findings: string[];
  citations: AnalysisCitation[];
  notes: string[];
  group?: string | null;
  bypassable?: boolean;
  bypass?: boolean;
  duration_ms?: number | null;
}

export interface PageObservation {
  page: number;
  observations: string[];
}

export interface DocumentAnalysis {
  overview: AnalysisMetric[];
  selected_rule_count: number;
  text_rule_count: number;
  vision_rule_count: number;
  rule_assessments: RuleAssessment[];
  text_page_results: PageRuleAssessment[];
  visual_page_results: PageRuleAssessment[];
  page_observations: PageObservation[];
}

export interface DocumentValidationResponse {
  document_id: string;
  document_type?: string;
  options?: DocumentOptions;
  page_count: number;
  source_filename?: string | null;
  analysis: DocumentAnalysis;
  pages: PageExtraction[];
}

export interface ValidationJobResponse {
  job_id: string;
  status: string;
  message: string;
  progress_current: number;
  progress_total: number;
  error?: string | null;
  result?: DocumentValidationResponse | null;
}

export interface WorkspaceStatus {
  label: string;
  tone: StatusTone;
  isLoading: boolean;
}

export interface FeedbackPayload {
  document_id: string;
  source_filename: string | null;
  page: number | null;
  rule_id: string;
  rule_name: string;
  analysis_type: AnalysisType;
  verdict: string;
  summary: string;
  reasoning: string;
  assessment: "correct" | "incorrect";
  comment: string;
}

export interface FeedbackResponse {
  status: string;
}
