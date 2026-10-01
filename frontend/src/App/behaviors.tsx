import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { readEnv } from "@/config/runtime";
import { fetchDocumentTypes } from "@/services/documentTypes";
import { fetchRules } from "@/services/rules";
import { cancelValidationJob, createValidationJob, getValidationJob } from "@/services/validationJobs";
import {
  FALLBACK_DOCUMENT_TYPES,
  fileMatchesFormats,
  fileMatchesType,
  isWorkbookType,
  priorDocumentNeeded,
  uploadBlocker,
} from "@/utils/documentTypes";
import type {
  DocumentOptions,
  DocumentTypeDefinition,
  DocumentValidationResponse,
  RuleDefinition,
  RunOutcome,
  ValidationJobResponse,
  WorkspaceStatus,
  WorkspaceTabKey,
} from "@/types/api";


const DEFAULT_STATUS: WorkspaceStatus = {
  label: "Select a document type to begin",
  tone: "neutral",
  isLoading: false,
};

const READY_STATUS: WorkspaceStatus = {
  label: "No document uploaded",
  tone: "neutral",
  isLoading: false,
};

const POLL_INTERVAL_MS = 900;
const APP_NAME = readEnv("VITE_APP_NAME") || "Petra Vision";

function revokePreviewUrl(url: string | null): void {
  if (url) {
    URL.revokeObjectURL(url);
  }
}

function buildProgressSuffix(job: ValidationJobResponse): string {
  if (!job.progress_total) {
    return "";
  }
  return ` (${job.progress_current}/${job.progress_total})`;
}

function createWorkingStatus(label: string): WorkspaceStatus {
  return {
    label,
    tone: "working",
    isLoading: true,
  };
}

