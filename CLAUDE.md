# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Petra Vision is an AI-powered PDF document validation tool. It extracts content from PDFs, runs configurable validation rules through LLMs (OpenAI or Anthropic), and produces structured audit reports. It exposes both a REST API (FastAPI) and a React SPA frontend with Microsoft Entra ID authentication.

## Commands

### Setup

Requires Python 3.10 or newer.

```bash
# Mac/Linux
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt

# Windows
python -m venv .venv && .venv\Scripts\activate && pip install -r requirements.txt
```

Required before running the API, tests, or any scripts.

### Backend

```bash
# Run API server
python -m uvicorn src.main:app --reload --port 8000

# Run all unit tests
pytest

# Run a single test file or test
pytest tests/test_page_classifier.py
pytest tests/test_number_normalization.py
pytest -k "test_name"

# Run smoke test (no API keys required)
pytest tests/smoke_test.py

# Run CLI validation (one-off, no server)
python -m src.main validate --pdf ./tests/sample.pdf --out ./data/reports/report.json
```

Unit test files:
- `tests/test_page_classifier.py` — page type classification and rule-to-page applicability logic
- `tests/test_number_normalization.py` — pdfplumber number-artifact normalization helper
- `tests/test_double_underline.py` — double-underline vector-hint extraction and detection logic
- `tests/smoke_test.py` — full pipeline structural check against `tests/sample.pdf`; skips silently if the file is absent

### Integration Tests

Integration tests run fixed documents through the live validation pipeline (real LLM calls) and assert that each rule produces the expected verdict. They require a valid `.env` with API keys.

```bash
# Run all integration tests
pytest tests/integration -m integration

# Critical-severity rules only (~29% of full cost)
pytest tests/integration -m integration --severity critical

# One specific rule across all cases
pytest tests/integration -m integration --rule GRAM-SPELL

# Multiple rules
pytest tests/integration -m integration --rule DATE-INTEGRITY-FULL --rule CVR-DATE-CHECK

# Combined: severity + rule filter
pytest tests/integration -m integration --severity critical --rule GRAM-SPELL

# One specific case
pytest tests/integration -m integration --case my_fund

# Filter by node ID substring (case or rule name)
pytest tests/integration -m integration -k "my_fund"
pytest tests/integration -m integration -k "my_fund/BS-FMT"

# Verbose output — shows the full node ID and failure details
pytest tests/integration -m integration -v
```

**Adding a new test case:**

1. Place the PDF in `tests/fixtures/documents/`
2. Add a case block to `tests/integration/cases.yaml` with `id`, `document`, and `rules` (leave `expected` out)
3. Run the discovery script — it calls the pipeline and prints a ready-to-paste `expected` block:
   ```bash
   python scripts/update_integration_expectations.py
   ```
4. Review the printed verdicts, paste the `expected` block into `cases.yaml`, commit

Test cases are defined in `tests/integration/cases.yaml`. Each case specifies the document path (relative to repo root), the list of rule IDs to run, and the expected `verdict` per rule (`pass` | `fail` | `needs_review` | `not_applicable`). An optional `matched_pages` list can assert which pages the rule fired on.

### Frontend

```bash
cd frontend
cp .env.example .env   # fill in VITE_API_BASE_URL, VITE_AZURE_* values
npm install
npm run dev        # dev server on port 5173
npm run build      # TypeScript check + production build
```

### Docker (local full-stack)

```bash
docker compose up --build   # API on :8000, frontend on :5173
```

### Azure Deployment

```bash
azd env new <environment>
azd env set AZURE_LOCATION eastus
azd env set-secret ANTHROPIC_API_KEY   # or OPENAI_API_KEY
azd up
```

### Flag Analysis Tool

Standalone script that calls Claude to surface improvement areas from feedback or validation report data:

```bash
# Reads ~/Desktop/feedback_audit_tool/feedback.json by default
python flag_analysis/analyze_flags.py

# Point at a specific file (feedback.json or validation report JSON)
python flag_analysis/analyze_flags.py path/to/feedback.json --out analysis.md

# Dry run (no LLM call, just prints summary stats)
python flag_analysis/analyze_flags.py --skip-llm
```

Auto-detects two input shapes: a `feedback.json` list (from the `/app/data/feedback.json` API output) or a `DocumentValidationResponse` report (from the `validate` CLI).

## Architecture

### Validation Pipeline

The core logic lives in `src/pipeline/` and runs in five sequential stages:

