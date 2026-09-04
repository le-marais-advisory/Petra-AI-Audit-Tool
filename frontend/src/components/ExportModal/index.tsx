import { useExportModalBehavior, type ExportModalProps } from "./behaviors";


export function ExportModal(props: ExportModalProps) {
  const { dialogRef, coverText, setCoverText, stage, errorMsg, handleExport } = useExportModalBehavior(props);

  return (
    <dialog
      ref={dialogRef}
      className="fixed top-1/2 left-1/2 m-0 w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-[1.5rem] border border-slate-200 bg-white p-0 shadow-xl backdrop:bg-slate-950/40"
    >
      <div className="p-6">
        <h2 className="text-lg font-semibold text-slate-950">Export Audit Report</h2>
        <p className="mt-1 text-sm text-slate-500">
          Generate a PDF containing all analysis results. Optionally add a cover page with notes explaining any non-passes.
        </p>

        <div className="mt-5">
          <label htmlFor="cover-text" className="block text-sm font-medium text-slate-700">
            Cover Page Notes <span className="font-normal text-slate-400">(optional)</span>
          </label>
          <textarea
            id="cover-text"
            rows={6}
            className="mt-2 w-full rounded-xl border border-slate-300 bg-slate-50 px-4 py-3 text-sm text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
            placeholder="e.g. FMT-HEADINGS fail is expected — this is a client style preference. The RND-GLOBAL needs_review was manually verified as correct."
            value={coverText}
            onChange={(e) => setCoverText(e.target.value)}
            disabled={stage === "exporting" || stage === "done"}
          />
        </div>

        {stage === "done" ? (
          <p className="mt-4 rounded-xl bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-700">
            Report downloaded successfully.
          </p>
        ) : null}

        {stage === "error" ? (
          <p className="mt-4 rounded-xl bg-rose-50 px-4 py-3 text-sm font-medium text-rose-700">{errorMsg}</p>
        ) : null}

        <div className="mt-6 flex items-center justify-end gap-3">
          <button
            type="button"
            className="rounded-full border border-slate-300 px-4 py-2 text-sm font-medium text-slate-600 transition hover:bg-slate-50"
            onClick={props.onClose}
            disabled={stage === "exporting"}
          >
            Cancel
          </button>
          <button
            type="button"
            className="rounded-full bg-slate-950 px-5 py-2 text-sm font-medium text-white transition hover:bg-slate-800 disabled:opacity-50"
            onClick={() => void handleExport()}
            disabled={stage === "exporting" || stage === "done" || !props.analysis}
          >
            {stage === "exporting" ? "Generating..." : "Export PDF"}
          </button>
        </div>
      </div>
    </dialog>
  );
}
