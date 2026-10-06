import { StatusPill } from "@/components/StatusPill";
import { cn } from "@/utils/cn";
import { acceptAttribute, booleanOptions } from "@/utils/documentTypes";

import { getEnumOptions, useUploadPanelBehavior, type UploadPanelProps } from "./behaviors";


export function UploadPanel(props: UploadPanelProps) {
  const {
    canRun,
    handleDragEnter,
    handleDragLeave,
    handleDragOver,
    handleDrop,
    handleInputChange,
    handlePriorChange,
    handleRun,
    inputRef,
    isDisabled,
    isDragActive,
    needsPrior,
    prior,
    priorError,
    priorFile,
    priorInputRef,
    stagedFile,
  } = useUploadPanelBehavior(props);
  const { documentType, documentTypes } = props;
  const formats = (documentType?.accepted_formats || ["pdf"]).map((format) => `.${format}`).join(" / ");
  const enumOptions = getEnumOptions(documentType);
  const checkboxes = booleanOptions(documentType);

  return (
    <article className="section-panel p-6">
      <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-slate-950">Validate a Document</h2>
          <p className="mt-1 text-sm text-slate-500">Choose the document type, then drop or select the file to audit.</p>
        </div>
        <div className="text-sm text-slate-500">Use the tabs below to compare source, extraction, and analysis.</div>
      </div>

      <div className="mt-5 flex flex-wrap items-end gap-4">
          <label className="flex flex-col gap-1 text-sm font-medium text-slate-700">
            Document type
            <select
              className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
              disabled={props.isBusy}
              value={props.documentTypeId}
              onChange={(event) => props.onDocumentTypeChange(event.target.value)}
            >
              <option value="" disabled>
                {documentTypes.length ? "Select a document type…" : "Loading document types…"}
              </option>
              {documentTypes.map((type) => (
                <option key={type.id} value={type.id}>
                  {type.label}
                </option>
              ))}
            </select>
          </label>

          {enumOptions.map((option) => (
            <label key={option.name} className="flex flex-col gap-1 text-sm font-medium text-slate-700">
              {option.title}
              {option.required ? <span className="sr-only">(required)</span> : null}
              <select
                className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
                disabled={props.isBusy}
                value={String(props.documentOptions[option.name] ?? "")}
                onChange={(event) => props.onDocumentOptionChange(option.name, event.target.value)}
              >
                <option value="">Select…</option>
                {option.choices.map((choice) => (
                  <option key={choice.value} value={choice.value}>
                    {choice.label}
                  </option>
                ))}
              </select>
            </label>
          ))}

          {checkboxes.map((option) => (
            <label key={option.name} className="flex items-center gap-2 self-center text-sm text-slate-700">
              <input
                type="checkbox"
                className="h-4 w-4 rounded border-slate-300"
                disabled={props.isBusy}
                checked={props.documentOptions[option.name] === true}
                onChange={(event) => props.onDocumentOptionChange(option.name, event.target.checked)}
              />
              {option.title}
            </label>
          ))}

          {documentType?.description ? (
            <p className="max-w-md text-xs text-slate-500">{documentType.description}</p>
          ) : null}
      </div>

      {props.uploadHint ? (
        // Until the run is fully specified there is nothing to upload into: say what to pick instead.
        <p className="mt-6 rounded-[1.25rem] border border-slate-200 bg-slate-50 px-5 py-4 text-sm text-slate-600">
          {props.uploadHint}
        </p>
      ) : (
        <label
          className={cn(
            "mt-6 flex cursor-pointer flex-col items-center justify-center rounded-[1.75rem] border-2 border-dashed px-6 py-12 text-center transition",
            isDisabled ? "pointer-events-none cursor-not-allowed opacity-60" : "",
            isDragActive
              ? "border-blue-600 bg-blue-50"
              : isDisabled
                ? "border-slate-200 bg-slate-50"
                : "border-slate-300 bg-slate-50 hover:border-blue-600 hover:bg-blue-50",
          )}
          onDragEnter={handleDragEnter}
          onDragLeave={handleDragLeave}
          onDragOver={handleDragOver}
          onDrop={handleDrop}
          aria-disabled={isDisabled}
        >
          <input
            ref={inputRef}
            accept={acceptAttribute(documentType)}
            className="hidden"
            disabled={isDisabled}
            type="file"
            onChange={handleInputChange}
          />

          <div className="rounded-full bg-white p-4 shadow-sm">
            <svg className="h-8 w-8 text-blue-700" fill="none" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 16V4m0 0l-4 4m4-4l4 4M5 16v1a3 3 0 003 3h8a3 3 0 003-3v-1" />
            </svg>
          </div>

          <h3 className="mt-4 text-base font-semibold text-slate-900">
            {stagedFile ? stagedFile.name : prior ? `Drop the current ${formats} workbook here` : `Drop a ${formats} file here`}
          </h3>
          <p className="mt-2 max-w-md text-sm text-slate-500">
            {stagedFile
              ? "Current workbook selected. Drop or choose another file to replace it."
              : "or click to select a file. The uploaded document is processed ephemerally and discarded after analysis."}
          </p>

          <span className="mt-5 rounded-full bg-slate-950 px-4 py-2 text-sm font-medium text-white">
            {stagedFile ? "Replace file" : "Choose file"}
          </span>
        </label>
      )}

      {prior && !props.uploadHint ? (
        <div className="mt-4 space-y-3">
          {needsPrior ? (
            <div className="rounded-[1.25rem] border border-slate-200 bg-white px-5 py-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-slate-900">{prior.label}</p>
                  <p className="mt-1 max-w-xl text-xs text-slate-500">{prior.description}</p>
                </div>
                <button
                  type="button"
                  className="rounded-full border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
                  disabled={props.isBusy}
                  onClick={() => priorInputRef.current?.click()}
                >
                  {priorFile ? "Replace" : "Choose prior workbook"}
                </button>
                <input
                  ref={priorInputRef}
                  accept={prior.accepted_formats.map((format) => `.${format}`).join(",")}
                  className="hidden"
                  type="file"
                  onChange={handlePriorChange}
                />
              </div>
              {priorFile ? <p className="mt-2 text-sm text-slate-700">{priorFile.name}</p> : null}
              {priorError ? <p className="mt-2 text-sm text-rose-700">{priorError}</p> : null}
            </div>
          ) : (
            <p className="rounded-[1.25rem] border border-slate-200 bg-slate-50 px-5 py-3 text-sm text-slate-600">
              First capital event: no prior workbook is needed, and the cross-event checks will be marked not applicable.
            </p>
          )}

          <div className="flex justify-end">
            <button
              type="button"
              className="rounded-full bg-slate-950 px-5 py-2.5 text-sm font-medium text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40"
              disabled={!canRun}
              onClick={() => {
                void handleRun();
              }}
            >
              Run validation
            </button>
          </div>
        </div>
      ) : null}

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
