# Petra Vision API

A FastAPI service to upload a PDF, extract its contents with `pdfplumber`, and keep a real analysis stage built from that extracted content and the selected validation rules.

The same run can be reviewed in the React frontend through tabs that separate the process into three views:

- **Original PDF**
- **Extracted Text and Tables**
- **Analysis Derived From the Extraction**

The operator workflow is protected by Microsoft Entra ID. The React frontend requires a successful Microsoft sign-in, and the FastAPI backend accepts only Azure access tokens issued by the configured tenant, audience, scope, and approved frontend app registration.

---

## Setup

Requires Python 3.10 or newer. Create and activate a virtual environment, then install dependencies:

```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# Mac/Linux
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

This is required before running the API, the tests, or any of the scripts.

---

## Quick Start

1. **Configure**
   - Copy `env.example` to `.env`.
   - Copy `frontend/.env.example` to `frontend/.env`.
   - Choose providers with `TEXT_PROVIDER` and `VISION_PROVIDER` using `openai` or `claude`.
   - For Claude, configure `ANTHROPIC_API_KEY` (the app also accepts `ANTHROPIC_AI_API_KEY` and `ANTROPIC_AI_API_KEY`).
  - Configure Microsoft Entra ID values for the backend API and frontend SPA. The repository templates are already set for:
     - tenant `dce78d1e-e927-4f5a-8d06-0035eaf8cc08`
     - frontend app `fa60a728-3038-450f-ba94-8e89667048a4`
     - API app `f4cb04ea-3fd2-4d6d-83be-a08bb993f9e9`
     - API scope `api://f4cb04ea-3fd2-4d6d-83be-a08bb993f9e9/access_as_user`
     - accepted access token versions `1.0,2.0` in backend validation, so the API works whether your Entra app manifest still emits v1 tokens or has already been moved to v2
   - Optional tunables in `config/app.yaml`.
- Temporary workspace behavior is controlled with `LOCAL_WORKDIR`.

### Frontend

The supported UI now lives in the separate React app under `frontend/`.

- the legacy built-in backend UI under `src/ui/` is deprecated and no longer mounted
- the backend root `/` now returns a JSON service descriptor only
- use the standalone frontend service for operator workflows

2. **Run CLI (one-off PDF validation)**
   python -m src.main validate --pdf ./tests/sample.pdf --out ./data/reports/report.json
3. **Run API**
   python -m uvicorn src.main:app --reload --port 8000
   Then use the versioned API under `/api/v1`.
   Main extraction route: `POST /api/v1/validations` with `multipart/form-data`:
   - field `pdf`: your PDF file
   - field `rules_json`: selected validation rules sent from the frontend

4. **Run Frontend**
   cd frontend
   npm install
   npm run dev

   The browser will show a Microsoft sign-in gate before the workspace loads.

5. **Run with Docker Compose**
   docker compose up --build

The compose file starts two services:

- `petra_vision_api`
- `petra_vision_frontend`

---

## Azure Deployment

This repository now includes Azure Infrastructure as Code for:

- `azure.yaml` to orchestrate the workflow with `azd`
- `infra/main.bicep` to provision Azure Container Apps, Azure Container Registry, and Log Analytics
- `scripts/entra/sync_apps.py` to create or update the Microsoft Entra SPA and API app registrations in code

### What gets provisioned

- `1` Azure Container Apps environment
- `1` Azure Container Registry
- `1` Log Analytics workspace
- `1` Container App for the FastAPI backend
- `1` Container App for the React frontend
- `AcrPull` role assignments so both Container Apps can pull private images from ACR with managed identity

The backend is intentionally public in Azure. With the current frontend architecture, the browser needs a public API URL to call the backend directly. Access is still protected by Microsoft Entra ID and restricted to the configured tenant, audience, scope, and frontend app registration.

### Microsoft Entra automation

The Entra setup described in the project notes is now automated in `scripts/entra/sync_apps.py`.

During `azd up`:

1. the `preprovision` hook creates or updates the backend API app registration and the frontend SPA app registration
2. it configures the API scope `access_as_user`
3. it assigns the signed-in user as owner when the current Azure principal supports Microsoft Graph `/me`
4. it stores the generated tenant/app IDs into the current `azd` environment
5. after infrastructure provisioning, the `postprovision` hook adds the deployed frontend URL as an SPA redirect URI

If your tenant requires admin consent for delegated API access, that approval still needs to be granted by an administrator after the app registrations are created.

### Frontend runtime config in Azure