1. **PDF Extraction** (`pdf_extractor.py`) — pdfplumber extracts text and tables per page; `page_classifier.py` assigns each page a type (e.g. `balance_sheet`) used for rule filtering
2. **Text Rule Analysis** (`text_rule_analyzer.py`) — LLM evaluates text-based rules against extracted content
3. **Page Rendering** (`pdf_renderer.py`) — PyMuPDF renders pages as images at configurable DPI; also extracts PDF vector drawing metadata (e.g. double-underline line positions) for rules that benefit from geometry hints
4. **Vision Rule Analysis** (`vision_rule_analyzer.py`) — LLM + vision evaluates visual rules against rendered images; supports configurable concurrency; injects PDF vector metadata into the prompt for rules that use it (e.g. `FMT-DOUBLE-UNDERLINE`)
5. **Result Aggregation** (`result_builder.py`) — merges verdicts into a `DocumentValidationResponse`

`src/pipeline/orchestrator.py` drives the pipeline and is called by `src/services/validation_service.py`.

### Key Source Directories

- `src/api/routers/` — FastAPI route handlers (validations, rules, export, feedback, health, auth)
- `src/pipeline/` — core processing stages
- `src/providers/` — AI provider adapters (`text/` and `vision/` subdirs, each with `base.py`, `openai.py`, `claude.py`, `factory.py`)
- `src/services/` — business logic layer; `validation_job_service.py` manages the in-memory threaded job queue
- `src/schemas/` — Pydantic request/response models
- `src/core/` — settings (`pydantic-settings` + `config/app.yaml`), Azure auth, logging, security middleware
- `frontend/src/` — React SPA with Microsoft Entra ID auth gate (`@azure/msal-react`)
- `rules/rules.json` — validation rule definitions (text and vision types)
- `config/app.yaml` — PDF rendering DPI, vision concurrency/temperature, report toggles
- `config/text_analysis_system_prompt.md` / `config/vision_analysis_system_prompt.md` — LLM system prompts (loaded at runtime by the analyzers)
- `flag_analysis/` — standalone LLM-powered feedback audit tool
- `scripts/` — utilities: `import_rules_from_excel.py` (bulk rule import), `entra/sync_apps.py` (Entra app registration automation), `update_integration_expectations.py` (discovery helper for integration tests), `debug_double_underlines.py` (PDF line-geometry inspector for debugging double-underline detection)
- `tests/integration/` — integration test suite: `cases.yaml` (test case definitions), `conftest.py` (session-scoped pipeline fixture), `test_pipeline.py` (parametrized verdict assertions)
- `tests/fixtures/documents/` — PDF files used by integration tests. Synthetic test fixtures may be committed; do not commit real client documents (add those locally or via shared storage)
- `infra/` — Azure Bicep templates (Container Apps, ACR, Log Analytics)
- `docs/` — detailed documentation for pipeline, providers, auth, deployment, etc.

### Provider Abstraction

Text and vision analysis are provider-agnostic. The active provider is set via `.env` (`TEXT_PROVIDER=openai|claude`, `VISION_PROVIDER=openai|claude`). Provider adapters implement a shared base class interface; the factory pattern in each `factory.py` resolves the correct adapter at startup.

### Async Job Queue

Validation runs are dispatched as background jobs via `validation_job_service.py`. Clients poll `GET /api/v1/validations/jobs/{job_id}` for status; cancellation is supported via `POST /api/v1/validations/jobs/{job_id}/cancel`. The queue is in-memory — no persistent storage; jobs are lost on restart.

### Ephemeral Storage

PDFs are written to `data/tmp/` during processing and deleted afterwards. Feedback is stored in-memory. There is no database or persistent file storage.

## Configuration

Copy `env.example` to `.env` and fill in:

- `AZURE_*` — Microsoft Entra ID tenant/client IDs for auth
- `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` (also accepts `ANTHROPIC_AI_API_KEY` / `ANTROPIC_AI_API_KEY` aliases)
- `TEXT_PROVIDER` / `VISION_PROVIDER` — select active LLM provider per analysis type
- `OPENAI_TEXT_MODEL`, `CLAUDE_TEXT_MODEL`, etc. — optional model overrides

`config/app.yaml` controls PDF rendering (DPI defaults to 300), vision settings (concurrency, temperature, seed, max tokens, image detail), and report toggles (e.g. `include_thumbnails`).

`rules/rules.json` rule objects carry: `id`, `name`, `analysis_type` (text|vision), `query`, `description`, `acceptance_criteria`, `severity` (major|minor|critical), and optional `group`/`bypassable` fields.
