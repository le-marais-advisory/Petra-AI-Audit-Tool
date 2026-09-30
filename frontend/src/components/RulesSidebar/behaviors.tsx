import type { RuleDefinition } from "@/types/api";


export interface RulesSidebarProps {
  rules: RuleDefinition[];
  selectedRuleIds: string[];
  bypassedRuleIds: string[];
  onRuleToggle: (ruleId: string) => void;
  onBypassToggle: (ruleId: string) => void;
  onGroupToggle: (ruleIds: string[]) => void;
  onSelectAll: () => void;
  onRefresh: () => Promise<void>;
  errorMessage?: string | null;
  /** Shown instead of "No rules available." when there are no rules to list. */
  emptyMessage?: string;
}

export interface RuleGroup {
  key: string;
  label: string;
  rules: RuleDefinition[];
}

const UNCATEGORIZED_KEY = "__uncategorized__";

export function getSelectionLabel(selectedCount: number, totalCount: number): string {
  return `${selectedCount} of ${totalCount} selected`;
}

export function getSelectAllLabel(selectedCount: number, totalCount: number): string {
  return selectedCount === totalCount && totalCount > 0 ? "Clear All" : "Select All";
}

export function humanizeGroupLabel(key: string): string {
  if (key === UNCATEGORIZED_KEY) {
    return "Uncategorized";
  }
  return key
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

export function groupRules(rules: RuleDefinition[]): RuleGroup[] {
  const buckets = new Map<string, RuleDefinition[]>();
  for (const rule of rules) {
    const key = rule.group?.trim() || UNCATEGORIZED_KEY;
    const list = buckets.get(key);
    if (list) {
      list.push(rule);
    } else {
      buckets.set(key, [rule]);
    }
  }

  const groups: RuleGroup[] = [];
  for (const [key, groupRulesList] of buckets.entries()) {
    groups.push({
      key,
      label: humanizeGroupLabel(key),
      rules: groupRulesList,
    });
  }
  // Stable sort: alphabetical, but Uncategorized last
  groups.sort((a, b) => {
    if (a.key === UNCATEGORIZED_KEY) return 1;
    if (b.key === UNCATEGORIZED_KEY) return -1;
    return a.label.localeCompare(b.label);
  });
  return groups;
}

export function getGroupSelectionState(
  group: RuleGroup,
  selectedRuleIds: string[],
): { selected: number; total: number; allSelected: boolean } {
  const total = group.rules.length;
  const selected = group.rules.filter((rule) => selectedRuleIds.includes(rule.id)).length;
  return { selected, total, allSelected: selected === total && total > 0 };
}
