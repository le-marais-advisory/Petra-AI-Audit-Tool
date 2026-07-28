import { useCallback, useMemo, useState } from "react";

import { cn } from "@/utils/cn";
import {
  VERDICT_FAIL,
  VERDICT_NEEDS_REVIEW,
  VERDICT_NOT_APPLICABLE,
  VERDICT_PASS,
  isExecutionIncomplete,
  normalizeVerdict,
} from "@/utils/verdicts";

import type { AnalysisType, PageRuleAssessment, RuleAssessment, RunOutcome } from "@/types/api";


export interface AnalysisResultsProps {
  analysisType: AnalysisType;
  items: PageRuleAssessment[];
  emptyMessage: string;
  documentId: string | null;
  sourceFilename: string | null;
  /** Document-level ledger, one row per selected rule. Drives the coverage line. */
  ruleAssessments: RuleAssessment[];
  /** True while the job is still running and results are still streaming in. */
  isStreaming: boolean;
  runOutcome: RunOutcome;
}

export type ResultSectionKey = "attention" | "passed" | "not_applicable";

export type ResultSectionTone = "attention" | "clear" | "pass" | "muted";

/** One rendered card, paired with a key that is stable across poll ticks. */
export interface AnalysisResultRow {
  key: string;
  item: PageRuleAssessment;
}

export interface ResultSection {
  key: ResultSectionKey;
  /** Fully-formed heading, e.g. "Needs attention" or "12 passed". */
  title: string;
  tone: ResultSectionTone;
  rows: AnalysisResultRow[];
}

export interface CoverageSummary {
  evaluated: number;
  total: number;
  /** True when every selected rule of this analysis type produced a result. */
  reconciles: boolean;
  message: string;
}

export const ATTENTION_SECTION_KEY: ResultSectionKey = "attention";

/** Rule-level statuses meaning the rule produced a real outcome rather than never running. */
const EVALUATED_RULE_STATUSES: ReadonlySet<string> = new Set(["completed", "error", "not_applicable"]);

export function getAnalysisResultsHeading(analysisType: AnalysisType): string {
  return analysisType === "vision" ? "Visual Rule Assessments" : "Text Rule Assessments";
}

export function getResultCountLabel(count: number): string {
  return `${count} rule result${count === 1 ? "" : "s"}`;
}

/**
 * Bucket by exclusion, never by inclusion. `verdict` is a plain `string` on the wire and the
 * enum is enforced only at the LLM boundary, so an unrecognised value must still land
 * somewhere — anything we cannot classify is something a human should look at. This keeps
 * `attention + passed + not_applicable === items.length` structurally true.
 */
export function getSectionKey(item: PageRuleAssessment): ResultSectionKey {
  if (isExecutionIncomplete(item.execution_status)) {
    return "attention";
  }
  const verdict = normalizeVerdict(item.verdict);
  if (verdict === VERDICT_PASS) {
    return "passed";
  }
  if (verdict === VERDICT_NOT_APPLICABLE) {
    return "not_applicable";
  }
  return "attention";
}

/** Sort tier inside "Needs attention": fail, then needs_review, then did-not-run, then unknown. */
export function getAttentionRank(item: PageRuleAssessment): number {
  if (isExecutionIncomplete(item.execution_status)) {
    return 2;
  }
  const verdict = normalizeVerdict(item.verdict);
  if (verdict === VERDICT_FAIL) {
    return 0;
  }
  if (verdict === VERDICT_NEEDS_REVIEW) {
    return 1;
  }
  return 3;
}

function getPageNumber(item: PageRuleAssessment): number {
  return Number.isFinite(item.page) ? item.page : Number.MAX_SAFE_INTEGER;
}

function comparePageRows(left: AnalysisResultRow, right: AnalysisResultRow): number {
  // Key tiebreak, not just page: sort is stable, so without it same-page rows would keep
  // arrival order — thread-pool completion order, which differs between runs.
  return getPageNumber(left.item) - getPageNumber(right.item) || left.key.localeCompare(right.key);
}

function compareAttentionRows(left: AnalysisResultRow, right: AnalysisResultRow): number {
  return getAttentionRank(left.item) - getAttentionRank(right.item) || comparePageRows(left, right);
}

/**
 * Stable per-card keys. `page` + `rule_id` is unique in practice, but `rule_id` falls back to
 * "" in the pipeline, so identical bases get an occurrence suffix. MUST be computed over the
 * raw `items` array: the backend only appends to `page_results`, so occurrence indices never
 * shift between poll ticks. Never derive a key from an array index.
 */
