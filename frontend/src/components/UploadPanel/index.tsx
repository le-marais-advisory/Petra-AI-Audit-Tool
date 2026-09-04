import { StatusPill } from "@/components/StatusPill";
import { cn } from "@/utils/cn";

import { useUploadPanelBehavior, type UploadPanelProps } from "./behaviors";


export function UploadPanel(props: UploadPanelProps) {
  const {
    handleDragEnter,
    handleDragLeave,
    handleDragOver,
    handleDrop,
    handleInputChange,
    inputRef,
    isDragActive,
  } = useUploadPanelBehavior(props);

  return (
    <article className="section-panel p-6">
      <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-slate-950">Extract PDF Content</h2>
          <p className="mt-1 text-sm text-slate-500">Drop a PDF or choose a file to inspect extracted text page by page.</p>
        </div>
        <div className="text-sm text-slate-500">Use the tabs below to compare source, extraction, and analysis.</div>
      </div>

      <label
        className={cn(
          "mt-6 flex cursor-pointer flex-col items-center justify-center rounded-[1.75rem] border-2 border-dashed px-6 py-12 text-center transition",
          props.isBusy ? "pointer-events-none opacity-70" : "",
          isDragActive ? "border-blue-600 bg-blue-50" : "border-slate-300 bg-slate-50 hover:border-blue-600 hover:bg-blue-50",
        )}
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
      >
        <input ref={inputRef} accept=".pdf" className="hidden" type="file" onChange={handleInputChange} />

        <div className="rounded-full bg-white p-4 shadow-sm">
          <svg className="h-8 w-8 text-blue-700" fill="none" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 16V4m0 0l-4 4m4-4l4 4M5 16v1a3 3 0 003 3h8a3 3 0 003-3v-1" />
          </svg>
        </div>

        <h3 className="mt-4 text-base font-semibold text-slate-900">Drop PDF here</h3>
        <p className="mt-2 max-w-md text-sm text-slate-500">
          or click to select a file. The uploaded PDF is processed ephemerally and discarded after analysis.
        </p>

        <span className="mt-5 rounded-full bg-slate-950 px-4 py-2 text-sm font-medium text-white">Choose PDF</span>
      </label>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <StatusPill status={props.status} />

        {props.isBusy ? (
          <button
            className="rounded-full border border-rose-300 px-3 py-1.5 text-sm font-medium text-rose-700 transition hover:bg-rose-50"
            type="button"
            onClick={() => {
              void props.onStopAnalysis();
            }}
          >
            Stop Analysis
          </button>
        ) : null}

        {props.documentId ? <span className="text-sm text-slate-500">Document ID: {props.documentId}</span> : null}
      </div>
    </article>
  );
}
