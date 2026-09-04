import { cn } from "@/utils/cn";

import type { RuleDefinition } from "@/types/api";


export interface RuleSelectionCardProps {
  checked: boolean;
  bypassed: boolean;
  rule: RuleDefinition;
  onToggle: (ruleId: string) => void;
  onBypassToggle: (ruleId: string) => void;
}

export function getRuleTypeClasses(type: RuleDefinition["analysis_type"]): string {
  return type === "vision"
    ? "bg-violet-100 text-violet-700"
    : "bg-blue-100 text-blue-700";
}

export function getCardClasses(checked: boolean): string {
  return cn(
    "block w-full cursor-pointer rounded-[1.4rem] border p-4 transition",
    checked ? "border-blue-300 bg-blue-50/70" : "border-slate-200 bg-slate-50",
  );
}