export function buildResultRows(items: PageRuleAssessment[]): AnalysisResultRow[] {
  const seen = new Map<string, number>();
  const rows: AnalysisResultRow[] = [];
  for (const item of items) {
    const page = Number.isFinite(item.page) ? String(item.page) : "unknown";
    const base = `p${page}-${item.rule_id || "unknown-rule"}`;
    const occurrence = seen.get(base) || 0;
    seen.set(base, occurrence + 1);
    rows.push({ key: occurrence === 0 ? base : `${base}#${occurrence}`, item });
  }
  return rows;
}

export function buildResultSections(items: PageRuleAssessment[], isStreaming: boolean): ResultSection[] {
  // Record<ResultSectionKey, ...> makes TS reject a new section key that is not bucketed.
  const buckets: Record<ResultSectionKey, AnalysisResultRow[]> = {
    attention: [],
    passed: [],
    not_applicable: [],
  };
  for (const row of buildResultRows(items)) {
    buckets[getSectionKey(row.item)].push(row);
  }

  if (import.meta.env.DEV) {
    const bucketed = buckets.attention.length + buckets.passed.length + buckets.not_applicable.length;
    if (bucketed !== items.length) {
      console.error("Result bucketing dropped rows", { bucketed, items: items.length });
    }
  }

  buckets.attention.sort(compareAttentionRows);
  buckets.passed.sort(comparePageRows);
  buckets.not_applicable.sort(comparePageRows);

  const sections: ResultSection[] = [
    buckets.attention.length
      ? {
          key: "attention",
          title: isStreaming ? `Needs attention (${buckets.attention.length} so far)` : "Needs attention",
          tone: "attention",
          rows: buckets.attention,
        }
      : {
          key: "attention",
          title: isStreaming ? "Nothing needs attention yet" : "Nothing needs attention",
          tone: "clear",
          rows: [],
        },
    { key: "passed", title: `${buckets.passed.length} passed`, tone: "pass", rows: buckets.passed },
    {
      key: "not_applicable",
      title: `${buckets.not_applicable.length} not applicable`,
      tone: "muted",
      rows: buckets.not_applicable,
    },
  ];

  // Empty roll-ups are noise; the attention section always shows so the tab is never blank.
  return sections.filter((section) => section.key === ATTENTION_SECTION_KEY || section.rows.length > 0);
}

/** Hedged while streaming: an empty attention bucket mid-run only means nothing bad has arrived yet. */
export function getNothingToReviewMessage(totalCount: number, isStreaming: boolean): string {
  const results = `${totalCount} result${totalCount === 1 ? "" : "s"}`;
  return isStreaming
    ? `No failures, reviews, or incomplete rules among the ${results} received so far.`
    : `No failures, reviews, or incomplete rules across all ${results}.`;
}

function describeSection(section: ResultSection): string {
  if (!section.rows.length) {
    return "";
  }
  return section.key === ATTENTION_SECTION_KEY ? `${section.rows.length} need attention` : section.title;
}

/**
 * Coverage ledger. Derived from `rule_assessments` (one row per selected rule, always) rather
 * than `overview` (label/value strings that count rules, not rows). This is what makes the
 * roll-ups trustworthy: cancelling a run drops page rows entirely without emitting error rows,
 * so without this a stopped run would look like a clean one.
 */
export function buildCoverageSummary(
  ruleAssessments: RuleAssessment[],
  analysisType: AnalysisType,
  sections: ResultSection[],
  totalResults: number,
  isStreaming: boolean,
  runOutcome: RunOutcome,
): CoverageSummary {
  const scoped = ruleAssessments.filter((assessment) => assessment.analysis_type === analysisType);
  const evaluated = scoped.filter((assessment) =>
    EVALUATED_RULE_STATUSES.has((assessment.execution_status || "").trim().toLowerCase()),
  ).length;
  const total = scoped.length;
  const missing = Math.max(total - evaluated, 0);
  const label = analysisType === "vision" ? "vision" : "text";
  const ledger = `${evaluated} of ${total} ${label} rule${total === 1 ? "" : "s"} evaluated`;
  const counts = sections.map(describeSection).filter(Boolean);

  // Mid-run a shortfall is expected, so only flag a mismatch once the run has stopped.
  if (!missing || isStreaming) {
    return {
      evaluated,
      total,
      reconciles: true,
      message: [ledger, getResultCountLabel(totalResults), ...counts].join(" · "),
    };
  }

  const cause =
    runOutcome === "cancelled"
      ? " Run was stopped before completion."
      : runOutcome === "failed"
        ? " Run failed before completion."
        : "";
  return {
    evaluated,
    total,
    reconciles: false,
    message: `${ledger} — ${missing} rule${missing === 1 ? "" : "s"} produced no result.${cause}`,
  };
}

