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
| `merge` | Mail-merge / notice data with investor IDs, fund IDs and file names. There is one per vehicle, often named after the fund, sometimes without the word "Merge", and clients may hide the tabs once the notices are generated; a hidden Merge tab is still processed |
| `mgmt_fee` | Management-fee calculation tab. Processed when present; a call does not always carry a fee |
| `investor_data` | Investor register exported from the document system |
| `holiday_calendar` | Bank holidays used to compute due dates |
| `other` | Everything else: notes, trackers, and legacy or hidden working sheets. These are never processed |

The fund accountants confirmed the three event types: capital call, distribution and net event. To add an event type, add an entry to the YAML file. To limit a rule to certain events, set its `event_types`.

### Vehicles and look-through blocks

An Allocation sheet may hold several vehicle blocks (a main fund, a parallel fund, an executive fund), each with its own driver row, and a grand-total row that adds the vehicles' total rows. The fund-level amount of a component is then the sum of the vehicles' drivers. A block whose driver row re-allocates the other blocks' rows (for example the general partner entity's own partners, whose driver is `=Q61+Q91+Q130`, the GP's share of each fund) is a **look-through** block: the extractor marks it non-additive from the formulas and the fund-level ties, the Summary and net-event facts leave it out.

Investors are matched across sheets and workbooks by **(vehicle, name)** (`src/pipeline/workbook/keys.py`): one LP can sit in two vehicles, and the GP entity usually sits in every block. Vehicle blocks mapped separately for each sheet are aligned by position, confirmed by the overlap of their investor names.

### Recycling funds

A fund that recycles distributions calls more than the commitment over its life. Its roll-forward then models recallable distributions, and its ITD "Recallable Distributions" column may be derived by formula (the room left under the LPA cap) rather than accumulated from the marked event columns. The extractor classifies each cumulative column as `accumulator`, `derived` or `value`; derived columns make `CE-ITD-CUMULATIVE` and `CE-XEV-ITD-ROLL-FORWARD` return `needs_review` with one message instead of failing every investor, and `CE-RF-FOOTING` reviews rather than fails over-contributed LPs when recallables are modelled.

### Prior-event workbook

The type declares a `prior_document`. With each upload the user also sends the workbook from the most recent prior event (`prior_file`). For the fund's first capital event they set the `first_event` option instead, and no prior workbook is needed. Sending both is rejected.

This mirrors the QC team's manual review of both workbooks. From the prior workbook, the pipeline reads only the Allocation and ITD sheets. Four deterministic **cross-event rules** compare it with the current workbook (`checks/cross_event.py`):

