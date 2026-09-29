import type { AnalysisMetric, PageExtraction } from "@/types/api";


export interface SourcePreviewProps {
  previewUrl: string | null;
  sourceFilename?: string | null;
  /** Workbook documents are summarised as a sheet inventory instead of an inline preview. */
  isWorkbook?: boolean;
  pages?: PageExtraction[];
  overview?: AnalysisMetric[];
}

const ROLE_LABELS: Record<string, string> = {
  allocation: "Allocation",
  itd: "ITD capital activity",
  summary: "Summary",
  merge: "Merge (notice data)",
  mgmt_fee: "Management fee",
  investor_data: "Investor data",
  holiday_calendar: "Holiday calendar",
  other: "Other",
};

export function describeRole(role: string | undefined): string {
  return (role && ROLE_LABELS[role]) || role || "Unclassified";
}

export function findMetric(overview: AnalysisMetric[] | undefined, label: string): AnalysisMetric | undefined {
  return overview?.find((metric) => metric.label === label);
}
