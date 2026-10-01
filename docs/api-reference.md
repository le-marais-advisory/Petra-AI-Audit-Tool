# API Reference

All endpoints are prefixed with `/api/v1` (configurable via `API_PREFIX`).

When authentication is enabled, all endpoints except `/health` require a valid Azure AD bearer token in the `Authorization` header.

## Validations

### POST /validations

Synchronous document validation. Blocks until analysis is complete.

**Request:** `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | file | Yes | Document to validate (`pdf` is accepted as a deprecated alias) |
| `document_type` | string | No | Document type id from `GET /document-types` (default `financial_statements`) |
| `options_json` | string | No | JSON object of options for the document type, e.g. `{"event_type": "capital_call"}` (required for `capital_event_workbook`) |
| `prior_file` | file | Conditional | Workbook from the most recent prior event. Required for `capital_event_workbook` unless `options_json` sets `"first_event": true` |
| `rules_json` | string | No | JSON string of selected rules. If omitted, the document type's rule pack is used (filtered by event type). |

**Response:** `200 OK` - `DocumentValidationResponse`

```json
{
  "document_id": "upload_20240115T143022",
  "document_type": "financial_statements",
  "options": {},
  "page_count": 5,
  "source_filename": "report.pdf",
  "analysis": {
    "overview": [{ "label": "Total Rules", "value": "8" }],
    "selected_rule_count": 8,
    "text_rule_count": 6,
    "vision_rule_count": 2,
    "rule_assessments": [...],
    "text_page_results": [...],
    "visual_page_results": [...],
    "page_observations": [...]
  },
  "pages": [
    {
      "page": 1,
      "text": "Extracted page text...",
      "tables": [{ "index": 1, "rows": [["Header1", "Header2"], ["val1", "val2"]] }],
      "char_count": 1523
    }
  ]
}
```

**Error Responses:**

| Status | Condition |
|--------|-----------|
| 400 | Unknown `document_type` |
| 413 | File exceeds `MAX_UPLOAD_SIZE_MB` (default 50 MB) |
| 415 | The file's format (detected from its bytes) is not accepted by the document type, e.g. a `.xls` or a PDF sent as a workbook. Also returned when `prior_file` is not an accepted format |
| 422 | `options_json` is invalid or misses a required option (e.g. `event_type`), or no file was uploaded. Also returned when `prior_file` is missing (and `first_event` is not set), is sent together with `first_event`, or is sent for a type with no prior document |

### POST /validations/jobs

Create an asynchronous validation job. Returns immediately with a job ID for polling.

**Request:** Same as `POST /validations`

**Response:** `200 OK` - `ValidationJobResponse`

```json
{
  "job_id": "abc123",
  "status": "running",
  "message": "Validation job started.",
  "progress_current": 0,
  "progress_total": 10
}
```

### GET /validations/jobs/{job_id}

Poll the status of an async validation job.

**Response:** `200 OK` - `ValidationJobResponse`

When the job is complete, the `result` field contains the full `DocumentValidationResponse`:

```json
{
  "job_id": "abc123",
  "status": "completed",
  "message": "Validation complete.",
  "progress_current": 10,
  "progress_total": 10,
  "error": null,
  "result": { ... }
}
```

**Job Statuses:** `pending`, `running`, `completed`, `failed`, `cancelled`

**Error Response:** `404` if `job_id` not found.

### POST /validations/jobs/{job_id}/cancel

Cancel a running validation job.

**Response:** `200 OK` - `ValidationJobResponse` with updated status.

**Error Response:** `404` if `job_id` not found.

## Rules

### GET /rules

List all available validation rules.

**Query Parameters:**

| Param | Type | Description |
|-------|------|-------------|
| `rules_path` | string | Optional path to a custom rules JSON file |
| `document_type` | string | Rule pack to list (default `financial_statements`) |
| `event_type` | string | Capital-event workbooks: only rules that apply to this event |

**Response:** `200 OK`

```json
{
  "rules": [
    {
      "id": "FMT-HEADINGS",
      "name": "Heading Alignment and Integrity",
      "analysis_type": "text",
      "query": "Title lines and subtitle lines...",
      "description": "Determine whether title/subtitle lines...",
      "acceptance_criteria": "Title lines should be complete...",
      "severity": "major",
      "group": null,
      "bypassable": false
    }
  ]
}
```

## Document Types

### GET /document-types

List the supported document types. The user picks one before uploading.

**Response:** `200 OK`

```json
{
  "document_types": [
    {
      "id": "capital_event_workbook",
      "label": "Capital Event Workbook",
      "description": "Excel roll-forward model for a capital call, distribution or net event.",
      "accepted_formats": ["xlsx", "xlsm"],
      "options_schema": {
        "type": "object",
        "properties": {
          "event_type": {"type": "string", "enum": ["capital_call", "distribution", "net_event"]},
          "first_event": {"type": "boolean", "title": "This is the fund's first capital event (no prior workbook)"}
        },
        "required": ["event_type"]
      },
      "prior_document": {
        "label": "Prior event workbook",
        "description": "The workbook of the most recent prior capital event, used to check that history and the roll-forward carried over and that plugs are allocated consistently.",
        "accepted_formats": ["xlsx", "xlsm"],
        "waived_by_option": "first_event"
      }
    }
  ]
}
```

For workbook results, each entry in `pages` is a processed sheet: `page` is the sheet index, `label` the sheet name and `page_type` the sheet role. Citations carry `sheet` and `cell`.

## Export

### POST /export

Generate a PDF audit report from analysis results.

**Request:** `application/json` - `ExportPdfRequest`

The request body contains the full analysis result (the same structure as `DocumentValidationResponse.analysis`).

**Response:** `200 OK` - Streaming PDF file download (`application/pdf`)

## Feedback

### POST /feedbacks

Submit user feedback on a validation result.

**Request:** `application/json`

```json
{
  "document_id": "upload_20240115T143022",
  "rule_id": "FMT-HEADINGS",
  "feedback_type": "incorrect",
  "message": "This heading was flagged incorrectly."
}
```

**Response:** `200 OK`

```json
{
  "success": true
}
```

## Health

### GET /health

Health check endpoint. No authentication required.

**Response:** `200 OK`

```json
{
  "status": "healthy"
}
```

## Data Models

### DocumentValidationResponse

| Field | Type | Description |
|-------|------|-------------|
| `document_id` | string | Unique identifier for the validation run |
| `page_count` | integer | Total pages in the PDF |
| `source_filename` | string | Original uploaded filename |
| `analysis` | DocumentAnalysisSchema | Full analysis results |
| `pages` | PageExtractionSchema[] | Extracted text and tables per page |

### RuleAssessmentSchema

| Field | Type | Description |
|-------|------|-------------|
| `rule_id` | string | Rule identifier (e.g., "FMT-HEADINGS") |
| `rule_name` | string | Human-readable rule name |
| `analysis_type` | "text" \| "vision" | Type of analysis performed |
| `execution_status` | string | "completed", "running", "skipped" |
| `verdict` | string | "pass", "fail", "needs_review", "not_applicable" |
| `summary` | string | Brief description of the assessment |
| `reasoning` | string | Detailed reasoning behind the verdict |
| `findings` | string[] | List of specific findings |
| `citations` | AnalysisCitationSchema[] | Page references with evidence |
| `matched_pages` | integer[] | Pages where the rule was evaluated |
| `notes` | string[] | Additional notes |
| `group` | string \| null | Rule group (e.g., "numerical") |
| `bypassable` | boolean | Whether the rule can be bypassed |
| `bypass` | boolean | Whether the rule was bypassed |

### ValidationJobResponse

| Field | Type | Description |
|-------|------|-------------|
| `job_id` | string | Unique job identifier |
| `status` | string | Job status |
| `message` | string | Human-readable status message |
| `progress_current` | integer | Current progress count |
| `progress_total` | integer | Total items to process |
| `error` | string \| null | Error message if failed |
| `result` | DocumentValidationResponse \| null | Full result when completed |
