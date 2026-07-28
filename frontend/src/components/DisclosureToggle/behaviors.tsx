import { cn } from "@/utils/cn";

import type { ReactNode } from "react";


export type DisclosureToggleTone = "inline" | "section";

export interface DisclosureToggleProps {
  expanded: boolean;
  onToggle: () => void;
  /** Fully-formed label. The caller owns any count, e.g. "Citations (3)" or "12 passed". */
  label: string;
  /** Right-aligned content inside the button; section headers use it for a count chip. */
  trailing?: ReactNode;
  tone?: DisclosureToggleTone;
  /** DOM id of the region this button reveals, for aria-controls. */
  controls?: string;
}

// `cn` is a plain join, so conflicting utilities never go in the base string —
// a base `gap-1.5` plus a branch `gap-3` would be decided by Tailwind's own order.
export function getDisclosureButtonClasses(tone: DisclosureToggleTone): string {
  return cn(
    "flex items-center transition",
    tone === "inline" && "gap-1.5 rounded-full text-xs font-semibold uppercase tracking-wide text-slate-500 hover:text-slate-800",
    tone === "section" && "w-full gap-3 text-left text-base font-semibold text-slate-950 hover:text-slate-700",
  );
}

export function getDisclosureChevronClasses(tone: DisclosureToggleTone, expanded: boolean): string {
  return cn(
    "shrink-0 transition-transform",
    tone === "inline" ? "h-3.5 w-3.5" : "h-4 w-4 text-slate-400",
    expanded && "rotate-90",
  );
}
