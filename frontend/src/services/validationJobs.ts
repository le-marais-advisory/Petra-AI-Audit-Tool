import { apiGet, apiPost, apiPostForm } from "@/services/apiClient";
import type { DocumentOptions, RuleDefinition, ValidationJobResponse } from "@/types/api";


export async function createValidationJob(
  file: File,
  selectedRules: RuleDefinition[],
  documentType: string,
  options: DocumentOptions,
  priorFile?: File | null,
): Promise<ValidationJobResponse> {
  const formData = new FormData();
  formData.append("file", file);
  if (priorFile) {
    formData.append("prior_file", priorFile);
  }
  formData.append("document_type", documentType);
  formData.append("options_json", JSON.stringify(options));
  formData.append("rules_json", JSON.stringify({ rules: selectedRules }));
  return apiPostForm<ValidationJobResponse>("/validations/jobs", formData);
}

export async function getValidationJob(jobId: string): Promise<ValidationJobResponse> {
  return apiGet<ValidationJobResponse>(`/validations/jobs/${jobId}`);
}

export async function cancelValidationJob(jobId: string): Promise<ValidationJobResponse> {
  return apiPost<ValidationJobResponse>(`/validations/jobs/${jobId}/cancel`);
}
