import { cn } from "@/utils/cn";


export const VERDICT_PASS = "pass";
export const VERDICT_FAIL = "fail";
export const VERDICT_NEEDS_REVIEW = "needs_review";
export const VERDICT_NOT_APPLICABLE = "not_applicable";

/**
 * Display labels for verdicts. Mirrors `_VERDICT_LABELS` in `src/api/routers/export.py`
 * so the web UI and the exported PDF never disagree. Stored in sentence case; badge
 * styling applies `uppercase`, so "Needs review" renders as "NEEDS REVIEW".
 */
const VERDICT_LABELS: Record<string, string> = {
  pass: "Pass",
  fail: "Fail",
  needs_review: "Needs review",
  not_applicable: "N/A",
  skipped: "Skipped",
};

const EXECUTION_STATUS_LABELS: Record<string, string> = {
  completed: "Completed",
  error: "Error",
  skipped: "Skipped",
  running: "Running",
  pending: "Pending",
  not_applicable: "N/A",
};

/**
 * Statuses meaning "the rule reached a deliberate terminal state". Everything else —
 * including values we have never seen — counts as incomplete, so it surfaces rather
 * than hiding behind the backend's `needs_review` fallback verdict.
 */
const SETTLED_EXECUTION_STATUSES: ReadonlySet<string> = new Set(["completed", "not_applicable"]);

/** "needs_review" -> "Needs review". Fallback for tokens we have no label for. */
export function humanizeToken(value: string): string {
  const words = value.split(/[_\s-]+/).filter(Boolean);
  if (!words.length) {
    return "Unknown";
  }
  const [first, ...rest] = words;
  return [first.charAt(0).toUpperCase() + first.slice(1).toLowerCase(), ...rest.map((word) => word.toLowerCase())].join(" ");
}

/** Casing/whitespace drift on a `string`-typed field is free to absorb, so absorb it. */
export function normalizeVerdict(verdict: string | null | undefined): string {
  return (verdict || "").trim().toLowerCase();
}

export function getVerdictLabel(verdict: string): string {
  return VERDICT_LABELS[normalizeVerdict(verdict)] || humanizeToken(verdict);
}

export function getExecutionStatusLabel(status: string): string {
  return EXECUTION_STATUS_LABELS[status] || humanizeToken(status);
}

/** True when the rule did not reach a deliberate terminal state (error, skipped, unknown). */
export function isExecutionIncomplete(status: string | null | undefined): boolean {
  return !SETTLED_EXECUTION_STATUSES.has((status || "").trim().toLowerCase());
}

export function getVerdictClasses(verdict: string): string {
  const value = normalizeVerdict(verdict);
  return cn(
    "rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-wide",
    value === VERDICT_PASS && "bg-emerald-100 text-emerald-700",
    value === VERDICT_FAIL && "bg-rose-100 text-rose-700",
    value === VERDICT_NOT_APPLICABLE && "bg-slate-200 text-slate-700",
    ![VERDICT_PASS, VERDICT_FAIL, VERDICT_NOT_APPLICABLE].includes(value) && "bg-amber-100 text-amber-700",
  );
}
