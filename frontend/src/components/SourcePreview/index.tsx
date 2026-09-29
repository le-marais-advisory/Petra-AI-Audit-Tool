import { EmptyState } from "@/components/EmptyState";

import { describeRole, findMetric, type SourcePreviewProps } from "./behaviors";


function WorkbookInventory({ sourceFilename, pages = [], overview }: SourcePreviewProps) {
  if (!pages.length) {
    return (
      <EmptyState
        title={sourceFilename ? `Analyzing ${sourceFilename}` : "No workbook loaded"}
        description="Workbooks cannot be previewed in the browser. The processed sheets are listed here once analysis finishes."
      />
    );
  }
  const processed = findMetric(overview, "Sheets Processed");
  return (
    <div className="space-y-4">
      <div className="rounded-[1.25rem] border border-slate-200 bg-slate-50 px-4 py-3">
        <p className="text-sm font-semibold text-slate-900">{sourceFilename || "Workbook"}</p>
        <p className="text-xs text-slate-500">{processed?.detail || "Sheets processed for the selected event type."}</p>
      </div>
      <table className="min-w-full overflow-hidden rounded-xl border border-slate-200 text-sm">
        <thead className="bg-slate-100 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-4 py-2">#</th>
            <th className="px-4 py-2">Sheet</th>
            <th className="px-4 py-2">Role</th>
            <th className="px-4 py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          {pages.map((page) => (
            <tr key={page.page} className="border-t border-slate-200 bg-white">
              <td className="px-4 py-2 text-slate-500">{page.page}</td>
              <td className="px-4 py-2 font-medium text-slate-900">{page.label || `Sheet ${page.page}`}</td>
              <td className="px-4 py-2 text-slate-600">{describeRole(page.page_type?.[0])}</td>
              <td className="px-4 py-2 text-emerald-700">Processed</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}


export function SourcePreview(props: SourcePreviewProps) {
  const { previewUrl, sourceFilename, isWorkbook } = props;
  if (isWorkbook) {
    return <WorkbookInventory {...props} />;
  }
  if (!previewUrl) {
    return <EmptyState title="No document loaded" description="Upload a document to preview the original here." />;
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-[1.25rem] border border-slate-200 bg-slate-50 px-4 py-3">
        <div>
          <p className="text-sm font-semibold text-slate-900">{sourceFilename || "Source document"}</p>
          <p className="text-xs text-slate-500">Local browser preview</p>
        </div>

        <a
          className="rounded-full bg-slate-950 px-4 py-2 text-sm font-semibold text-white"
          href={previewUrl}
          rel="noreferrer"
          target="_blank"
        >
          Open document
        </a>
      </div>

      <iframe className="h-[900px] w-full rounded-[1.5rem] border border-slate-200 bg-white" src={previewUrl} title="Source document preview" />
    </div>
  );
}
