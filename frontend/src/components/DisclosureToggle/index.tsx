import { getDisclosureButtonClasses, getDisclosureChevronClasses, type DisclosureToggleProps } from "./behaviors";


export function DisclosureToggle({ controls, expanded, label, onToggle, tone = "inline", trailing }: DisclosureToggleProps) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={expanded}
      aria-controls={controls}
      className={getDisclosureButtonClasses(tone)}
    >
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 16 16"
        fill="currentColor"
        aria-hidden="true"
        className={getDisclosureChevronClasses(tone, expanded)}
      >
        <path d="M6.22 3.22a.75.75 0 0 1 1.06 0l4.25 4.25a.75.75 0 0 1 0 1.06l-4.25 4.25a.75.75 0 0 1-1.06-1.06L9.94 8 6.22 4.28a.75.75 0 0 1 0-1.06Z" />
      </svg>
      <span>{label}</span>
      {trailing ? <span className="ml-auto">{trailing}</span> : null}
    </button>
  );
}
