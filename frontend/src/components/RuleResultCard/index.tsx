import { useState } from "react";

import { FeedbackModal } from "@/components/FeedbackModal";

import { formatDuration, getAnalysisTypeClasses, getVerdictClasses, type RuleResultCardProps } from "./behaviors";


export function RuleResultCard({ item, documentId, sourceFilename, defaultExpanded = false }: RuleResultCardProps) {
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [expanded, setExpanded] = useState(defaultExpanded);

  const durationLabel = formatDuration(item.duration_ms);
  const hasDetails = Boolean(item.reasoning) || item.findings.length > 0 || item.citations.length > 0 || item.notes.length > 0;

  return (
    <article className="rounded-[1.5rem] border border-slate-200 bg-white p-5">
      <div className="flex flex-wrap items-center gap-3">
        <h4 className="text-base font-semibold text-slate-950">{item.rule_name || item.rule_id}</h4>
        <span className={getAnalysisTypeClasses(item.analysis_type)}>{item.analysis_type}</span>
        <span className={getVerdictClasses(item.verdict)}>{item.verdict}</span>
        <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-slate-600">
          {item.execution_status}
        </span>
        {item.matched_pages ? (
          <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-slate-600">
            {item.matched_pages.length} page match{item.matched_pages.length === 1 ? "" : "es"}
          </span>
        ) : null}
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

      {hasDetails ? (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          className="mt-3 flex items-center gap-1.5 rounded-full text-xs font-semibold uppercase tracking-wide text-slate-500 transition hover:text-slate-800"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 16 16"
            fill="currentColor"
            className={`h-3.5 w-3.5 transition-transform ${expanded ? "rotate-90" : ""}`}
          >
            <path d="M6.22 3.22a.75.75 0 0 1 1.06 0l4.25 4.25a.75.75 0 0 1 0 1.06l-4.25 4.25a.75.75 0 0 1-1.06-1.06L9.94 8 6.22 4.28a.75.75 0 0 1 0-1.06Z" />
          </svg>
          {expanded ? "Hide details" : "Show details"}
        </button>
      ) : null}

      {hasDetails && expanded ? (
        <div>
          {item.reasoning ? <p className="mt-3 text-sm leading-6 text-slate-600">{item.reasoning}</p> : null}

          {item.findings.length ? (
            <div className="mt-4">
              <h5 className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Findings</h5>
              <ul className="mt-3 space-y-2">
                {item.findings.map((finding, index) => (
                  <li key={`${item.rule_id}-finding-${index}`} className="rounded-xl bg-slate-50 px-3 py-2 text-sm text-slate-600">
                    {finding}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {item.citations.length ? (
            <div className="mt-4">
              <h5 className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Citations</h5>
              <ul className="mt-3 space-y-2">
                {item.citations.map((citation, index) => (
                  <li key={`${item.rule_id}-citation-${index}`} className="rounded-xl bg-slate-50 px-3 py-2 text-sm text-slate-600">
                    Page {citation.page}: {citation.evidence}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {item.notes.length ? (
            <div className="mt-4">
              <h5 className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Notes</h5>
              <ul className="mt-3 space-y-2">
                {item.notes.map((note, index) => (
                  <li key={`${item.rule_id}-note-${index}`} className="rounded-xl bg-slate-50 px-3 py-2 text-sm text-slate-600">
                    {note}
                  </li>
                ))}
              </ul>
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
