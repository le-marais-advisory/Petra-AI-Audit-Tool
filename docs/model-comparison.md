# Comparing Models

Before changing a default model or effort level, or a rule's own override, measure the change on labelled documents. The comparison tool runs a suite of documents under several **variants**. It records every verdict, token count, cost and timing, and writes an interactive HTML report.

```bash
# what would run, plus a cost estimate from earlier comparisons (no API calls)
python -m src.evaluation.llm_compare run

# run it: every case x variant x repeat, then write results.json and report.html
python -m src.evaluation.llm_compare run --variant current --variant sonnet-5-5-medium --repeats 3 --yes
```

Live runs call the Claude API and cost money. Without `--yes` the tool only prints the plan and an estimate.

Useful filters:
- `--case <id>` and `--rule <id>` (both repeatable) narrow the run.
- `--repeats 3` is worth the cost when you compare effort levels, because a single run can't separate a real difference from run-to-run variance.

Output goes to `data/llm_compare/<timestamp>/`, which is git-ignored:
- `results.json` holds every run's verdicts, answers, per-call usage and layout scores. It is written after each run, so an interrupted comparison keeps the runs it finished.
- `report.html` is a self-contained page with no external assets. It can contain client data, so keep it local.

## Variants (`evals/llm_variants.yaml`)

A variant changes only what it names. Everything else falls through to the production defaults: the settings, and each rule's own `model` / `effort` fields.

```yaml
baseline: current            # agreement is measured against this one; always run
variants:
  current: {description: Production defaults}
  sonnet-5-5-medium:
    stages:                  # text_rule | vision_rule | hybrid_rule | layout | roles
      text_rule: {effort: medium}
      hybrid_rule: {effort: medium}
  haiku-formatting-rules:
    rule_overrides:          # one rule; beats the rule's own fields
      GRAM-SPELL: {model: claude-haiku-4-5}
  # defaults: {model: ..., effort: ...}   every call
  # ignore_rule_overrides: true           ignore the rule files' model/effort fields
  # prompt_cache: false                   no cache markers (measures what caching saves)
```

Variant models must be listed in `config/models.yaml`, which also supplies the prices. Overrides are checked when the file loads, using the same rules as the rule files (see [AI Models](providers.md)).

The committed variants are:
- `current`: production;
- `sonnet-5`: the previous default, to check the upgrade for regressions;
- `sonnet-5-5-medium` and `sonnet-5-5-low`: lower effort on rule calls and role assignment;
- `haiku-formatting-rules`: Haiku 4.5 on the formatting and spelling rules only.

## Suites (`evals/llm_suite.yaml`)

A suite lists the documents and their expected verdicts. The committed default contains labelled fixtures only:
- **The PDF integration cases** in `tests/integration/cases.yaml`. A check there can re-aggregate a rule over a page subset (`pages:`), as the integration tests do.
- **The synthetic workbook cases** in `tests/evals/capital_event_cases.yaml`. These are generated on demand. Each one carries its hybrid-rule expectations, plus the fixture generator's expected verdicts for the deterministic rules and its golden layouts. That means a layout-mapping or role-assignment change is scored too.

For real client documents, write a local suite under `temp/`, which is git-ignored and never committed:

```yaml
cases:
  - from: tests/integration/cases.yaml        # imports are allowed
    only: [multi_page_rules]
  - id: ftv-v-dist14
    document: temp/test-run-2026-10-03/FTV V, L.P. - Distribution #14 - Due 07 17, 2026 v6 ILPA.xlsx
    document_type: capital_event_workbook
    options: {event_type: net_event}
    prior_document: temp/test-run-2026-10-03/FTV V, L.P. - Capital Call #18 - Due 06 01, 2026 v6 - For Merge Master.xlsx
    expected: {CE-ITD-EVENT-BLOCK: pass, CE-TIE-SUMMARY: pass}
```

`rules: [...]` restricts a case to some rules; by default, every rule of the document type and event type runs.

To label a new document:
1. Run it once.
2. Print the majority verdicts as YAML:
   ```bash
   python -m src.evaluation.llm_compare bootstrap data/llm_compare/<run> --variant current
   ```
3. Review every verdict against the document, then paste the reviewed block into the suite.

## Reading the report

**Scorecard**, one row per variant. The best value in each column is highlighted, and clicking a header sorts by it.

| Column | What it measures |
|---|---|
| Accuracy | Share of expected verdicts matched, counted over every repeat. A failed run counts as wrong. |
| Missed fails | Expected fail, got pass. This is the costliest error for an audit tool, so treat any increase as disqualifying. |
| False fails | Got fail where the expected verdict was something else. |
| Review on pass/fail | Answered needs-review where a decided verdict was expected. |
| Stability | Rules with the same verdict on every repeat (needs `--repeats` of 2 or more). |
| Agreement | Rules whose majority verdict matches the baseline's. This also covers rules no case labels. |
| Errors | Rules whose calls errored. |
| Truncated | Answers cut off by the token budget. |
| Fallbacks | Calls a refusal fallback answered. |
| Cost / run | Dollars per validation, priced from `config/models.yaml`. |
| Tokens / run | Input, cached, output and thinking tokens. |
| Time / run | Wall time per validation. |
| Rule p50 / p95 | Per-rule call latency, median and 95th percentile. |
| Layout anchors | Anchor accuracy of the mapped workbook layouts against the golden ones (synthetic cases only). |

**Cost vs accuracy** puts each variant on one chart.

**Accuracy by rule kind and severity** splits accuracy into text, vision, hybrid and deterministic rules, and by severity. Deterministic rules don't call the LLM, but they depend on the layouts it maps.

**Rules** has one row per (case, rule). Each variant gets one coloured chip per repeat, marked ✓ or ✗ against the expected verdict, and the rule's cost.
- Filter to the rows where variants disagree, differ from the baseline, or are wrong somewhere.
- Click a row to read the variants' summaries, findings and reasoning side by side. That is how you judge quality beyond the verdict.

**Confusion matrices** cross expected verdicts against the verdicts each variant gave.

## Fair comparisons

- **Cold cache.** Each run starts with an empty prompt cache: the system prompt carries a per-run tag, and the in-process layout cache is cleared. Otherwise a repeat would read the cache an earlier run wrote, and look cheaper and faster than production, where every document is new. `--warm-cache` turns this off.
- **Interleaved order.** Repeats run in rounds (all variants, then all variants again), so a slow period of the API doesn't land on a single variant.
- **Adding a variant later.** Run only the new variant, plus the baseline, which always runs. Then merge the two comparisons into one report:
  ```bash
  python -m src.evaluation.llm_compare report data/llm_compare/<first> data/llm_compare/<second>
  ```

## Changing a default

A lower effort or a cheaper model is a candidate when it:
- keeps **missed fails** at the baseline's level;
- keeps accuracy within a point or two;
- keeps stability.

Make the change:
- for every rule: set `TEXT_RULE_EFFORT`, `VISION_RULE_EFFORT`, `LAYOUT_MAPPING_EFFORT`, `ROLE_ASSIGNMENT_EFFORT` or `CLAUDE_TEXT_MODEL`, in the deployment's environment as well;
- for one rule: set its `model` / `effort` fields in the rule file.

Then re-run the comparison with `current`, which now includes the change.
