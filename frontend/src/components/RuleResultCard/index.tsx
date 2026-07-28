import { useId, useState } from "react";

import { DisclosureToggle } from "@/components/DisclosureToggle";
import { FeedbackModal } from "@/components/FeedbackModal";
import { getExecutionStatusLabel, getVerdictClasses, getVerdictLabel, isExecutionIncomplete } from "@/utils/verdicts";

import {
  formatDuration,
  getAnalysisTypeClasses,
  getLocatorLabel,
  isCardExpandable,
  shouldOpenCitations,
  type RuleResultCardProps,
} from "./behaviors";


export function RuleResultCard({ item, documentId, sourceFilename, expanded, onToggleExpanded }: RuleResultCardProps) {
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [citationsOpen, setCitationsOpen] = useState(() => shouldOpenCitations(item));
  const instanceId = useId();

  const durationLabel = formatDuration(item.duration_ms);
  const locatorLabel = getLocatorLabel(item);
  const expandable = isCardExpandable(item);
  // An incomplete rule carries `needs_review` as a backend fallback, not as a judgment —
  // showing it as a verdict would be misinformation, so the status badge replaces it.
  const incomplete = isExecutionIncomplete(item.execution_status);
  const detailsId = `${instanceId}-details`;
  const citationsId = `${instanceId}-citations`;

  return (
    <article className="rounded-[1.25rem] border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h5 className="text-base font-semibold text-slate-950">{item.rule_name || item.rule_id}</h5>
        {locatorLabel ? (
          <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-slate-600">
            {locatorLabel}
          </span>
        ) : null}
        <span className={getAnalysisTypeClasses(item.analysis_type)}>{item.analysis_type}</span>
        {incomplete ? (
          <span
            className="rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-amber-700"
            title={`This rule did not finish (${getExecutionStatusLabel(item.execution_status)}). No verdict was reached — re-run the analysis.`}
          >
            Not run
          </span>
        ) : (
          <span className={getVerdictClasses(item.verdict)}>{getVerdictLabel(item.verdict)}</span>
        )}
        {durationLabel ? (
          <span
            className="rounded-full bg-slate-50 px-3 py-1 text-xs font-medium tabular-nums text-slate-400"
            title="Total LLM execution time for this rule"
          >
            {durationLabel}
          </span>
        ) : null}
        <button
          type="button"
          onClick={() => setFeedbackOpen(true)}
          className="ml-auto rounded-full bg-slate-100 p-2 text-slate-400 transition hover:bg-slate-200 hover:text-slate-600"
          title="Submit feedback on this assessment"
        >
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" className="h-4 w-4">
            <path d="M2.75 2a.75.75 0 0 0-.75.75v10.5a.75.75 0 0 0 1.5 0v-3.5h3.75l.25 1h4.5a.75.75 0 0 0 .75-.75v-5a.75.75 0 0 0-.75-.75H8.75L8.5 3H3.5V2.75A.75.75 0 0 0 2.75 2Z" />
          </svg>
        </button>
      </div>

      <p className="mt-4 text-sm font-medium text-slate-900">{item.summary}</p>

      {expandable ? (
        <div className="mt-3">
          <DisclosureToggle
            expanded={expanded}
            onToggle={onToggleExpanded}
            label={expanded ? "Hide details" : "Show details"}
            controls={detailsId}
          />
        </div>
      ) : null}

      {expandable && expanded ? (
        <div id={detailsId}>
          {item.reasoning ? <p className="mt-3 text-sm leading-6 text-slate-600">{item.reasoning}</p> : null}

          {item.findings.length ? (
            <div className="mt-4">
              <h6 className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Findings</h6>
              <ul className="mt-3 space-y-2">
                {item.findings.map((finding, index) => (
                  <li key={`${item.rule_id}-finding-${index}`} className="rounded-xl bg-slate-50 px-3 py-2 text-sm text-slate-600">
                    {finding}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {item.notes.length ? (
            <div className="mt-4">
              <h6 className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Notes</h6>
              <ul className="mt-3 space-y-2">
                {item.notes.map((note, index) => (
                  <li key={`${item.rule_id}-note-${index}`} className="rounded-xl bg-slate-50 px-3 py-2 text-sm text-slate-600">
                    {note}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {item.citations.length ? (
            <div className="mt-4 border-t border-slate-200 pt-4">
              <DisclosureToggle
                expanded={citationsOpen}
                onToggle={() => setCitationsOpen((value) => !value)}
                label={`Citations (${item.citations.length})`}
                controls={citationsId}
              />
              {citationsOpen ? (
                <ul id={citationsId} className="mt-3 space-y-2">
                  {item.citations.map((citation, index) => (
                    <li key={`${item.rule_id}-citation-${index}`} className="rounded-xl bg-slate-50 px-3 py-2 text-sm text-slate-600">
                      Page {citation.page}: {citation.evidence}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

      <FeedbackModal
        isOpen={feedbackOpen}
        onClose={() => setFeedbackOpen(false)}
        payload={{
          document_id: documentId || "",
          source_filename: sourceFilename,
          page: item.page ?? null,
          rule_id: item.rule_id,
          rule_name: item.rule_name,
          analysis_type: item.analysis_type,
          verdict: item.verdict,
          summary: item.summary,
          reasoning: item.reasoning,
        }}
      />
    </article>
  );
}