The production frontend container no longer depends on baked-in `VITE_*` build arguments for Azure auth or API URLs.

- local Vite development still reads `frontend/.env`
- the Azure Nginx container writes `runtime-config.js` at startup from container environment variables
- this lets the same frontend image run in different subscriptions or tenants without rebuilding it for each environment
- the runtime variable mapping used for provisioning lives in `infra/main.parameters.json`, which is the standard `azd` bridge between `.azure/<env>/.env` and Bicep parameters

### Deploy with azd

Minimum flow:

```bash
azd env new personal
azd env set AZURE_LOCATION eastus
azd env set-secret OPENAI_API_KEY
azd up
```

Use `azd env set-secret` for provider API keys so the raw secret does not get passed directly in the command line or stored in plain text in the local `azd` environment file.

Useful optional settings:

```bash
azd env set TEXT_PROVIDER openai
azd env set VISION_PROVIDER openai
azd env set OPENAI_TEXT_MODEL gpt-5.4-mini
azd env set OPENAI_VISION_MODEL gpt-5.4
azd env set CLAUDE_TEXT_MODEL claude-sonnet-4-6
azd env set CLAUDE_VISION_MODEL claude-sonnet-4-6
azd env set-secret ANTHROPIC_API_KEY
```

After deployment:

- `azd show` returns the provisioned resources
- `FRONTEND_ENDPOINT` is the public frontend URL
- `BACKEND_ENDPOINT` is the public backend URL
- if you change runtime environment variables later, run `azd provision` or `azd up`; `azd deploy` only updates images/code and does not rewrite Container App environment variables

### Deploy to another environment

The same flow can be repeated for a separate environment:

```bash
az login
azd auth login
azd env new prod
azd env set AZURE_LOCATION eastus
azd env set-secret OPENAI_API_KEY
azd up
```

The main caveat is Microsoft Entra permissions. If the signed-in principal cannot create app registrations or add owners in the target tenant, the Azure resources may still provision but the Entra automation step will fail until the required tenant permissions are granted.

---

## Output format

The pipeline returns results grouped by **page**, and it also returns a dedicated analysis section built from the extracted text and tables. The React frontend surfaces these parts in separate tabs so the original PDF, the extraction, and the analysis can be compared side by side during the same review:

{
  "document_id": "sample_20260326T140100",
  "page_count": 2,
  "source_filename": "sample.pdf",
  "analysis": {
    "overview": [
      { "label": "Pages", "value": "2", "detail": "Total pages processed from the PDF." },
      { "label": "Selected Rules", "value": "3", "detail": "Rules enabled for this analysis run." }
    ],
    "rule_assessments": [
      {
        "rule_id": "FMT-HEADINGS",
        "rule_name": "All page headers",
        "matched_pages": [1],
        "notes": ["Keyword overlap found on page(s): 1."]
      }
    ],
    "page_observations": [
      {
        "page": 1,
        "observations": ["Starts with: Statement of Financial Position", "1 table(s) detected."]
      }
    ]
  },
  "pages": [
    {
      "page": 1,
      "text": "Statement of Financial Position ...",
      "char_count": 4210,
      "tables": [
        {
          "index": 1,
          "rows": [
            ["Assets", "2024", "2023"],
            ["Cash", "100", "90"]
          ]
        }
      ]
    }
  ]
}

## Architecture

- **API**: FastAPI routers under `src/api/routers/` (validations, rules, export, feedback, health, auth)
- **Pipeline**: PDF extraction and derived analysis live in `src/pipeline/`; `orchestrator.py` drives the five-stage sequence
- **Services**: business orchestration lives in `src/services/`; `validation_job_service.py` manages the in-memory threaded job queue
- **Providers**: text and vision integrations live in `src/providers/` (`text/` and `vision/` subdirs, each with `base.py`, `openai.py`, `claude.py`, `factory.py`)
- **Schemas**: request and response contracts live in `src/schemas/`
- **Core**: settings, Azure auth, logging, and security middleware live in `src/core/`
- **Rules**: validation rule definitions in `rules/rules.json`
- **Config**: LLM system prompts in `config/text_analysis_system_prompt.md` and `config/vision_analysis_system_prompt.md`
- **Integration tests**: `tests/integration/` — `cases.yaml` (test case definitions), `conftest.py` (session-scoped pipeline fixture), `test_pipeline.py` (parametrised verdict assertions)
- **Test fixtures**: PDF files used by integration tests live in `tests/fixtures/documents/`. Synthetic test fixtures may be committed; real client documents stay local (do not commit)
- **Infra**: Azure Bicep templates (Container Apps, ACR, Log Analytics) live in `infra/`
- **Docs**: detailed documentation for pipeline, providers, auth, and deployment live in `docs/`

