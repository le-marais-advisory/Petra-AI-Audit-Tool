import { useState } from "react";

import { EmptyState } from "@/components/EmptyState";
import { RuleResultCard } from "@/components/RuleResultCard";

import { getPageResultsHeading, groupItemsByPage, type PageRuleResultsProps } from "./behaviors";


export function PageRuleResults({ analysisType, emptyMessage, items, documentId, sourceFilename }: PageRuleResultsProps) {
  // Cards default to collapsed. "Expand all"/"Collapse all" flips the default and
  // bumps `resetKey`, which remounts the cards so they pick up the new default
  // while still allowing per-card toggling afterwards.
  const [expandAll, setExpandAll] = useState(false);
  const [resetKey, setResetKey] = useState(0);

  if (!items.length) {
    return (
      <article className="rounded-[1.5rem] border border-slate-200 bg-white p-5 text-sm text-slate-500">
        {emptyMessage}
      </article>
    );
  }

  const groups = groupItemsByPage(items);

  const toggleAll = () => {
    setExpandAll((value) => !value);
    setResetKey((value) => value + 1);
  };

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-base font-semibold text-slate-950">{getPageResultsHeading(analysisType)}</h3>
        <div className="flex items-center gap-3">
          <span className="text-sm text-slate-500">{items.length} page-rule result(s)</span>
          <button
            type="button"
            onClick={toggleAll}
            className="rounded-full border border-slate-200 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-slate-500 transition hover:border-slate-300 hover:text-slate-800"
          >
            {expandAll ? "Collapse all" : "Expand all"}
          </button>
        </div>
      </div>

      {groups.map((group) => (
        <section key={`${analysisType}-page-${group.page}`} className="rounded-[1.5rem] border border-slate-200 bg-white p-5">
          <div className="flex items-center justify-between gap-3 border-b border-slate-200 pb-4">
            <h4 className="text-base font-semibold text-slate-950">Page {group.page}</h4>
            <span className="text-sm text-slate-500">{group.items.length} rule result(s)</span>
          </div>

          <div className="mt-4 space-y-4">
            {group.items.map((item) => (
              <RuleResultCard
                key={`${analysisType}-${group.page}-${item.rule_id}-${resetKey}`}
                item={item}
                documentId={documentId}
                sourceFilename={sourceFilename}
                defaultExpanded={expandAll}
              />
            ))}
          </div>
        </section>
      ))}
    </section>
  );
}
