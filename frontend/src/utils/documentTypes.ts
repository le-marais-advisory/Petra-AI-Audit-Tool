import type { DocumentTypeDefinition, DocumentOptions } from "@/types/api";


export const DEFAULT_DOCUMENT_TYPE = "financial_statements";

/** The value for the file input's `accept` attribute, e.g. ".xlsx,.xlsm". */
export function acceptAttribute(type: DocumentTypeDefinition | null): string {
  return (type?.accepted_formats || ["pdf"]).map((format) => `.${format}`).join(",");
}

export function fileMatchesType(file: File, type: DocumentTypeDefinition | null): boolean {
  const name = file.name.toLowerCase();
  return (type?.accepted_formats || ["pdf"]).some((format) => name.endsWith(`.${format}`));
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
