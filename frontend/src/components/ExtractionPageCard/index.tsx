import { getTableLabel, type ExtractionPageCardProps } from "./behaviors";


export function ExtractionPageCard({ page }: ExtractionPageCardProps) {
  const tables = page.tables || [];

  return (
    <section className="rounded-[1.75rem] border border-slate-200 bg-white p-6 shadow-panel">
      <div className="border-b border-slate-200 pb-5">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
          {page.label ? `Sheet ${page.page}: ${page.label}` : `Page ${page.page}`}
        </p>
        <h3 className="mt-2 text-xl font-semibold text-slate-950">{page.char_count} extracted characters</h3>
        {page.label ? null : <p className="mt-2 text-sm text-slate-500">{getTableLabel(tables.length)}</p>}
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <article className="rounded-[1.25rem] border border-slate-200 bg-slate-50 p-4">
          <h4 className="text-sm font-semibold text-slate-900">{page.label ? "Sheet skeleton (labels and formula patterns)" : "Extracted Text"}</h4>
          <pre className="pretty-scrollbar mt-3 max-h-[32rem] overflow-auto whitespace-pre-wrap break-words rounded-xl bg-white p-4 text-sm leading-6 text-slate-700">
            {page.text || "No text extracted."}
          </pre>
        </article>

        <div className="space-y-4">
          <h4 className="text-sm font-semibold text-slate-900">Extracted Tables</h4>

          {tables.length ? (
            tables.map((table) => (
              <article key={`${page.page}-${table.index}`} className="rounded-[1.25rem] border border-slate-200 bg-slate-50 p-4">
                <div className="mb-3 flex items-center justify-between">
                  <h5 className="text-sm font-semibold text-slate-900">Table {table.index}</h5>
                  <span className="rounded-full bg-white px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                    {table.rows.length} rows
                  </span>
                </div>
                <div className="pretty-scrollbar overflow-x-auto">
                  <table className="min-w-full border-separate border-spacing-0 overflow-hidden rounded-xl">
                    <tbody>
                      {table.rows.map((row, rowIndex) => (
                        <tr key={`${page.page}-${table.index}-${rowIndex}`}>
                          {row.map((cell, cellIndex) => (
                            <td key={`${page.page}-${table.index}-${rowIndex}-${cellIndex}`} className="border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700">
                              {cell}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </article>
            ))
          ) : (
            <p className="rounded-[1.25rem] border border-slate-200 bg-slate-50 px-4 py-4 text-sm text-slate-500">
              No tables detected on this page.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
