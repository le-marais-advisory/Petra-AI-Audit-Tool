import { apiGet } from "@/services/apiClient";
import type { DocumentTypeDefinition, DocumentTypesResponse } from "@/types/api";


export async function fetchDocumentTypes(): Promise<DocumentTypeDefinition[]> {
  const response = await apiGet<DocumentTypesResponse>("/document-types");
  return response.document_types || [];
}
