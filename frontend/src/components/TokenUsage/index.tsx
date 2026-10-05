import { EmptyState } from "@/components/EmptyState";
import type { LlmUsageRow } from "@/types/api";

import { cachedShare, formatTokens, visibleColumns, type TokenUsageProps, type UsageColumn } from "./behaviors";


function SummaryTile({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="rounded-[1.25rem] border border-slate-200 bg-slate-50 px-4 py-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-xl font-semibold text-slate-900">{value}</p>
      {detail ? <p className="text-xs text-slate-500">{detail}</p> : null}
    </div>
  );
}

function UsageTable({
  title,
  rows,
  columns,
  firstColumn,
  describe,
}: {
  title: string;
  rows: LlmUsageRow[];
  columns: UsageColumn[];
  firstColumn: string;
  describe?: (row: LlmUsageRow) => string | undefined;
}) {
  if (!rows.length) {
    return null;
  }
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
      <div className="overflow-x-auto">
        <table className="min-w-full overflow-hidden rounded-xl border border-slate-200 text-sm">
          <thead className="bg-slate-100 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-2">{firstColumn}</th>
              {columns.map((column) => (
                <th key={column.key} className="px-4 py-2 text-right">
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const description = describe?.(row);
              return (
                <tr key={row.label} className="border-t border-slate-200 bg-white">
                  <td className="px-4 py-2">
                    <span className="font-medium text-slate-900">{row.label}</span>
                    {description ? <span className="block text-xs text-slate-500">{description}</span> : null}
                  </td>
                  {columns.map((column) => (
                    <td key={column.key} className="px-4 py-2 text-right tabular-nums text-slate-700">
                      {formatTokens(row[column.key])}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function TokenUsage({ usage, isBusy, ruleNames }: TokenUsageProps) {
  if (!usage) {
    return (
      <EmptyState
        title={isBusy ? "Audit in progress" : "No token usage yet"}
        description="LLM token consumption is reported here once the document audit finishes."
      />
    );
  }

  const { totals } = usage;
  if (!totals.calls) {
    return (
      <EmptyState
        title="No LLM calls"
        description="This run was evaluated without calling an LLM, so no tokens were consumed."
      />
    );
  }

  const columns = visibleColumns(totals);
  const uncached = totals.input_tokens - totals.cache_read_tokens - totals.cache_creation_tokens;

  return (
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <SummaryTile label="LLM calls" value={formatTokens(totals.calls)} />
        <SummaryTile
          label="Input tokens"
          value={formatTokens(totals.input_tokens)}
          detail={`${formatTokens(uncached)} uncached`}
        />
        <SummaryTile
          label="Cached input tokens"
          value={formatTokens(totals.cache_read_tokens)}
          detail={
            totals.cache_creation_tokens
              ? `${cachedShare(totals)} of input; ${formatTokens(totals.cache_creation_tokens)} written to cache`
              : `${cachedShare(totals)} of input`
          }
        />
        <SummaryTile
          label="Output tokens"
          value={formatTokens(totals.output_tokens)}
          detail={totals.reasoning_tokens ? `${formatTokens(totals.reasoning_tokens)} reasoning` : undefined}
        />
      </div>

      <p className="text-xs leading-5 text-slate-500">
        Input counts every prompt token sent, cached or not. Cached input is the part served from the provider&apos;s
        prompt cache and billed at a reduced rate. Output includes any reasoning tokens.
      </p>

      <UsageTable title="By stage" firstColumn="Stage" rows={usage.by_stage} columns={columns} />
      <UsageTable title="By model" firstColumn="Provider / model" rows={usage.by_model} columns={columns} />
      <UsageTable
        title="By rule"
        firstColumn="Rule"
        rows={usage.by_rule}
        columns={columns}
        describe={(row) => ruleNames[row.label]}
      />
    </div>
  );
}
