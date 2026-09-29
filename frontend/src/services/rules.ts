import { apiGet } from "@/services/apiClient";
import type { RuleDefinition, RulesResponse } from "@/types/api";


export async function fetchRules(documentType?: string, eventType?: string): Promise<RuleDefinition[]> {
  const params = new URLSearchParams();
  if (documentType) {
    params.set("document_type", documentType);
  }
  if (eventType) {
    params.set("event_type", eventType);
  }
  const query = params.toString();
  const response = await apiGet<RulesResponse>(query ? `/rules?${query}` : "/rules");
  return response.rules || [];
}
