import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, DragEvent } from "react";

import type { DocumentOptions, DocumentTypeDefinition, WorkspaceStatus } from "@/types/api";
import { fileMatchesFormats, priorDocumentNeeded } from "@/utils/documentTypes";


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
  onDocumentOptionChange: (name: string, value: string | boolean) => void;
  /** Starts the run. Types that take a prior document pass it as the second file. */
  onFileSelected: (file: File, priorFile?: File | null) => Promise<void>;
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
  documentType,
  documentTypeId,
  documentOptions,
  onFileSelected,
}: Pick<
  UploadPanelProps,
  "isBusy" | "uploadHint" | "documentType" | "documentTypeId" | "documentOptions" | "onFileSelected"
>) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const priorInputRef = useRef<HTMLInputElement | null>(null);
  const [isDragActive, setIsDragActive] = useState(false);
  // Types with a prior document stage both files and start on "Run validation";
  // single-file types start as soon as the file is dropped.
  const [stagedFile, setStagedFile] = useState<File | null>(null);
  const [priorFile, setPriorFile] = useState<File | null>(null);
  const [priorError, setPriorError] = useState<string | null>(null);
  const isDisabled = isBusy || Boolean(uploadHint);
  const prior = documentType?.prior_document || null;
  const needsPrior = priorDocumentNeeded(documentType, documentOptions);
  const canRun = Boolean(stagedFile) && (!needsPrior || Boolean(priorFile)) && !isDisabled;

  useEffect(() => {
    setStagedFile(null);
    setPriorFile(null);
    setPriorError(null);
  }, [documentTypeId]);

  const accept = async (file: File) => {
    if (prior) {
      setStagedFile(file);
      return;
    }
    await onFileSelected(file);
  };

  const handleInputChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) {
      await accept(file);
    }
    event.target.value = "";
  };

  const handlePriorChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !prior) {
      return;
    }
    if (!fileMatchesFormats(file, prior.accepted_formats)) {
      setPriorError(`${file.name} is not a ${prior.accepted_formats.map((f) => `.${f}`).join(" / ")} file.`);
      return;
    }
    setPriorError(null);
    setPriorFile(file);
  };

  const handleRun = async () => {
    if (stagedFile && canRun) {
      await onFileSelected(stagedFile, needsPrior ? priorFile : null);
    }
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
      await accept(file);
    }
  };

  return {
    canRun,
    inputRef,
    isDisabled,
    isDragActive,
    needsPrior,
    prior,
    priorError,
    priorFile,
    priorInputRef,
    stagedFile,
    handleDragEnter,
    handleDragLeave,
    handleDragOver,
    handleDrop,
    handleInputChange,
    handlePriorChange,
    handleRun,
  };
}
