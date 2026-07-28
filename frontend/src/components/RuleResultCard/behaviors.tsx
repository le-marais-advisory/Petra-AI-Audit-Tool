import { cn } from "@/utils/cn";

import type { AnalysisType } from "@/types/api";


export interface RuleResultLike {
  analysis_type: AnalysisType;
  citations: Array<{ page: number; evidence: string }>;
  execution_status: string;
  findings: string[];
  matched_pages?: number[];
  notes: string[];
  page?: number;
  reasoning: string;
  rule_id: string;
  rule_name: string;
  summary: string;
  verdict: string;
  duration_ms?: number | null;
}

export interface RuleResultCardProps {
  item: RuleResultLike;
  documentId: string | null;
  sourceFilename: string | null;
  /** Initial expanded state for the details section. Defaults to collapsed. */
  defaultExpanded?: boolean;
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

export function getVerdictClasses(verdict: string): string {
  return cn(
    "rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-wide",
    verdict === "pass" && "bg-emerald-100 text-emerald-700",
    verdict === "fail" && "bg-rose-100 text-rose-700",
    verdict === "not_applicable" && "bg-slate-200 text-slate-700",
    !["pass", "fail", "not_applicable"].includes(verdict) && "bg-amber-100 text-amber-700",
  );
}

export function getAnalysisTypeClasses(type: AnalysisType): string {
  return type === "vision"
    ? "rounded-full bg-violet-100 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-violet-700"
    : "rounded-full bg-cyan-100 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-cyan-700";
}
