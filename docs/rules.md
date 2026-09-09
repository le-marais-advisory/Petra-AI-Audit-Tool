# Rule System

Petra Vision validates documents against configurable rules defined in JSON. Rules are the core of the audit process — each rule describes a specific check to perform on the document.

## Rule Storage

Rules are stored in `rules/rules.json`. The file contains a single object with a `rules` array:

```json
{
  "rules": [
    { "id": "FMT-HEADINGS", "name": "Heading Alignment and Integrity", ... },
    { "id": "RND-GLOBAL", "name": "Whole-Dollar Values Check", ... }
  ]
}
```

## Rule Types

### Text Rules (`analysis_type: "text"`)

Text rules analyze extracted text and table data from each page. They are processed by the `TextRuleAnalyzer` using an LLM (OpenAI or Claude) with the extracted page content as input.

Examples:
- **FMT-HEADINGS** - Heading alignment and integrity
- **RND-GLOBAL** - Whole-dollar values (no cents)
- **NUM-REFOOT** - Column total verification
- **GRAM-SPELL** - Grammar and spelling

### Vision Rules (`analysis_type: "vision"`)

Vision rules analyze rendered page images. Each PDF page is rendered as a high-resolution image and sent to an LLM with vision capabilities.

Examples:
- **FMT-VISUAL-INTEGRITY** - Visual layout integrity (alignment, completeness)

## Rule Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Unique identifier (e.g., `"FMT-HEADINGS"`) |
| `name` | string | Yes | Human-readable name |
| `analysis_type` | `"text"` \| `"vision"` | Yes | Type of analysis to perform |
| `query` | string | Yes | What to look for in the document |
| `description` | string | Yes | Detailed description of the check |
| `acceptance_criteria` | string | Yes | What constitutes a pass |
| `severity` | string | Yes | `"critical"`, `"major"`, or `"minor"` |
| `group` | string | No | Rule group for categorization (e.g., `"numerical"`, `"dates"`) |
| `section` | string | No | Target document section (e.g., `"Balance Sheet"`, `"All"`) |
| `bypassable` | boolean | No | Whether the rule can be bypassed by the user |
| `tolerance` | number \| null | No | Numeric tolerance for math checks |
| `check_method` | string \| null | No | Analysis method hint (e.g., `"text_lint"`, `"visual_or_hint"`) |
| `steps` | string | No | Step-by-step analysis instructions |
| `pass_criteria` | string | No | Explicit pass conditions |
| `fail_criteria` | string | No | Explicit fail conditions |
| `action_if_fail` | string | No | What happens on failure (e.g., `"REJECTED."`) |
| `rationale` | string | No | Why this rule exists |

## Verdicts

Each rule assessment produces one of these verdicts:

| Verdict | Meaning |
|---------|---------|
| `pass` | The document meets the rule's acceptance criteria |
| `fail` | The document violates the rule |
| `needs_review` | The analysis is inconclusive and requires human review |
| `not_applicable` | The rule does not apply to this page/document |

## Severity Levels

| Severity | Impact |
|----------|--------|
| `critical` | Must be resolved. Typically triggers rejection. |
| `major` | Should be resolved. Affects document quality. |
| `minor` | Nice to fix. Does not affect correctness. |

## Rule Groups

Rules can be organized into groups for filtering in the UI:

| Group | Description |
|-------|-------------|
| `numerical` | Math verification (totals, cross-footing) |
| `dates` | Date validity and ordering |
| `rounding_signage_formatting` | Number formatting, signage, visual formatting |
| `spelling_grammar` | Text quality checks |
| `cover_page` | Cover page-specific checks |
| `balance_sheet` | Balance sheet-specific checks |
| `schedule_of_investments` | SOI-specific checks |
| `statement_of_operations` | Operations statement checks |
| `statement_of_cash_flows` | Cash flow statement checks |

## Adding a New Rule

1. Open `rules/rules.json`
2. Add a new object to the `rules` array:

```json
{
  "id": "MY-NEW-RULE",
  "name": "My Custom Check",
  "analysis_type": "text",
  "query": "What to look for in extracted text...",
  "description": "Detailed description of what this rule checks...",
  "acceptance_criteria": "Conditions for a pass verdict...",
  "severity": "major",
  "group": "my_group",
  "bypassable": false
}
```

3. Restart the backend (or the frontend will fetch the updated rules via `GET /rules`)
4. The new rule appears in the rules sidebar for selection

## Bypass Mechanism

Rules with `bypassable: true` can be toggled to bypass mode in the frontend UI. When bypassed, the rule still runs but its result is marked with `bypass: true`, indicating the finding was acknowledged but does not block the audit.

## Key Files

- `rules/rules.json` - Main rule definitions
- `src/services/rule_service.py` - Rule loading and filtering
- `src/schemas/rule.py` - Rule Pydantic models
- `src/api/routers/rules.py` - Rules API endpoint
