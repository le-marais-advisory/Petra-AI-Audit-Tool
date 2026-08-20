import { useMsal } from "@azure/msal-react";

import { authEnabled, azurePostLogoutRedirectUri } from "@/auth/config";
import { showRulesSidebar } from "@/config/features";
import { useState } from "react";

import { AnalysisResults } from "@/components/AnalysisResults";
import { EmptyState } from "@/components/EmptyState";
import { ExportModal } from "@/components/ExportModal";
import { ExtractionResults } from "@/components/ExtractionResults";
import { HeroBanner } from "@/components/HeroBanner";
import { RulesSidebar } from "@/components/RulesSidebar";
import { SourcePreview } from "@/components/SourcePreview";
import { TabNavigation } from "@/components/TabNavigation";
import { UploadPanel } from "@/components/UploadPanel";
import { WorkspaceShell } from "@/components/WorkspaceShell";

import { useAppBehavior } from "./behaviors";


export function App() {
  const { accounts, instance } = useMsal();
  const {
    activeTab,
    analysis,
    appName,
    availableRules,
    beginUpload,
    documentId,
    handleRuleToggle,
    handleBypassToggle,
    handleGroupToggle,
    handleSelectAllRules,
    handleStopAnalysis,
    isBusy,
    loadRules,
    pages,
    result,
    rulesError,
    runOutcome,
    selectedRuleIds,
    bypassedRuleIds,
    sourceFilename,
    sourcePreviewUrl,
    status,
    setNextTab,
  } = useAppBehavior();

  const signedInAccount = instance.getActiveAccount() || accounts[0] || null;
  const signedInAs = authEnabled ? signedInAccount?.name || signedInAccount?.username || null : null;

  const handleSignOut = async () => {
    if (!authEnabled) {
      return;
    }
    await instance.logoutPopup({
      account: signedInAccount || undefined,
      postLogoutRedirectUri: azurePostLogoutRedirectUri,
    });
  };

  const [exportOpen, setExportOpen] = useState(false);

  return (
    <>
    <WorkspaceShell
      hero={<HeroBanner appName={appName} authEnabled={authEnabled} signedInAs={signedInAs} onSignOut={handleSignOut} />}
      sidebar={
        showRulesSidebar ? (
          <RulesSidebar
            rules={availableRules}
            selectedRuleIds={selectedRuleIds}
            bypassedRuleIds={bypassedRuleIds}
            onRuleToggle={handleRuleToggle}
            onBypassToggle={handleBypassToggle}
            onGroupToggle={handleGroupToggle}
            onSelectAll={handleSelectAllRules}
            onRefresh={loadRules}
            errorMessage={rulesError}
          />
        ) : null
      }
      main={
        <div className="space-y-6">
          {!showRulesSidebar && rulesError ? (
            <p className="section-panel px-6 py-4 text-sm text-rose-700">
              Validation rules could not be loaded: {rulesError}
            </p>
          ) : null}

          <UploadPanel
            documentId={documentId}
            isBusy={isBusy}
            status={status}
            onFileSelected={beginUpload}
            onStopAnalysis={handleStopAnalysis}
          />

          {analysis && !isBusy ? (
            <div className="flex justify-end">
              <button
                type="button"
                className="inline-flex items-center gap-2 rounded-full bg-slate-950 px-5 py-2.5 text-sm font-medium text-white transition hover:bg-slate-800"
                onClick={() => setExportOpen(true)}
              >
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4">
                  <path d="M10.75 2.75a.75.75 0 0 0-1.5 0v8.614L6.295 8.235a.75.75 0 1 0-1.09 1.03l4.25 4.5a.75.75 0 0 0 1.09 0l4.25-4.5a.75.75 0 0 0-1.09-1.03l-2.955 3.129V2.75Z" />
                  <path d="M3.5 12.75a.75.75 0 0 0-1.5 0v2.5A2.75 2.75 0 0 0 4.75 18h10.5A2.75 2.75 0 0 0 18 15.25v-2.5a.75.75 0 0 0-1.5 0v2.5c0 .69-.56 1.25-1.25 1.25H4.75c-.69 0-1.25-.56-1.25-1.25v-2.5Z" />
                </svg>
                Export PDF Report
              </button>
            </div>
          ) : null}

          <section className="section-panel overflow-hidden">
            <TabNavigation activeTab={activeTab} onTabChange={setNextTab} />

            <div className="p-5">
              {activeTab === "source" ? (
                <SourcePreview previewUrl={sourcePreviewUrl} sourceFilename={result?.source_filename || sourceFilename} />
              ) : null}

              {activeTab === "extracted" ? <ExtractionResults pages={pages} /> : null}

              {activeTab === "text-analysis" ? (
                analysis ? (
                  <AnalysisResults
                    analysisType="text"
                    emptyMessage={
                      analysis.text_rule_count === 0
                        ? "No text rules were selected for this run."
                        : isBusy
                          ? "Text analysis is still running. Results will appear as pages are evaluated."
                          : "Text analysis could not be completed. Check that the text provider is configured correctly."
                    }
                    items={analysis.text_page_results || []}
                    documentId={documentId}
                    sourceFilename={sourceFilename}
                    ruleAssessments={analysis.rule_assessments || []}
                    isStreaming={isBusy}
                    runOutcome={runOutcome}
                  />
                ) : (
                  <EmptyState title="No text analysis yet" description="Upload a PDF to see text/content rule results." />
                )
              ) : null}

              {activeTab === "visual-analysis" ? (
                analysis ? (
                  <AnalysisResults
                    analysisType="vision"
                    emptyMessage={
                      analysis.vision_rule_count === 0
                        ? "No visual rules were selected for this run."
                        : isBusy
                          ? "Visual analysis has not started yet. Text rules are evaluated first."
                          : "Visual analysis could not be completed. Check that the vision provider is configured correctly."
                    }
                    items={analysis.visual_page_results || []}
                    documentId={documentId}
                    sourceFilename={sourceFilename}
                    ruleAssessments={analysis.rule_assessments || []}
                    isStreaming={isBusy}
                    runOutcome={runOutcome}
                  />
                ) : (
                  <EmptyState title="No visual analysis yet" description="Upload a PDF to inspect visual-rule status." />
                )
              ) : null}
            </div>
          </section>
        </div>
      }
    />

      <ExportModal
        isOpen={exportOpen}
        onClose={() => setExportOpen(false)}
        documentId={documentId}
        sourceFilename={sourceFilename}
        pageCount={result?.page_count || 0}
        analysis={analysis}
      />
    </>
  );
}