export function useAppBehavior() {
  const pollTimerRef = useRef<number | null>(null);
  const previewUrlRef = useRef<string | null>(null);

  const [availableRules, setAvailableRules] = useState<RuleDefinition[]>([]);
  const [selectedRuleIds, setSelectedRuleIds] = useState<string[]>([]);
  const [bypassedRuleIds, setBypassedRuleIds] = useState<string[]>([]);
  const [status, setStatus] = useState<WorkspaceStatus>(DEFAULT_STATUS);
  const [activeTab, setActiveTab] = useState<WorkspaceTabKey>("source");
  const [isBusy, setIsBusy] = useState(false);
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [sourcePreviewUrl, setSourcePreviewUrl] = useState<string | null>(null);
  const [sourceFilename, setSourceFilename] = useState<string | null>(null);
  const [result, setResult] = useState<DocumentValidationResponse | null>(null);
  const [rulesError, setRulesError] = useState<string | null>(null);
  const [runOutcome, setRunOutcome] = useState<RunOutcome>(null);
  const [documentTypes, setDocumentTypes] = useState<DocumentTypeDefinition[]>([]);
  // Nothing is preselected: the user must choose the document type before rules load or uploads open.
  const [documentTypeId, setDocumentTypeId] = useState<string>("");
  const [documentOptions, setDocumentOptions] = useState<DocumentOptions>({});

  const documentType = useMemo(
    () => documentTypes.find((type) => type.id === documentTypeId) || null,
    [documentTypes, documentTypeId],
  );
  const eventType = typeof documentOptions.event_type === "string" ? documentOptions.event_type : undefined;
  const uploadHint = uploadBlocker(documentType, documentOptions);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      window.clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const replacePreviewUrl = useCallback((nextUrl: string | null) => {
    revokePreviewUrl(previewUrlRef.current);
    previewUrlRef.current = nextUrl;
    setSourcePreviewUrl(nextUrl);
  }, []);

  const loadRules = useCallback(async () => {
    if (!documentTypeId) {
      setAvailableRules([]);
      setSelectedRuleIds([]);
      setRulesError(null);
      return;
    }
    try {
      const rules = await fetchRules(documentTypeId, eventType);
      setAvailableRules(rules);
      setSelectedRuleIds((currentIds) => {
        if (!currentIds.length) {
          return rules.map((rule) => rule.id);
        }
        const nextIds = currentIds.filter((id) => rules.some((rule) => rule.id === id));
        return nextIds.length ? nextIds : rules.map((rule) => rule.id);
      });
      setRulesError(null);
    } catch (error) {
      setRulesError(error instanceof Error ? error.message : "Failed to load rules.");
    }
  }, [documentTypeId, eventType]);

  const loadDocumentTypes = useCallback(async () => {
    try {
      const types = await fetchDocumentTypes();
      setDocumentTypes(types.length ? types : FALLBACK_DOCUMENT_TYPES);
    } catch {
      // Older backends have no /document-types endpoint: offer PDFs only (still not preselected).
      setDocumentTypes(FALLBACK_DOCUMENT_TYPES);
    }
  }, []);

  const handleDocumentTypeChange = useCallback((nextId: string) => {
    setDocumentTypeId(nextId);
    setDocumentOptions({});
    setSelectedRuleIds([]);
    setStatus(nextId ? READY_STATUS : DEFAULT_STATUS);
  }, []);

  const handleDocumentOptionChange = useCallback((name: string, value: string | boolean) => {
    setDocumentOptions((current) => {
      const next = { ...current };
      if (value === "" || value === false) {
        delete next[name];  // unset options are left out of options_json
      } else {
        next[name] = value;
      }
      return next;
    });
  }, []);

  const selectedRules = useMemo(
    () =>
      availableRules
        .filter((rule) => selectedRuleIds.includes(rule.id))
        .map((rule) =>
          rule.bypassable && bypassedRuleIds.includes(rule.id) ? { ...rule, bypass: true } : rule,
        ),
    [availableRules, selectedRuleIds, bypassedRuleIds],
  );

  const syncJobSnapshot = useCallback((job: ValidationJobResponse) => {
    if (job.result) {
      setResult(job.result);
    }
  }, []);

  const pollJob = useCallback(
    async (jobId: string) => {
      try {
        const job = await getValidationJob(jobId);
        syncJobSnapshot(job);

        if (job.status === "queued" || job.status === "running") {
          setStatus(createWorkingStatus(`${job.message || "Analyzing document"}${buildProgressSuffix(job)}`));
          setIsBusy(true);
          pollTimerRef.current = window.setTimeout(() => {
            void pollJob(jobId);
          }, POLL_INTERVAL_MS);
          return;
        }

        stopPolling();
        setIsBusy(false);

        if (job.status === "completed") {
          setRunOutcome("completed");
          setStatus({
            label: "Analysis complete",
            tone: "success",
            isLoading: false,
          });
          setActiveTab("text-analysis");
          return;
        }

        if (job.status === "cancelled") {
          setRunOutcome("cancelled");
          setStatus({
            label: "Analysis stopped",
            tone: "error",
            isLoading: false,
          });
          return;
        }

        setRunOutcome("failed");
        setStatus({
          label: job.error || job.message || "Analysis failed",
          tone: "error",
          isLoading: false,
        });
      } catch (error) {
        stopPolling();
        setIsBusy(false);
        setRunOutcome("failed");
        setStatus({
          label: error instanceof Error ? error.message : "Failed to fetch validation job.",
          tone: "error",
          isLoading: false,
        });
      }
    },
    [stopPolling, syncJobSnapshot],
  );

  const beginUpload = useCallback(
    async (file: File, priorFile?: File | null) => {
      const blocker = uploadBlocker(documentType, documentOptions);
      if (blocker || !documentType) {
        setStatus({ label: blocker || "Select a document type first.", tone: "error", isLoading: false });
        return;
      }
      const prior = documentType.prior_document;
      if (prior && priorDocumentNeeded(documentType, documentOptions) && !priorFile) {
        setStatus({
          label: `Add the ${prior.label.toLowerCase()} or mark this as the fund's first capital event.`,
          tone: "error",
          isLoading: false,
        });
        return;
      }
      if (prior && priorFile && !fileMatchesFormats(priorFile, prior.accepted_formats)) {
        setStatus({
          label: `${priorFile.name} is not a valid ${prior.label.toLowerCase()} (expected ${prior.accepted_formats.map((f) => `.${f}`).join(", ")}).`,
          tone: "error",
          isLoading: false,
        });
        return;
      }
      if (!fileMatchesType(file, documentType)) {
        const accepted = documentType.accepted_formats.map((format) => `.${format}`).join(", ");
        setStatus({
          label: `${file.name} is not a ${documentType.label} file (expected ${accepted}).`,
          tone: "error",
          isLoading: false,
        });
        return;
      }

      stopPolling();
      setCurrentJobId(null);
      setResult(null);
      setRunOutcome(null);
      // Browsers can preview PDFs inline; workbooks are summarised from the analysis result instead.
      replacePreviewUrl(isWorkbookType(documentType) ? null : URL.createObjectURL(file));
      setSourceFilename(file.name);
      setActiveTab("source");

      if (!selectedRules.length) {
        setIsBusy(false);
        setRunOutcome("failed");
        setStatus({
          label: rulesError
            ? `Validation rules could not be loaded: ${rulesError}`
            : "No validation rules selected. Select at least one rule before running an analysis.",
          tone: "error",
          isLoading: false,
        });
        return;
      }

      setStatus(createWorkingStatus(`Uploading ${file.name}`));
      setIsBusy(true);

      try {
        const job = await createValidationJob(
          file,
          selectedRules,
          documentTypeId,
          documentOptions,
          priorDocumentNeeded(documentType, documentOptions) ? priorFile : null,
        );
        setCurrentJobId(job.job_id);
        setStatus(createWorkingStatus(job.message || `Analyzing ${file.name}`));
        await pollJob(job.job_id);
      } catch (error) {
        setIsBusy(false);
        setResult(null);
        setRunOutcome("failed");
        setStatus({
          label: error instanceof Error ? error.message : "Validation failed.",
          tone: "error",
          isLoading: false,
        });
      }
    },
    [documentOptions, documentType, documentTypeId, pollJob, replacePreviewUrl, rulesError, selectedRules, stopPolling],
  );

  const handleRuleToggle = useCallback((ruleId: string) => {
    setSelectedRuleIds((currentIds) =>
      currentIds.includes(ruleId)
        ? currentIds.filter((currentId) => currentId !== ruleId)
        : [...currentIds, ruleId],
    );
  }, []);

  const handleBypassToggle = useCallback((ruleId: string) => {
    setBypassedRuleIds((currentIds) =>
      currentIds.includes(ruleId)
        ? currentIds.filter((currentId) => currentId !== ruleId)
        : [...currentIds, ruleId],
    );
  }, []);

  const handleSelectAllRules = useCallback(() => {
    setSelectedRuleIds((currentIds) =>
      currentIds.length === availableRules.length ? [] : availableRules.map((rule) => rule.id),
    );
  }, [availableRules]);

  const handleGroupToggle = useCallback(
    (ruleIdsInGroup: string[]) => {
      setSelectedRuleIds((currentIds) => {
        const allSelected = ruleIdsInGroup.every((id) => currentIds.includes(id));
        if (allSelected) {
          return currentIds.filter((id) => !ruleIdsInGroup.includes(id));
        }
        const merged = new Set(currentIds);
        ruleIdsInGroup.forEach((id) => merged.add(id));
        return Array.from(merged);
      });
    },
    [],
  );

  const handleStopAnalysis = useCallback(async () => {
    if (!currentJobId) {
      return;
    }

    try {
      await cancelValidationJob(currentJobId);
      setStatus(createWorkingStatus("Stopping analysis..."));
    } catch (error) {
      setStatus({
        label: error instanceof Error ? error.message : "Failed to stop analysis.",
        tone: "error",
        isLoading: false,
      });
    }
  }, [currentJobId]);

  const setNextTab = useCallback((tab: WorkspaceTabKey) => {
    setActiveTab(tab);
  }, []);

  const documentId = result?.document_id || null;
  const analysis = result?.analysis || null;
  const pages = result?.pages || [];

  useEffect(() => {
    void loadDocumentTypes();
    return () => {
      stopPolling();
      revokePreviewUrl(previewUrlRef.current);
    };
  }, [loadDocumentTypes, stopPolling]);

  useEffect(() => {
    void loadRules();
  }, [loadRules]);

  return {
    activeTab,
    analysis,
    appName: APP_NAME,
    availableRules,
    documentId,
    documentOptions,
    documentType,
    documentTypeId,
    documentTypes,
    isBusy,
    uploadHint,
    pages,
    result,
    rulesError,
    runOutcome,
    selectedRuleIds,
    bypassedRuleIds,
    selectedRules,
    sourceFilename,
    sourcePreviewUrl,
    status,
    beginUpload,
    handleDocumentOptionChange,
    handleDocumentTypeChange,
    handleRuleToggle,
    handleBypassToggle,
    handleGroupToggle,
    handleSelectAllRules,
    handleStopAnalysis,
    loadRules,
    setNextTab,
  };
}