### Provider Abstraction

Text and vision analysis are provider-agnostic. The active provider is set via `.env` (`TEXT_PROVIDER=openai|claude`, `VISION_PROVIDER=openai|claude`). Provider adapters implement a shared base class interface; the factory pattern in each `factory.py` resolves the correct adapter at startup.

### Async Job Queue

Validation runs are dispatched as background jobs. Clients poll `GET /api/v1/validations/jobs/{job_id}` for status; cancellation is supported via `POST /api/v1/validations/jobs/{job_id}/cancel`. The queue is in-memory — no persistent storage; jobs are lost on restart.

### Ephemeral Storage

The service does not persist uploaded PDFs, validation runs, or feedback records.

- uploaded PDFs are written to `data/tmp/` during processing and deleted afterwards
- rendered page images are temporary and removed after use
- feedback is stored in-memory only
- the React frontend previews the original PDF from the browser's local file object, not from a server-side public URL

---

## Configuration

- `config/app.yaml` controls:
  - PDF rendering DPI
  - image format
  - report toggles
- Environment variables control:
  - text provider selection via `TEXT_PROVIDER`
  - vision provider selection via `VISION_PROVIDER`
  - OpenAI or Claude model selection
  - Microsoft Entra ID tenant, audience, scope, and allowed SPA client app IDs
  - temporary workspace location via `LOCAL_WORKDIR`
  - legacy `ENABLE_UI` parsing for backward compatibility only; the backend no longer mounts the built-in UI

## Testing

### Unit tests

Unit tests cover individual components (page classification, number normalisation, etc.) and run without API keys:

```bash
# Run all unit tests
pytest

# Run a single test file or test
pytest tests/test_page_classifier.py
pytest tests/test_number_normalization.py
pytest -k "test_name"
```

Unit test files:
- `tests/test_page_classifier.py` — page type classification and rule-to-page applicability logic
- `tests/test_number_normalization.py` — pdfplumber number-artifact normalization helper
- `tests/test_double_underline.py` — double-underline vector-hint extraction and detection logic

### Smoke test

The smoke test runs the full extraction pipeline against `tests/sample.pdf` without LLM calls. It verifies the output structure (document_id, pages, analysis, rule_assessments) but skips silently if the sample PDF is missing.

```bash
pytest tests/smoke_test.py
```

### Integration tests

Integration tests run real PDF documents through the full validation pipeline — including live LLM calls — and assert that each rule produces the expected verdict. They require a valid `.env` with API keys.

```bash
# Run all integration tests
pytest tests/integration -m integration

# Critical-severity rules only (~29% of full cost)
pytest tests/integration -m integration --severity critical

# One specific rule across all cases
pytest tests/integration -m integration --rule GRAM-SPELL

# Multiple rules
pytest tests/integration -m integration --rule DATE-INTEGRITY-FULL --rule CVR-DATE-CHECK

# One specific case
pytest tests/integration -m integration --case my_fund

# Run with more parallel workers (default 4; lower to respect API rate limits)
pytest tests/integration -m integration --workers 8

# Filter by node ID substring (case or rule name)
pytest tests/integration -m integration -k "my_fund/BS-FMT"

# Verbose output — shows the full node ID and failure details
pytest tests/integration -m integration -v
```

Test cases are configured in `tests/integration/cases.yaml`. Each case lists a document path, the rule IDs to run, and the expected verdict per rule (`pass` | `fail` | `needs_review` | `not_applicable`).

**Adding a new test case:**

1. Place the PDF in `tests/fixtures/documents/`
2. Add a case block to `tests/integration/cases.yaml` with `id`, `document`, and `rules` (leave `expected` out)
3. Run the discovery script to see what the pipeline returns:
   ```bash
   python scripts/update_integration_expectations.py
   ```
4. Paste the printed `expected` block into `cases.yaml` and commit

---

## Notes

- This service extracts PDF content with `pdfplumber` and uses that extracted material as the input to a separate analysis stage.
- The service runs without persistent storage. Uploaded PDFs and rendered page images are removed after analysis.
- The backend disables public OpenAPI and documentation routes so the API surface is not exposed anonymously.
- The legacy backend UI under `src/ui/` is deprecated and no longer served by FastAPI.
- The supported operator interface is the separate React frontend under `frontend/`.
- Ensure you have permission to process the uploaded documents.

---

## License

MIT
