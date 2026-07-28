import { DisclosureToggle } from "@/components/DisclosureToggle";
import { RuleResultCard } from "@/components/RuleResultCard";

import {
  buildCoverageSummary,
  getAnalysisResultsHeading,
  getCoverageClasses,
  getNothingToReviewMessage,
  getResultCountLabel,
  getSectionBodyClasses,
  getSectionChipClasses,
  getSectionPanelClasses,
  useAnalysisResultsBehavior,
  type AnalysisResultsProps,
} from "./behaviors";


export function AnalysisResults({
  analysisType,
  emptyMessage,
  items,
  documentId,
  sourceFilename,
  ruleAssessments,
  isStreaming,
  runOutcome,
}: AnalysisResultsProps) {
  // Called above the early return below — hook order must not depend on whether there are items.
  const { anyExpanded, canToggleAll, expandedRows, isSectionOpen, sections, toggleAll, toggleRow, toggleSection } =
    useAnalysisResultsBehavior(items, isStreaming);

  if (!items.length) {
    return (
      <article className="rounded-[1.5rem] border border-slate-200 bg-white p-5 text-sm text-slate-500">
        {emptyMessage}
      </article>
    );
  }

  const coverage = buildCoverageSummary(ruleAssessments, analysisType, sections, items.length, isStreaming, runOutcome);

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-base font-semibold text-slate-950">{getAnalysisResultsHeading(analysisType)}</h3>
        <button
          type="button"
          onClick={toggleAll}
          disabled={!canToggleAll}
          className="rounded-full border border-slate-200 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-slate-500 transition hover:border-slate-300 hover:text-slate-800 disabled:opacity-50"
        >
          {anyExpanded ? "Collapse all" : "Expand all"}
        </button>
      </div>

      <p className={getCoverageClasses(coverage.reconciles)}>{coverage.message}</p>

      {sections.map((section) => {
        const open = isSectionOpen(section.key);
        const bodyId = `${analysisType}-${section.key}-body`;

        return (
          <section key={section.key} aria-label={section.title} className={getSectionPanelClasses(section.tone)}>
            <h4>
              <DisclosureToggle
                tone="section"
                expanded={open}
                onToggle={() => toggleSection(section.key)}
                label={isStreaming && section.key === "attention" ? `${section.title} — analysis in progress` : section.title}
                controls={bodyId}
                trailing={
                  section.rows.length ? (
                    <span className={getSectionChipClasses(section.tone)}>{getResultCountLabel(section.rows.length)}</span>
                  ) : null
                }
              />
            </h4>

            {open ? (
              <div id={bodyId} className={getSectionBodyClasses(section.tone)}>
                {section.rows.length ? (
                  section.rows.map((row) => (
                    <RuleResultCard
                      key={row.key}
                      item={row.item}
                      documentId={documentId}
                      sourceFilename={sourceFilename}
                      expanded={expandedRows.has(row.key)}
                      onToggleExpanded={() => toggleRow(row.key)}
                    />
                  ))
                ) : (
                  <p className="rounded-[1.25rem] border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
                    {getNothingToReviewMessage(items.length, isStreaming)}
                  </p>
                )}
              </div>
            ) : null}
          </section>
        );
      })}
    </section>
  );
}
