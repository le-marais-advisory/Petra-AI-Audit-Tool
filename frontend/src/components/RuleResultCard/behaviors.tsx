import { VERDICT_FAIL, VERDICT_NEEDS_REVIEW, normalizeVerdict } from "@/utils/verdicts";

import type { AnalysisType } from "@/types/api";


export type RuleScope = "page" | "multi_page" | "document";

export interface RuleResultLike {
  analysis_type: AnalysisType;
  citations: Array<{ page: number; evidence: string }>;
  execution_status: string;
  findings: string[];
  notes: string[];
  page?: number;
  reasoning: string;
  rule_id: string;
  rule_name: string;
  scope?: RuleScope;
  summary: string;
  verdict: string;
  duration_ms?: number | null;
}

export interface RuleResultCardProps {
  item: RuleResultLike;
  documentId: string | null;
  sourceFilename: string | null;
  /** Whether the details disclosure is open. Owned by the parent so "Expand all" can drive every card. */
  expanded: boolean;
  onToggleExpanded: () => void;
}

/** Format an execution time in milliseconds as a compact label (e.g. "1.2s"). */
export function formatDuration(durationMs: number | null | undefined): string | null {
  if (durationMs === null || durationMs === undefined || Number.isNaN(durationMs)) {
    return null;
  }
  const seconds = durationMs / 1000;
  if (seconds >= 60) {
    const minutes = Math.floor(seconds / 60);
    const secs = Math.round(seconds % 60);
    return `${minutes}m ${secs}s`;
  }
  return `${seconds.toFixed(1)}s`;
}

/**
 * Whether the card has anything behind "Show details". Citations count even though they
 * sit behind their own nested toggle — otherwise a citations-only card would be unreachable.
 */
export function isCardExpandable(item: RuleResultLike): boolean {
  return Boolean(item.reasoning) || item.findings.length > 0 || item.notes.length > 0 || item.citations.length > 0;
}

/**
 * Where the finding lives. Broad-scope rules report a synthetic page — the first page of the
 * gathered set (`text_rule_analyzer._analyze_broad_scope_rules`) — so labelling those "Page 1"
 * would send a reviewer to the wrong page. Their real locations are in `citations`.
 */
export function getLocatorLabel(item: RuleResultLike): string | null {
  if (item.scope === "document") {
    return "Whole document";
  }
  if (item.scope === "multi_page") {
    return "Multiple pages";
  }
  return typeof item.page === "number" ? `Page ${item.page}` : null;
}

/** Citations are the evidence for an action item, so they start open on rows a reviewer must act on. */
export function shouldOpenCitations(item: RuleResultLike): boolean {
  const verdict = normalizeVerdict(item.verdict);
  return verdict === VERDICT_FAIL || verdict === VERDICT_NEEDS_REVIEW;
}

export function getAnalysisTypeClasses(type: AnalysisType): string {
  return type === "vision"
    ? "rounded-full bg-violet-100 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-violet-700"
    : "rounded-full bg-cyan-100 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-cyan-700";
}
