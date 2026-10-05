# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Petra Vision is an AI-powered document validation tool. It validates PDF financial statements and capital-event Excel workbooks against configurable rules, using Claude (Anthropic) plus deterministic checks for workbooks, and produces structured audit reports. The user picks the document type per run (`GET /document-types`); see `docs/document-types.md`. It exposes both a REST API (FastAPI) and a React SPA frontend with Microsoft Entra ID authentication.

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
python -m src.main --file ./tests/sample.pdf --out ./data/reports/report.json
python -m src.main --file ./workbook.xlsx --document-type capital_event_workbook --event-type capital_call --out ./data/reports/ce.json
```

Unit test files:
- `tests/test_page_classifier.py` — page type classification and rule-to-page applicability logic
- `tests/test_number_normalization.py` — pdfplumber number-artifact normalization helper
- `tests/test_double_underline.py` — double-underline vector-hint extraction and detection logic
- `tests/smoke_test.py` — full pipeline structural check against `tests/sample.pdf`; skips silently if the file is absent
- `tests/test_llm_routing.py` — model/effort routing, rule overrides, refusal fallback, cache grouping per target
- `tests/test_llm_compare.py` — the model comparison tool end to end with a fake router

Capital-event workbook tests (TDD; written ahead of the implementation in `src/pipeline/workbook/`):
- `tests/fixtures/generate_capital_event_fixtures.py` — synthetic workbook generator (event type x layout variant x seeded defect); each fixture's manifest carries sheet roles, golden layouts, truth values and expected verdicts. `tests/conftest.py` exposes it as the session fixture `capital_event_fixtures`. Never commit real client workbooks
- `tests/test_capital_event_fixtures.py` — generator self-checks (cached values, tie-outs, golden layouts)
- `tests/test_document_types.py`, `tests/test_capital_event_rule_pack.py` — document-type registry, upload API, rule pack filtering
- `tests/test_workbook_{loader,layout,extract,checks,facts,pipeline}.py` — loader/inventory/roles/skeleton, layout validator, typed extraction, deterministic checks, hybrid facts, offline pipeline
- `tests/test_workbook_keys.py`, `tests/test_event_labels.py` — (vehicle, name) investor matching across sheets; combined event labels and fee-quarter ranges
- Fixtures with `with_prior=True` also generate the prior-event workbook used by the cross-event (`CE-XEV-*`) rules
- The `multi_vehicle` variant mirrors the multi-vehicle reference client: three vehicles plus a GP look-through block, no fund-level driver row, hidden Merge tabs, a combined prior-event label, two fee quarters billed at once and a Summary with one section per vehicle

### Evals (live LLM)

```bash
pytest tests/evals -m eval                                  # layout mapping, role assignment, hybrid rules
CAPITAL_EVENT_SAMPLE_PATH=temp/capital-event-rules/sample-workbook.xlsx pytest tests/evals/test_real_sample_eval.py -m eval
# multi-vehicle net event with its prior workbook (never commit the files)
CAPITAL_EVENT_NET_SAMPLE_PATH="temp/test-run-2026-10-02/BPCP IV - Capital Call #19 - 08.04.2026.xlsm" \
CAPITAL_EVENT_NET_PRIOR_PATH="temp/test-run-2026-10-02/BPCP IV - Distribution #8 - 12.11.2025 V4.xlsm" \
  pytest tests/evals/test_real_sample_eval.py -m eval -k net
