import { stripExtension } from "@/utils/documentTypes";
import { useCallback, useEffect, useRef, useState } from "react";

import { apiPostBlob } from "@/services/apiClient";
import type { DocumentAnalysis } from "@/types/api";


export interface ExportModalProps {
  isOpen: boolean;
  onClose: () => void;
  documentId: string | null;
  sourceFilename: string | null;
  pageCount: number;
  documentType?: string;
  options?: Record<string, string>;
  analysis: DocumentAnalysis | null;
}

type ExportStage = "idle" | "exporting" | "done" | "error";

export function useExportModalBehavior({
  isOpen,
  onClose,
  documentId,
  sourceFilename,
  pageCount,
  analysis,
  documentType,
  options,
}: ExportModalProps) {
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const [coverText, setCoverText] = useState("");
  const [stage, setStage] = useState<ExportStage>("idle");
  const [errorMsg, setErrorMsg] = useState("");

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    if (isOpen && !dialog.open) {
      dialog.showModal();
    } else if (!isOpen && dialog.open) {
      dialog.close();
    }
  }, [isOpen]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    const handleClose = () => {
      onClose();
    };
    dialog.addEventListener("close", handleClose);
    return () => dialog.removeEventListener("close", handleClose);
  }, [onClose]);

  // Reset state when modal opens
  useEffect(() => {
    if (isOpen) {
      setStage("idle");
      setErrorMsg("");
    }
  }, [isOpen]);

  const handleExport = useCallback(async () => {
    if (!analysis || !documentId) return;

    setStage("exporting");
    setErrorMsg("");

    try {
      const body = {
        document_id: documentId,
        source_filename: sourceFilename,
        page_count: pageCount,
        document_type: documentType || "financial_statements",
        options: options || {},
        cover_sheet_text: coverText,
        analysis,
      };

      const blob = await apiPostBlob("/export/pdf", body);
      const url = URL.createObjectURL(blob);

      // Trigger download
      const a = document.createElement("a");
      a.href = url;
      const safeName = stripExtension(sourceFilename || "report");
      a.download = `${safeName}-audit-report.pdf`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      setStage("done");
      setTimeout(() => {
        onClose();
      }, 1200);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Export failed.");
      setStage("error");
    }
  }, [analysis, documentId, sourceFilename, pageCount, documentType, options, coverText, onClose]);

  return {
    dialogRef,
    coverText,
    setCoverText,
    stage,
    errorMsg,
    handleExport,
  };
}
