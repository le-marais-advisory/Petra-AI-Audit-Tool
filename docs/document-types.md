# Document Types

Petra Vision validates more than one kind of document. The user chooses the **document type** for each run, because several types can share a file format (for example, two different `.xlsx` workbook types). The tool never infers the type from the file.

| Document type id | Formats | Pipeline | Rule pack |
|---|---|---|---|
| `financial_statements` | `.pdf` | `ValidationPipeline` (PDF extraction, text and vision rules) | `rules/rules.json`, `rules/multi_page_rules.json` |
| `capital_event_workbook` | `.xlsx`, `.xlsm` | `WorkbookPipeline` (see below) | `rules/capital_event/workbook_rules.json` |

The registry lives in `src/document_types/registry.py`. `GET /document-types` returns each type's label, accepted formats and **options schema**, which lists the inputs the user must give at upload time. The frontend builds its document-type selector and options form from that response.

## Capital-event workbooks

A capital-event workbook is the long-lived Excel model that fund accountants add to at every capital call, distribution or net event. Most sheets are not touched by a given event, so the user states the **event type** at upload (`options.event_type`) and the tool processes only the sheets that event needs.

### Event types and sheet roles

Event types are defined in `config/document_types/capital_event.yaml`. Each one lists its relevant **sheet roles**:

| Role | What it is |
|---|---|
| `allocation` | Per-investor allocation of the current event: commitments, commitment %, component columns, roll-forward |
| `itd` | Inception-to-date capital activity: one column block per event, plus the X classification band |
| `summary` | One-page event summary, including its check cell(s) |
| `merge` | Mail-merge / notice data with investor IDs, fund IDs and file names. There is one per vehicle, often named after the fund |
| `mgmt_fee` | Management-fee calculation tab |
| `investor_data` | Investor register exported from the document system |
| `holiday_calendar` | Bank holidays used to compute due dates |
| `other` | Everything else: notes, trackers, and legacy or hidden working sheets. These are never processed |

To add an event type, add an entry to the YAML file. To limit a rule to certain events, set its `event_types`.

### Prior-event workbook

The type declares a `prior_document`. With each upload the user also sends the workbook from the most recent prior event (`prior_file`). For the fund's first capital event they set the `first_event` option instead, and no prior workbook is needed. Sending both is rejected.

From the prior workbook, the pipeline reads only the Allocation and ITD sheets. Three deterministic **cross-event rules** compare it with the current workbook (`checks/cross_event.py`):

| Rule | Check |
|---|---|
| `CE-XEV-HISTORY-UNCHANGED` | Every ITD event block in the prior workbook reappears unchanged in the current one |
| `CE-XEV-ROLL-FORWARD` | The current Allocation's prior contributions equal the prior workbook's ITD contributions to date |
| `CE-XEV-PLUG-CONSISTENCY` | The rounding-plug pattern (single or spread, and which investors) matches the prior event |

These rules return `not_applicable` on a first event, and `needs_review` when no prior workbook was supplied (for example, in a CLI run without one).

### Referenced sheets

Unreferenced hidden or legacy sheets are skipped. A sheet that a formula on a processed sheet references (for example `'Portfolio Investment Tracker'!E12`) is a **reference sheet**, hidden or not. The workbook-wide scans (formula errors and placeholders) cover it too. Reference sheets appear in the result with `page_type: ["reference"]`.

### How a workbook run works

The pipeline is `src/pipeline/workbook/pipeline.py`.

