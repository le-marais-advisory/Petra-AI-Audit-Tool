import { useRef, useState } from "react";
import type { ChangeEvent, DragEvent } from "react";

import type { DocumentOptions, DocumentTypeDefinition, WorkspaceStatus } from "@/types/api";


export interface UploadPanelProps {
  documentId: string | null;
  isBusy: boolean;
  status: WorkspaceStatus;
  documentTypes: DocumentTypeDefinition[];
  documentType: DocumentTypeDefinition | null;
  documentTypeId: string;
  documentOptions: DocumentOptions;
  /** What must still be chosen before uploading (document type, event type), or null when ready. */
  uploadHint: string | null;
  onDocumentTypeChange: (documentTypeId: string) => void;
  onDocumentOptionChange: (name: string, value: string) => void;
  onFileSelected: (file: File) => Promise<void>;
  onStopAnalysis: () => Promise<void>;
}

/** Enum options of the selected document type, rendered as dropdowns (e.g. event type). */
export function getEnumOptions(documentType: DocumentTypeDefinition | null) {
  if (!documentType) {
    return [];
  }
  const required = new Set(documentType.options_schema.required || []);
  return Object.entries(documentType.options_schema.properties)
    .filter(([, property]) => Array.isArray(property.enum) && property.enum.length > 0)
    .map(([name, property]) => ({
      name,
      title: property.title || name.replace(/_/g, " "),
      required: required.has(name),
      choices: (property.enum || []).map((value, index) => ({
        value,
        label: property.enumLabels?.[index] || value.replace(/_/g, " "),
      })),
    }));
}

export function useUploadPanelBehavior({
  isBusy,
  uploadHint,
  onFileSelected,
}: Pick<UploadPanelProps, "isBusy" | "uploadHint" | "onFileSelected">) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [isDragActive, setIsDragActive] = useState(false);
  const isDisabled = isBusy || Boolean(uploadHint);

  const handleInputChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) {
      await onFileSelected(file);
    }
    event.target.value = "";
  };

  const handleDragEnter = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    if (!isDisabled) {
      setIsDragActive(true);
    }
  };

  const handleDragLeave = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    setIsDragActive(false);
  };

  const handleDragOver = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    if (!isDisabled) {
      setIsDragActive(true);
    }
  };

  const handleDrop = async (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    setIsDragActive(false);
    if (isDisabled) {
      return;
    }
    const file = event.dataTransfer.files?.[0];
    if (file) {
      await onFileSelected(file);
    }
  };

  return {
    inputRef,
    isDisabled,
    isDragActive,
    handleDragEnter,
    handleDragLeave,
    handleDragOver,
    handleDrop,
    handleInputChange,
  };
}
