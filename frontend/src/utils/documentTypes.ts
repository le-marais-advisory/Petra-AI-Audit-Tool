import type { DocumentTypeDefinition, DocumentOptions } from "@/types/api";


/**
 * Used only when the backend has no /document-types endpoint (older deployments). The user
 * still has to pick it explicitly: nothing is preselected.
 */
export const FALLBACK_DOCUMENT_TYPES: DocumentTypeDefinition[] = [
  {
    id: "financial_statements",
    label: "Financial Statements (PDF)",
    description: "Fund financial statements.",
    accepted_formats: ["pdf"],
    options_schema: { type: "object", properties: {}, required: [] },
  },
];

/**
 * What still has to be chosen before a file can be uploaded, as a short instruction, or
 * null when the run is fully specified.
 */
export function uploadBlocker(type: DocumentTypeDefinition | null, options: DocumentOptions): string | null {
  if (!type) {
    return "Select a document type above to enable uploads.";
  }
  const missing = missingRequiredOptions(type, options);
  if (missing.length) {
    return `Select the ${missing.map((name) => optionLabel(type, name).toLowerCase()).join(", ")} above to enable uploads.`;
  }
  return null;
}

/** The value for the file input's `accept` attribute, e.g. ".xlsx,.xlsm". */
export function acceptAttribute(type: DocumentTypeDefinition | null): string {
  return (type?.accepted_formats || ["pdf"]).map((format) => `.${format}`).join(",");
}

export function fileMatchesFormats(file: File, formats: string[]): boolean {
  const name = file.name.toLowerCase();
  return formats.some((format) => name.endsWith(`.${format}`));
}

export function fileMatchesType(file: File, type: DocumentTypeDefinition | null): boolean {
  return fileMatchesFormats(file, type?.accepted_formats || ["pdf"]);
}

/** Whether this run needs the prior document (the type takes one and it is not waived). */
export function priorDocumentNeeded(type: DocumentTypeDefinition | null, options: DocumentOptions): boolean {
  const prior = type?.prior_document;
  return Boolean(prior && !options[prior.waived_by_option]);
}

/** Boolean options of the type, rendered as checkboxes (e.g. "first capital event"). */
export function booleanOptions(type: DocumentTypeDefinition | null) {
  if (!type) {
    return [];
  }
  return Object.entries(type.options_schema.properties)
    .filter(([, property]) => property.type === "boolean")
    .map(([name, property]) => ({ name, title: property.title || name.replace(/_/g, " ") }));
}

export function isWorkbookType(type: DocumentTypeDefinition | null): boolean {
  return Boolean(type?.accepted_formats.some((format) => format.startsWith("xls")));
}

/** Names of required options that have no value yet. */
export function missingRequiredOptions(type: DocumentTypeDefinition | null, options: DocumentOptions): string[] {
  const required = type?.options_schema.required || [];
  return required.filter((name) => !options[name]);
}

export function optionLabel(type: DocumentTypeDefinition | null, name: string): string {
  return type?.options_schema.properties[name]?.title || name.replace(/_/g, " ");
}

/** Strip any file extension (".pdf", ".xlsx", ...) from a source filename. */
export function stripExtension(filename: string): string {
  return filename.replace(/\.[A-Za-z0-9]{2,5}$/, "");
}