1. **Load.** `loader.py` reads the workbook twice with openpyxl, once for the stored formulas and once for the cached values Excel saved, so nothing is recalculated. Macros are never executed. A workbook saved without cached values is flagged.
2. **Inventory and roles.** `inventory.py` summarises every sheet cheaply. `roles.py` proposes a role for each sheet from its name and headers, and one small LLM call confirms or corrects the proposal. Hidden sheets are treated as legacy and assigned `other`.
3. **Select.** `selection.py` keeps the sheets whose role the event type needs. The sheets those sheets reference are added as reference sheets.
4. **Map layouts.** For each selected sheet, `skeleton.py` builds a compact view: every text label, the formulas folded into relative per-column patterns with the exceptions listed (for example a `+0.02` rounding plug), and the merged ranges and hidden rows and columns. `layout_mapper.py` sends that view to the LLM, which returns a `SheetLayout` (`layout.py`) through strict structured output. The layout says where the header, driver and investor rows, component columns, event blocks and so on are.
5. **Validate layouts.** `layout_validator.py` checks each layout against the actual cells: header labels, subtotal `SUM` ranges against the investor ranges, band labels, block overlaps, and fee "Total" columns mistaken for billing periods. If it finds problems, the mapper re-prompts once with them. A layout that still fails makes only the rules that need that sheet return `needs_review`.
6. **Extract.** `extract.py` reads typed data (`AllocationData`, `ItdData`, ...) from the validated layouts.
7. **Evaluate.**
   - **Deterministic rules** (`evaluator: "deterministic"`) run in code (`checks/`). They cover arithmetic, ties, formulas, dates, formats and structure.
   - **Hybrid rules** (`evaluator: "hybrid"`) go to the LLM. Each gets a `COMPUTED FACTS` block from `facts.py` plus skeletons of only the sheets the rule reads (its `required_roles`).
8. **Result.** The response has the usual `DocumentValidationResponse` shape. Each processed sheet is one unit: `page` holds the sheet index, `label` the sheet name and `page_type` the role. Citations carry `sheet` and `cell`.

On the reference sample, a 17-sheet workbook, 7 sheets are processed. A run needs about 42k LLM input tokens, against about 209k for the raw workbook.

### Writing a deterministic check

Register a function in one of the `src/pipeline/workbook/checks/*.py` modules:

```python
@check("CE-MY-RULE", needs=("allocation",))
def my_rule(ctx: CheckContext, out: Outcome) -> str:
    alloc = ctx.data.allocation
    for inv in alloc.investors:
        if something_wrong(inv):
            out.fail(f"{inv.name}: ...", alloc.sheet, f"{column}{inv.row}")
    return "Summary shown when the rule passes."
```

- `out.fail(...)` and `out.review(...)` record findings with a sheet and cell citation. The worst finding sets the verdict.
- Raising `NotApplicable("reason")` returns `not_applicable`.
- If a role listed in `needs` has no validated layout, the rule returns `needs_review` automatically.

Then add the rule to `rules/capital_event/workbook_rules.json` with `"evaluator": "deterministic"` and no `query`. The check function is the rule's logic; `description` and `acceptance_criteria` describe it to reviewers. Finally, give it a seeded defect in `tests/fixtures/generate_capital_event_fixtures.py`, so `tests/test_workbook_checks.py` proves it passes on clean fixtures and fails on the defect.

### Tests and evals

- `tests/fixtures/generate_capital_event_fixtures.py` generates synthetic workbooks: every event type, three layout variants, and one seeded defect per rule. Each comes with its golden layouts and expected verdicts. Real client workbooks must never be committed.
- `pytest` runs the offline suites (`tests/test_workbook_*.py`, `tests/test_document_types.py`, `tests/test_capital_event_rule_pack.py`).
- `pytest tests/evals -m eval` runs the live-LLM evals: layout mapping against the golden anchors, role assignment, and hybrid-rule verdicts (`tests/evals/capital_event_cases.yaml`).
- `CAPITAL_EVENT_SAMPLE_PATH=... pytest tests/evals/test_real_sample_eval.py -m eval` runs a local check against a real workbook.

The rule semantics follow the fund accountants' calibration answers. These are summarised in the "Phase 1 outcome" section of the project plan and reflected in the rule descriptions in `rules/capital_event/workbook_rules.json`. The rules that need the fund's terms (`CE-ALLOC-FEE-TIERS`, `CE-ALLOC-COMPONENT-PARTICIPATION`) are parked in `rules/capital_event/deferred/` with `requires_documents: ["fund_terms"]`.

## Adding a document type

1. Add a `DocumentTypeSpec` to `src/document_types/registry.py`: the id, label, accepted formats, rule files, options schema and pipeline factory.
2. Write the rule pack with `document_types: ["<id>"]` on every rule.
3. Implement a pipeline with `run(file_path, rules=..., options=..., source_filename=..., on_progress=..., is_cancelled=...)` that returns a `DocumentValidationResponse`-compatible dict. Workbook types can reuse the `src/pipeline/workbook/` loader, skeleton and layout machinery.
