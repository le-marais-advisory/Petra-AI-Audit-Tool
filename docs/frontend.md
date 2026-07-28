# Frontend

The Petra Vision frontend is a React/TypeScript single-page application built with Vite and styled with Tailwind CSS.

## Tech Stack

- **React 19** with TypeScript 6
- **Vite 8** for development and builds
- **Tailwind CSS 4** for styling
- **@azure/msal-react** for Microsoft Entra ID authentication

## Component Overview

### Layout Components

| Component | Path | Description |
|-----------|------|-------------|
| **App** | `src/App/index.tsx` | Root component, composes the full layout |
| **WorkspaceShell** | `src/components/WorkspaceShell/` | Main layout container (hero + sidebar + content) |
| **HeroBanner** | `src/components/HeroBanner/` | App header with title, user info, and sign-out |
| **AuthGate** | `src/components/AuthGate/` | Authentication wrapper, enforces sign-in |

### Input Components

| Component | Path | Description |
|-----------|------|-------------|
| **UploadPanel** | `src/components/UploadPanel/` | PDF file picker with drag-and-drop, upload progress, cancel |
| **RulesSidebar** | `src/components/RulesSidebar/` | Rule selection panel with checkboxes, group toggles, refresh |
| **RuleSelectionCard** | `src/components/RuleSelectionCard/` | Individual rule picker item |

### Display Components

| Component | Path | Description |
|-----------|------|-------------|
| **TabNavigation** | `src/components/TabNavigation/` | Tab switcher (Source, Extracted, Text Analysis, Visual Analysis) |
| **SourcePreview** | `src/components/SourcePreview/` | PDF viewer for the original document |
| **ExtractionResults** | `src/components/ExtractionResults/` | Displays extracted text and tables per page |
| **AnalysisResults** | `src/components/AnalysisResults/` | Text/vision rule results grouped by verdict: "Needs attention" plus collapsed pass / not-applicable roll-ups, with a coverage line |
| **RuleResultCard** | `src/components/RuleResultCard/` | Single rule assessment display (verdict, summary, findings, citations) |
| **DisclosureToggle** | `src/components/DisclosureToggle/` | Shared chevron toggle for collapsible sections and card details |
| **StatusPill** | `src/components/StatusPill/` | Color-coded verdict badge (pass/fail/needs_review) |
| **EmptyState** | `src/components/EmptyState/` | Placeholder when no data is available |

### Action Components

| Component | Path | Description |
|-----------|------|-------------|
| **ExportModal** | `src/components/ExportModal/` | PDF export dialog |
| **FeedbackModal** | `src/components/FeedbackModal/` | Feedback submission form |

## Application State

All state management is in `src/App/behaviors.tsx` via the `useAppBehavior()` hook:

### Key State

| State | Type | Description |
|-------|------|-------------|
| `activeTab` | string | Currently selected tab |
| `analysis` | DocumentAnalysisSchema | Full analysis results |
| `pages` | PageExtractionSchema[] | Extracted page data |
| `selectedRuleIds` | Set\<string\> | Selected rule IDs |
| `bypassedRuleIds` | Set\<string\> | Bypassed rule IDs |
| `isBusy` | boolean | Whether an operation is in progress |
| `status` | string | Current status message |

### API Operations

The `useAppBehavior` hook handles:

1. **Rules fetching** - `GET /api/v1/rules` on mount
2. **File upload** - `POST /api/v1/validations/jobs` with PDF + selected rules
3. **Job polling** - `GET /api/v1/validations/jobs/{job_id}` until complete
4. **PDF export** - `POST /api/v1/export` to generate a report
5. **Auth state** - token management via MSAL

## Tab Navigation

The UI has four main views accessible via tabs:

| Tab | Content | Data Source |
|-----|---------|-------------|
| **Source** | Original PDF viewer | Uploaded File object |
| **Extracted** | Extracted text and tables per page | `pages` array |
| **Text Analysis** | Text-based rule results per page | `analysis.text_page_results` |
| **Visual Analysis** | Vision-based rule results per page | `analysis.visual_page_results` |

## Upload Flow

1. User selects rules in the `RulesSidebar`
2. User drops/selects a PDF in `UploadPanel`
3. Frontend sends `POST /api/v1/validations/jobs` (multipart form data)
4. Backend returns a `job_id`
5. Frontend polls `GET /api/v1/validations/jobs/{job_id}` at intervals
6. Progress is displayed in the upload panel
7. On completion, results populate across all tabs
8. User can cancel the job via the cancel button

## Export Flow

1. User clicks the export button after analysis completes
2. `ExportModal` opens with export options
3. Frontend sends `POST /api/v1/export` with the analysis result
4. Backend generates a PDF report and streams it back
5. Browser triggers a file download

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `VITE_APP_NAME` | Application display name | `Petra Vision` |
| `VITE_API_BASE_URL` | Backend API base URL | `http://localhost:8000` |
| `VITE_API_PREFIX` | API path prefix | `/api/v1` |
| `VITE_AUTH_ENABLED` | Enable authentication gate | `false` |

See [Authentication](authentication.md) for auth-specific variables.

## Development

### Start the dev server

```bash
cd frontend
npm install
npm run dev
```

The frontend runs on `http://localhost:5173` with hot module replacement.

### Build for production

```bash
npm run build
```

Output goes to `frontend/dist/`.

## Key Files

- `frontend/src/App/index.tsx` - Main App component
- `frontend/src/App/behaviors.tsx` - Application state and API logic
- `frontend/src/types/api.ts` - TypeScript interfaces for API types
- `frontend/src/auth/config.ts` - MSAL auth configuration
- `frontend/src/config/runtime.ts` - Runtime environment configuration
- `frontend/vite.config.ts` - Vite build configuration
