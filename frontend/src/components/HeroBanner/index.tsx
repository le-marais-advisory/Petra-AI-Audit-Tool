import petraLogo from "@/assets/petra-logo.svg";

import type { HeroBannerProps } from "./behaviors";


export function HeroBanner({ appName, authEnabled, onSignOut, signedInAs }: HeroBannerProps) {
  return (
    <section className="glass-panel overflow-hidden">
      <div className="flex flex-col gap-6 px-6 py-8 lg:flex-row lg:items-start lg:justify-between lg:px-10">
        <div>
          <img src={petraLogo} alt="Petra" className="mb-4 h-9 w-auto" />
          <p className="mb-3 inline-flex rounded-full bg-accentSoft px-3 py-1 text-xs font-semibold uppercase tracking-[0.22em] text-accent">
            PDF Extraction Workspace
          </p>
          <h1 className="max-w-3xl text-3xl font-semibold tracking-tight text-slate-950 sm:text-4xl">
            {appName}
          </h1>
          <p className="mt-4 max-w-2xl text-sm leading-6 text-slate-600 sm:text-base">
            Upload a PDF, inspect the extracted text page by page, and review text and visual analysis without
            persisting the document on the server.
          </p>
        </div>

        {authEnabled ? (
          <div className="rounded-[1.5rem] border border-slate-200 bg-white/80 px-4 py-4 shadow-sm backdrop-blur-sm">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Signed In</p>
            <p className="mt-2 max-w-xs text-sm font-medium text-slate-900">{signedInAs || "Unknown account"}</p>
            <button
              className="mt-4 cursor-pointer rounded-full border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:border-slate-400 hover:bg-slate-50"
              type="button"
              onClick={() => {
                void onSignOut();
              }}
            >
              Sign out
            </button>
          </div>
        ) : null}
      </div>
    </section>
  );
}