export function getCoverageClasses(reconciles: boolean): string {
  return cn(
    "rounded-[1.25rem] border px-4 py-3 text-sm",
    reconciles && "border-slate-200 bg-slate-50 text-slate-500",
    !reconciles && "border-amber-200 bg-amber-50 text-amber-800",
  );
}

export function getSectionPanelClasses(tone: ResultSectionTone): string {
  return cn(
    "rounded-[1.5rem] border p-5",
    tone === "attention" && "border-amber-200 bg-amber-50/50",
    tone === "clear" && "border-emerald-200 bg-emerald-50/50",
    (tone === "pass" || tone === "muted") && "border-slate-200 bg-slate-50",
  );
}

export function getSectionBodyClasses(tone: ResultSectionTone): string {
  // The divider lives on the body, not the header, so a collapsed section has no dangling rule.
  return cn(
    "mt-4 space-y-4 border-t pt-4",
    tone === "attention" && "border-amber-200",
    tone === "clear" && "border-emerald-200",
    (tone === "pass" || tone === "muted") && "border-slate-200",
  );
}

export function getSectionChipClasses(tone: ResultSectionTone): string {
  return cn(
    "rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-wide",
    tone === "attention" && "bg-amber-100 text-amber-700",
    (tone === "clear" || tone === "pass") && "bg-emerald-100 text-emerald-700",
    tone === "muted" && "bg-slate-200 text-slate-700",
  );
}

export function useAnalysisResultsBehavior(items: PageRuleAssessment[], isStreaming: boolean) {
  const sections = useMemo(() => buildResultSections(items, isStreaming), [items, isStreaming]);

  // Presence in `openSections` means "open", but only for sections the user has touched —
  // `manuallyToggled` records those. Until then the default applies: roll-ups closed, and
  // "Needs attention" open only once the run has stopped. Expanding it mid-run would invite
  // reading a list that re-sorts under the cursor on every poll tick.
  const [openSections, setOpenSections] = useState<ReadonlySet<ResultSectionKey>>(() => new Set<ResultSectionKey>());
  const [manuallyToggled, setManuallyToggled] = useState<ReadonlySet<ResultSectionKey>>(() => new Set<ResultSectionKey>());
  // Keys of cards whose details disclosure is open. Empty set == everything collapsed.
  const [expandedRows, setExpandedRows] = useState<ReadonlySet<string>>(() => new Set<string>());

  const isSectionOpen = useCallback(
    (key: ResultSectionKey) =>
      manuallyToggled.has(key) ? openSections.has(key) : key === ATTENTION_SECTION_KEY && !isStreaming,
    [isStreaming, manuallyToggled, openSections],
  );

  const expandableKeys = useMemo(
    () => sections.filter((section) => isSectionOpen(section.key)).flatMap((section) => section.rows.map((row) => row.key)),
    [isSectionOpen, sections],
  );

  const toggleSection = useCallback(
    (key: ResultSectionKey) => {
      const currentlyOpen = isSectionOpen(key);
      setManuallyToggled((current) => new Set(current).add(key));
      setOpenSections((current) => {
        const next = new Set(current);
        if (currentlyOpen) {
          next.delete(key);
        } else {
          next.add(key);
        }
        return next;
      });
    },
    [isSectionOpen],
  );

  const toggleRow = useCallback((key: string) => {
    setExpandedRows((current) => {
      const next = new Set(current);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }, []);

  // One-shot, not a latch. It touches only `expandedRows` — never `openSections` — which is
  // what guarantees "Expand all" can never dump the rolled-up passes back on screen. It seeds
  // only rows in sections that are currently open.
  const toggleAll = useCallback(() => {
    setExpandedRows((current) => (current.size > 0 ? new Set<string>() : new Set(expandableKeys)));
  }, [expandableKeys]);

  const anyExpanded = expandedRows.size > 0;

  return {
    anyExpanded,
    canToggleAll: anyExpanded || expandableKeys.length > 0,
    expandedRows,
    isSectionOpen,
    sections,
    toggleAll,
    toggleRow,
    toggleSection,
  };
}
