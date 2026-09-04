import { getCardClasses, getRuleTypeClasses, type RuleSelectionCardProps } from "./behaviors";


export function RuleSelectionCard({ checked, bypassed, rule, onToggle, onBypassToggle }: RuleSelectionCardProps) {
  const checkboxId = `rule-${rule.id}`;
  const bypassId = `rule-${rule.id}-bypass`;

  return (
    <div className={getCardClasses(checked)}>
      <div className="flex items-start gap-3">
        <div className="mt-0.5">
          <label htmlFor={checkboxId} className="block cursor-pointer">
            <input
              id={checkboxId}
              checked={checked}
              className="peer sr-only"
              type="checkbox"
              onChange={() => onToggle(rule.id)}
            />
            <span className="flex h-5 w-5 items-center justify-center rounded-md border-2 border-slate-300 bg-white text-white transition peer-checked:border-blue-600 peer-checked:bg-blue-600 peer-focus-visible:ring-2 peer-focus-visible:ring-blue-500/30">
              <svg
                aria-hidden="true"
                className="h-3.5 w-3.5 opacity-0 transition peer-checked:opacity-100"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                viewBox="0 0 24 24"
              >
                <path d="M5 13l4 4L19 7" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </span>
          </label>
        </div>

        <label htmlFor={checkboxId} className="min-w-0 flex-1 cursor-pointer">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-slate-900">{rule.name || rule.id}</h3>
            <span className="rounded-full bg-white px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              {rule.severity || "rule"}
            </span>
            <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide ${getRuleTypeClasses(rule.analysis_type)}`}>
              {rule.analysis_type}
            </span>
            {rule.bypassable ? (
              <span className="rounded-full bg-amber-100 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-amber-700">
                bypassable
              </span>
            ) : null}
          </div>

          <p className="mt-2 text-xs uppercase tracking-[0.2em] text-slate-400">{rule.id}</p>
          <p className="mt-3 text-sm leading-6 text-slate-600">{rule.description || "No description available."}</p>
        </label>
      </div>

      {rule.bypassable && checked ? (
        <label
          htmlFor={bypassId}
          className="ml-8 mt-3 flex cursor-pointer items-center gap-2 rounded-lg bg-amber-50 px-3 py-2"
        >
          <input
            id={bypassId}
            checked={bypassed}
            className="h-3.5 w-3.5 rounded border-amber-400 text-amber-600 focus:ring-amber-500"
            type="checkbox"
            onChange={() => onBypassToggle(rule.id)}
          />
          <span className="text-xs font-medium text-amber-800">Allow bypass for this run</span>
        </label>
      ) : null}
    </div>
  );
}