| Rule | Check |
|---|---|
| `CE-XEV-HISTORY-UNCHANGED` | Every ITD event block in the prior workbook reappears unchanged in the current one |
| `CE-XEV-ROLL-FORWARD` | The current Allocation's prior contributions equal the prior workbook's ITD contributions to date |
| `CE-XEV-ITD-ROLL-FORWARD` | For each investor and ITD category, current ITD balance = the prior workbook's balance + the current event's amount (per the current block's X marks). For example, 150k ITD distributions before and a 25k distribution now must give 175k; 200k means 25k is double counted. Event blocks that are not in the prior workbook (besides the current one) also fail: the prior workbook isn't the most recent, or an event was entered twice |
| `CE-XEV-PLUG-CONSISTENCY` | The rounding-plug pattern (single or spread, and which investors) matches the prior event |

Investors are compared by vehicle and name, so an LP that sits in two vehicles is compared block by block.

These rules return `not_applicable` on a first event, and `needs_review` when no prior workbook was supplied (for example, in a CLI run without one).

### Linked sheets and support tabs

Unreferenced hidden or legacy sheets are skipped. These sheets are **linked sheets** (`page_type: ["reference"]` in the result):
- any sheet that a formula on a processed sheet references (for example `'Portfolio Investment Tracker'!E12`), hidden or not
- any visible sheet whose formulas reference the Allocation or Summary

The workbook-wide scans (formula errors and placeholders) cover linked sheets too. Hidden sheets that only read the Allocation are allocation breakouts saved from earlier events, so they are skipped, as the fund accountants confirmed.

Support tabs, such as a fee calculation, a distribution waterfall or an expense breakout, usually feed Allocation columns through per-investor lookups (`SUMIFS`, `SUMIF`, `INDEX`/`MATCH`, `VLOOKUP`). `CE-TIE-SUPPORT-TABS` (`checks/support.py`) finds each Allocation column fed this way and re-evaluates its lookup against the support tab's cached values:
- **Investor by investor:** a hardcoded or mislinked cell is caught.
- **In total:** an amount on the support tab that no Allocation investor pulls is caught.

The ITD, Summary and Merge tabs have their own tie-out rules, so this rule does not cover them:
- `CE-TIE-ITD-ALLOCATION` pairs the current ITD block's columns with the Allocation components from the ITD formulas, so one ITD "Investment" column may stand for several Allocation investment columns (`=SUM(Allocation!Q7:T7)`).
- `CE-TIE-MGMT-FEE` follows each vehicle's fee formulas: an event may bill several quarters at once from several fee tabs (header `Q3 2025 - Q3 2026 Mgmt Fees`, five `SUMIF` terms), and each LP's fee must equal what those columns hold for that LP.
- `CE-TIE-MERGE` ties every Merge tab to its vehicle: all active Allocation components must be pulled into the tab (a stale tab keeps the prior event's columns), the per-investor call, distribution and cash-due amounts must equal the Allocation's, the total row must refoot and the tab's own check cells must be zero.

### How a workbook run works

The pipeline is `src/pipeline/workbook/pipeline.py`.

1. **Load.** `loader.py` reads the workbook twice with openpyxl, once for the stored formulas and once for the cached values Excel saved, so nothing is recalculated. Macros are never executed. A workbook saved without cached values is flagged.
2. **Inventory and roles.** `inventory.py` summarises every sheet cheaply, including how many of its formulas read other sheets. `roles.py` proposes a role for each sheet from its name and headers, and one small LLM call confirms or corrects the proposal. Hidden sheets are treated as legacy and assigned `other`, except a hidden Merge tab, which keeps the `merge` role.
3. **Select.** `selection.py` keeps the sheets whose role the event type needs. The sheets those sheets reference are added as reference sheets.
4. **Map layouts.** For each selected sheet, `skeleton.py` builds a compact view: every text label, the formulas folded into relative per-column patterns with the exceptions listed (for example a `+0.02` rounding plug), and the merged ranges and hidden rows and columns. `layout_mapper.py` sends that view to the LLM, which returns a `SheetLayout` (`layout.py`) through strict structured output. The layout says where the header, driver and investor rows, component columns, event blocks and so on are.
5. **Validate layouts.** `layout_validator.py` checks each layout against the actual cells: header labels, subtotal `SUM` ranges against the investor ranges, band labels, block overlaps, and fee "Total" columns mistaken for billing periods. If it finds problems, the mapper re-prompts once with them. A layout that still fails makes only the rules that need that sheet return `needs_review`.
6. **Extract.** `extract.py` reads typed data (`AllocationData`, `ItdData`, ...) from the validated layouts.
7. **Evaluate.**
   - **Deterministic rules** (`evaluator: "deterministic"`) run in code (`checks/`). They cover arithmetic, ties, formulas, dates, formats and structure.
   - **Hybrid rules** (`evaluator: "hybrid"`) go to the LLM. Each gets skeletons of only the sheets the rule reads (its `required_roles`), then a `COMPUTED FACTS` block from `facts.py`, then the rule. Rules with the same `required_roles` send identical skeletons, so with `pipeline.prompt_cache` on the skeletons are cached and one rule per sheet set runs before the others.
8. **Result.** The response has the usual `DocumentValidationResponse` shape. Each processed sheet is one unit: `page` holds the sheet index, `label` the sheet name and `page_type` the role. Citations carry `sheet` and `cell`. A deterministic rule keeps up to ten itemised `findings`; its `summary` states the count and the first one, and the PDF export lists them all.

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

- `tests/fixtures/generate_capital_event_fixtures.py` generates synthetic workbooks: every event type, four layout variants (including `multi_vehicle`: three vehicles plus a GP look-through block, an investor present in two vehicles, combined ITD labels, a multi-quarter fee header over two fee tabs, a derived recallable column and hidden Merge tabs), and one seeded defect per rule. Each comes with its golden layouts and expected verdicts. Real client workbooks must never be committed.
- `pytest` runs the offline suites (`tests/test_workbook_*.py`, `tests/test_document_types.py`, `tests/test_capital_event_rule_pack.py`).
- `pytest tests/evals -m eval` runs the live-LLM evals: layout mapping against the golden anchors, role assignment, and hybrid-rule verdicts (`tests/evals/capital_event_cases.yaml`).
- `CAPITAL_EVENT_SAMPLE_PATH=... pytest tests/evals/test_real_sample_eval.py -m eval` runs a local check against a real workbook; `CAPITAL_EVENT_NET_SAMPLE_PATH=... CAPITAL_EVENT_NET_PRIOR_PATH=...` does the same for a net-event workbook with its prior workbook (the multi-vehicle, recycling reference pair).

The rule semantics follow the fund accountants' calibration answers. These are summarised in the "Phase 1 outcome" section of the project plan and reflected in the rule descriptions in `rules/capital_event/workbook_rules.json`. The rules that need the fund's terms (`CE-ALLOC-FEE-TIERS`, `CE-ALLOC-COMPONENT-PARTICIPATION`) are parked in `rules/capital_event/deferred/` with `requires_documents: ["fund_terms"]`.

## Adding a document type

1. Add a `DocumentTypeSpec` to `src/document_types/registry.py`: the id, label, accepted formats, rule files, options schema and pipeline factory.
2. Write the rule pack with `document_types: ["<id>"]` on every rule.
3. Implement a pipeline with `run(file_path, rules=..., options=..., source_filename=..., on_progress=..., is_cancelled=...)` that returns a `DocumentValidationResponse`-compatible dict. Workbook types can reuse the `src/pipeline/workbook/` loader, skeleton and layout machinery.
