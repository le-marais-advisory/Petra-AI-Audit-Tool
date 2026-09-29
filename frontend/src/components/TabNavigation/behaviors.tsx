import type { WorkspaceTabKey } from "@/types/api";


export interface TabDefinition {
  key: WorkspaceTabKey;
  label: string;
}

export interface TabNavigationProps {
  activeTab: WorkspaceTabKey;
  onTabChange: (tab: WorkspaceTabKey) => void;
  tabs?: TabDefinition[];
}

export const tabDefinitions: TabDefinition[] = [
  { key: "source", label: "Original PDF" },
  { key: "extracted", label: "Extracted Text" },
  { key: "text-analysis", label: "Text Analysis Result" },
  { key: "visual-analysis", label: "Visual Analysis Result" },
];

/** Workbooks have no rendered pages, so there is no visual tab and the labels speak of sheets. */
export const workbookTabDefinitions: TabDefinition[] = [
  { key: "source", label: "Workbook Sheets" },
  { key: "extracted", label: "Sheet Content" },
  { key: "text-analysis", label: "Rule Results" },
];
