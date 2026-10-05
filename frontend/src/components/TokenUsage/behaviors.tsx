import type { LlmUsage, LlmUsageRow } from "@/types/api";


export interface TokenUsageProps {
  usage: LlmUsage | null | undefined;
  isBusy: boolean;
  /** Rule names by rule ID, so the per-rule table can show more than the ID. */
  ruleNames: Record<string, string>;
}

export type UsageColumnKey = keyof Omit<LlmUsageRow, "label">;

export interface UsageColumn {
  key: UsageColumnKey;
  label: string;
}

const allColumns: UsageColumn[] = [
  { key: "calls", label: "Calls" },
  { key: "input_tokens", label: "Input" },
  { key: "cache_read_tokens", label: "Cached input" },
  { key: "cache_creation_tokens", label: "Cache writes" },
  { key: "output_tokens", label: "Output" },
  { key: "reasoning_tokens", label: "Reasoning" },
];

/** Cache-write and reasoning counts are provider-specific; drop a column when the run never reported it. */
export function visibleColumns(totals: LlmUsageRow): UsageColumn[] {
  return allColumns.filter(
    (column) => !["cache_creation_tokens", "reasoning_tokens"].includes(column.key) || totals[column.key] > 0,
  );
}

export function formatTokens(value: number): string {
  return value.toLocaleString("en-US");
}

export function cachedShare(row: LlmUsageRow): string {
  if (!row.input_tokens) {
    return "0%";
  }
  return `${((row.cache_read_tokens / row.input_tokens) * 100).toFixed(1)}%`;
}
