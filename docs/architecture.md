# Architecture

## System Overview

Petra Vision follows a three-tier architecture: a React frontend communicates with a FastAPI backend, which orchestrates a validation pipeline powered by AI providers.

```
+-------------------------------------------+
|         React Frontend (port 5173)        |
|  - Microsoft Entra ID auth gate           |
|  - Tab-based UI (source/extracted/        |
|    text analysis/visual analysis)         |
|  - PDF upload, rule selection, export     |
+-------------------+-----------------------+
                    | HTTP REST (JSON + multipart)
                    v
+-------------------------------------------+
|       FastAPI Backend (port 8000)         |
|  /api/v1 routes:                          |
|  - validations (sync + async jobs)        |
|  - rules, export, feedback, health        |
+-------------------+-----------------------+
                    | Orchestration
                    v
+-------------------------------------------+
|        Validation Pipeline                |
|  1. PDF text/table extraction             |
|  2. Page rendering to images              |
|  3. Text rule analysis (LLM)             |
|  4. Vision rule analysis (LLM + images)  |
|  5. Result aggregation                    |
+-------------------------------------------+
```

## Tech Stack

### Backend

| Component | Technology |
|-----------|-----------|
| HTTP framework | FastAPI 0.115+ |
| Data validation | Pydantic 2.7+ |
| PDF text extraction | pdfplumber 0.11+ |
| PDF page rendering | PyMuPDF (fitz) 1.24+ |
| Image processing | Pillow 10.3+ |
| AI (Anthropic) | anthropic 0.86+ |
| Auth tokens | PyJWT 2.9+ |
| Retry logic | tenacity 9.0+ |
| CLI | Typer 0.12+ |
| PDF report generation | FPDF2 2.8+ |
| Configuration | PyYAML 6.0+ |

### Frontend

| Component | Technology |
|-----------|-----------|
| UI framework | React 19 |
| Language | TypeScript 6 |
| Build tool | Vite 8 |
| Styling | Tailwind CSS 4 |
| Authentication | @azure/msal-react 3 |

### Infrastructure

| Component | Technology |
|-----------|-----------|
| Container runtime | Docker / Docker Compose |
| Cloud hosting | Azure Container Apps |
| Container registry | Azure Container Registry |
| Infrastructure-as-code | Azure Bicep |
| CLI deployment | Azure Developer CLI (azd) |

## Ephemeral Data Model

Petra Vision has **no persistent storage by design**:

- Uploaded PDFs are written to a temporary directory (`data/tmp/`) and deleted after analysis completes.
- Rendered page images are transient and exist only during pipeline execution.
- Analysis results are returned in the HTTP response and not stored server-side.
- The async job queue is in-memory (a Python `dict`) and is lost on server restart.

This architecture ensures no sensitive document data persists on the server.

## Request Lifecycle

### Synchronous Validation

1. Client uploads PDF + selected rules via `POST /api/v1/validations`
2. Backend validates the PDF (size, magic bytes, content type)
3. PDF is staged to a temp file in the working directory
4. `ValidationService` loads rules and invokes `ValidationPipeline.run()`
5. Pipeline extracts text/tables, renders pages, runs text + vision analysis
6. Results are aggregated into `DocumentValidationResponse`
7. Temp file is cleaned up; response is returned

### Asynchronous Validation (Job Queue)

1. Client creates a job via `POST /api/v1/validations/jobs`
2. Backend returns a `job_id` immediately
3. A background thread runs the validation pipeline
4. Client polls `GET /api/v1/validations/jobs/{job_id}` for status
5. When complete, the response includes the full `DocumentValidationResponse`

## Folder Structure

```
petra-data-file/
  src/
    main.py                    # FastAPI app factory + Typer CLI
    api/
      routers/                 # Route handlers (validations, rules, export, etc.)
      middleware.py             # CORS, request logging
      deps.py                  # Dependency injection, auth guards
      errors.py                # Exception handlers
    core/
      config.py                # Settings (pydantic-settings) + app.yaml loader
      azure_auth.py            # Azure AD token validation
      security.py              # Security utilities
      prompting.py             # LLM prompt template loading
      logging.py               # Structured logging
    pipeline/
      orchestrator.py          # ValidationPipeline: coordinates all stages
      pdf_extractor.py         # Text/table extraction via pdfplumber
      pdf_renderer.py          # Page-to-image rendering via PyMuPDF
      text_rule_analyzer.py    # LLM-based text rule analysis
      vision_rule_analyzer.py  # LLM-based vision rule analysis
      result_builder.py        # Final response construction
    providers/
      text/                    # Claude text provider
      vision/                  # Claude vision provider
      router.py                # Model and effort per call (defaults, rule overrides)
      models.py                # Model registry (config/models.yaml)
      analysis_result.py       # JSON schema + payload compaction
    schemas/                   # Pydantic models for all request/response types
    services/
      validation_service.py    # Orchestrates rule loading + pipeline execution
      validation_job_service.py# In-memory async job queue
      rule_service.py          # Rule definition loading/filtering
      auth_service.py          # Auth business logic
  frontend/
    src/
      App/                     # Main app component + state hook
      components/              # All UI components
      auth/                    # MSAL config and client
      config/                  # Runtime configuration
      types/                   # TypeScript interfaces
      utils/                   # Utility functions
  config/
    app.yaml                   # PDF, vision, and report settings
    text_analysis_system_prompt.md
    vision_analysis_system_prompt.md
  rules/
    rules.json                 # Validation rule definitions
  infra/
    main.bicep                 # Azure infrastructure
    main.parameters.json
  docker/
    AppDevDockerfile            # Backend Dockerfile
  frontend/docker/
    AppDevDockerfile            # Frontend Dockerfile
  docker-compose.yml           # Local dev orchestration
  requirements.txt             # Python dependencies
```
