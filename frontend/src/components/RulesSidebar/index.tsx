import { useMemo } from "react";

import { RuleSelectionCard } from "@/components/RuleSelectionCard";

import {
  getGroupSelectionState,
  getSelectAllLabel,
  getSelectionLabel,
  groupRules,
  type RulesSidebarProps,
} from "./behaviors";


export function RulesSidebar({
  errorMessage,
  emptyMessage,
  onRefresh,
  onRuleToggle,
  onBypassToggle,
  onGroupToggle,
  onSelectAll,
  rules,
  selectedRuleIds,
  bypassedRuleIds,
}: RulesSidebarProps) {
  const selectedCount = selectedRuleIds.length;
  const totalCount = rules.length;

  const groups = useMemo(() => groupRules(rules), [rules]);

  return (
    <article className="section-panel p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-950">Validation Rules</h2>
          <p className="mt-1 text-sm text-slate-500">Enable the rules you want to include in the analysis.</p>
        </div>

        <button
          className="rounded-full border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:border-slate-400 hover:bg-slate-50"
          type="button"
          onClick={() => {
            void onRefresh();
          }}
        >
          Refresh
        </button>
      </div>

      <div className="mt-5 flex items-center justify-between rounded-[1.25rem] bg-slate-50 px-4 py-3">
        <p className="text-sm text-slate-500">
          <span className="font-semibold text-slate-900">{getSelectionLabel(selectedCount, totalCount)}</span>
        </p>
        <button
          className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500"
          type="button"
          onClick={onSelectAll}
        >
          {getSelectAllLabel(selectedCount, totalCount)}
        </button>
      </div>

      <div className="mt-5 space-y-5">
        {errorMessage ? <p className="rounded-2xl bg-rose-50 px-4 py-3 text-sm text-rose-700">{errorMessage}</p> : null}

        {!rules.length && !errorMessage ? (
          <p className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-500">{emptyMessage || "No rules available."}</p>
        ) : null}

        {groups.map((group) => {
          const { selected, total, allSelected } = getGroupSelectionState(group, selectedRuleIds);
          return (
            <section key={group.key} className="space-y-2">
              <div className="flex items-center justify-between gap-3 border-b border-slate-200 pb-2">
                <div className="flex items-baseline gap-2">
                  <h3 className="text-sm font-semibold uppercase tracking-[0.14em] text-slate-700">
                    {group.label}
                  </h3>
                  <span className="text-xs text-slate-500">
                    {selected}/{total}
                  </span>
                </div>
                <button
                  className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500 hover:text-slate-800"
                  type="button"
                  onClick={() => onGroupToggle(group.rules.map((r) => r.id))}
                >
                  {allSelected ? "Clear" : "All"}
                </button>
              </div>

              <div className="space-y-2">
                {group.rules.map((rule) => (
                  <RuleSelectionCard
                    key={rule.id}
                    checked={selectedRuleIds.includes(rule.id)}
                    bypassed={bypassedRuleIds.includes(rule.id)}
                    rule={rule}
                    onToggle={onRuleToggle}
                    onBypassToggle={onBypassToggle}
                  />
                ))}
              </div>
            </section>
          );
        })}
      </div>
    </article>
  );
}