```

### Comparing Models (live LLM)

Measure a model or effort change on labelled documents before making it (see `docs/model-comparison.md`):

```bash
python -m src.evaluation.llm_compare run                                   # plan + cost estimate, no API calls
python -m src.evaluation.llm_compare run --variant current --variant sonnet-5-5-medium --repeats 3 --yes
python -m src.evaluation.llm_compare run --suite temp/my_suite.yaml --yes  # local real documents (never commit)
python -m src.evaluation.llm_compare report data/llm_compare/<run> [<other run> ...]   # re-render / merge
python -m src.evaluation.llm_compare bootstrap data/llm_compare/<run> --variant current  # draft expected verdicts
```

- Variants live in `evals/llm_variants.yaml` (defaults, per-purpose stages, per-rule overrides); the default suite `evals/llm_suite.yaml` imports `tests/integration/cases.yaml` and `tests/evals/capital_event_cases.yaml`
- Output (`results.json`, self-contained `report.html`) goes to git-ignored `data/llm_compare/<timestamp>/`; runs start with a cold prompt cache unless `--warm-cache`
- Code: `src/evaluation/llm_compare/` (suite, variants, runner, metrics, report); `tests/test_llm_compare.py` covers it offline with a fake router

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
azd env set-secret ANTHROPIC_API_KEY
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

Auto-detects two input shapes: a `feedback.json` list (from the `/app/data/feedback.json` API output) or a `DocumentValidationResponse` report (from the validation CLI, `python -m src.main`).

## Architecture

### Validation Pipeline

The core logic lives in `src/pipeline/` and runs in five sequential stages:

1. **PDF Extraction** (`pdf_extractor.py`) — pdfplumber extracts text and tables per page; `page_classifier.py` assigns each page a type (e.g. `balance_sheet`) used for rule filtering
2. **Text Rule Analysis** (`text_rule_analyzer.py`) — LLM evaluates text-based rules against extracted content
3. **Page Rendering** (`pdf_renderer.py`) — PyMuPDF renders pages as images at configurable DPI; also extracts PDF vector drawing metadata (e.g. double-underline line positions) for rules that benefit from geometry hints
4. **Vision Rule Analysis** (`vision_rule_analyzer.py`) — LLM + vision evaluates visual rules against rendered images; supports configurable concurrency; injects PDF vector metadata into the prompt for rules that use it (e.g. `FMT-DOUBLE-UNDERLINE`)
5. **Result Aggregation** (`result_builder.py`) — merges verdicts into a `DocumentValidationResponse`

`src/pipeline/orchestrator.py` drives the pipeline and is called by `src/services/validation_service.py`.

### Document Types and the Workbook Pipeline

`src/document_types/registry.py` defines the supported document types. Each has accepted formats, rule files, an options schema and a pipeline factory. `ValidationService` and the job service dispatch on the type; the PDF path above is unchanged.

Capital-event workbooks run through `src/pipeline/workbook/pipeline.py`:
1. Load formulas and cached values (`loader.py`).
2. Assign sheet roles (`roles.py`: heuristic, then LLM confirmation).
3. Keep only the sheets the user-selected event type needs (`selection.py`, `config/document_types/capital_event.yaml`).
4. Map each kept sheet's layout with the LLM (`skeleton.py`, `layout_mapper.py`) and validate it against the cells (`layout_validator.py`).
5. Extract typed data (`extract.py`).
6. Read the prior-event workbook if one was uploaded (`prior_file`, or the `first_event` option for the first event). Cross-event rules live in `checks/cross_event.py`. Linked sheets are scanned too (role `reference`): those referenced by the kept sheets, plus visible sheets linking to the Allocation or Summary. Support tabs the Allocation pulls from are tied out in `checks/support.py`.
7. Evaluate rules:
   - deterministic rules run in `checks/`
   - hybrid rules go to the LLM with a computed-facts block (`facts.py`)

Rules declare `evaluator`, `required_roles` and `event_types`.

### Key Source Directories

- `src/api/routers/` — FastAPI route handlers (validations, rules, export, feedback, health, auth)
- `src/pipeline/` — core processing stages
- `src/providers/` — Claude providers (`text/` and `vision/` subdirs, each with `base.py`, `claude.py`, `factory.py`); `router.py` picks each call's model and effort; `models.py` loads the model registry `config/models.yaml` (allowlist, capabilities, prices)
- `src/services/` — business logic layer; `validation_job_service.py` manages the in-memory threaded job queue
- `src/schemas/` — Pydantic request/response models
- `src/core/` — settings (`pydantic-settings` + `config/app.yaml`), Azure auth, logging, security middleware
- `frontend/src/` — React SPA with Microsoft Entra ID auth gate (`@azure/msal-react`)
- `rules/rules.json` — validation rule definitions (text and vision types); `rules/capital_event/` — capital-event workbook rule pack
- `src/document_types/` — document-type registry; `src/pipeline/workbook/` — workbook pipeline (loader, roles, layouts, extraction, checks, facts)
- `config/app.yaml` — PDF rendering DPI, vision concurrency/temperature, report toggles
- `config/text_analysis_system_prompt.md` / `config/vision_analysis_system_prompt.md` — LLM system prompts (loaded at runtime by the analyzers)
- `flag_analysis/` — standalone LLM-powered feedback audit tool
- `scripts/` — utilities: `import_rules_from_excel.py` (bulk rule import), `entra/sync_apps.py` (Entra app registration automation), `update_integration_expectations.py` (discovery helper for integration tests), `debug_double_underlines.py` (PDF line-geometry inspector for debugging double-underline detection)
- `tests/integration/` — integration test suite: `cases.yaml` (test case definitions), `conftest.py` (session-scoped pipeline fixture), `test_pipeline.py` (parametrized verdict assertions)
- `tests/fixtures/documents/` — PDF files used by integration tests. Synthetic test fixtures may be committed; do not commit real client documents (add those locally or via shared storage)
- `infra/` — Azure Bicep templates (Container Apps, ACR, Log Analytics)
- `docs/` — detailed documentation for pipeline, providers, auth, deployment, etc.

### Models and Effort

Every LLM call goes to Claude; OpenAI is no longer supported. Each call has a purpose (`text_rule`, `vision_rule`, `hybrid_rule`, `layout`, `roles`) whose default model and effort come from settings (`CLAUDE_TEXT_MODEL` = `claude-sonnet-5-5`, `CLAUDE_VISION_MODEL`, `TEXT_RULE_EFFORT`, `VISION_RULE_EFFORT`, `LAYOUT_MAPPING_EFFORT`, `ROLE_ASSIGNMENT_EFFORT`; empty effort = the model default). A rule can override either with its own `model` / `effort` fields, validated against `config/models.yaml` at load and re-applied from the server rule files (client-sent values are ignored). `LlmRouter` (`src/providers/router.py`) resolves them and builds one provider per model; `ValidationService(router=...)` threads it through every pipeline, and tests inject fakes via `provider=` / `text_provider=` (`FixedProviderRouter`). Rule assessments record `llm_model` / `llm_effort`; `llm_usage` rows carry `cost_usd`. See `docs/providers.md`.

### Async Job Queue

Validation runs are dispatched as background jobs via `validation_job_service.py`. Clients poll `GET /api/v1/validations/jobs/{job_id}` for status; cancellation is supported via `POST /api/v1/validations/jobs/{job_id}/cancel`. The queue is in-memory — no persistent storage; jobs are lost on restart.

### Ephemeral Storage

PDFs are written to `data/tmp/` during processing and deleted afterwards. Feedback is stored in-memory. There is no database or persistent file storage.

## Configuration

Copy `env.example` to `.env` and fill in:

- `AZURE_*` — Microsoft Entra ID tenant/client IDs for auth
- `ANTHROPIC_API_KEY` (also accepts `ANTHROPIC_AI_API_KEY` / `ANTROPIC_AI_API_KEY` aliases)
- `CLAUDE_TEXT_MODEL`, `CLAUDE_VISION_MODEL`, `*_EFFORT` — optional default model and effort (see `docs/providers.md`)

`config/app.yaml` controls PDF rendering (DPI defaults to 300), vision concurrency, and report toggles (e.g. `include_thumbnails`).

Rule prompts are ordered shared-content-first (document content, then layout metadata, then the rule) so they can reuse a cached prefix; `pipeline.prompt_cache` adds Claude cache markers and starts one call per shared prefix first. See "Prompt caching" in `docs/configuration.md`, and keep new prompt content that varies per rule after the shared part.

`rules/rules.json` rule objects carry: `id`, `name`, `analysis_type` (text|vision), `query`, `description`, `acceptance_criteria`, `severity` (major|minor|critical), and optional `group`/`bypassable` fields, plus optional `model`/`effort` overrides.
