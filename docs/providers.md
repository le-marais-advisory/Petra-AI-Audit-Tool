# AI Models

Every LLM call goes to Anthropic's Claude. Each call has a model and an effort level. There is a default for each kind of call, and any rule can override both. To measure a change before making it, use the comparison tool ([Comparing models](model-comparison.md)).

## Which model and effort a call uses

Calls have one of five purposes:

| Purpose | Calls | Default model | Default effort |
|---|---|---|---|
| `text_rule` | PDF text rules (page and broad scope) | `CLAUDE_TEXT_MODEL` | `TEXT_RULE_EFFORT` |
| `vision_rule` | PDF vision rules | `CLAUDE_VISION_MODEL`, else `CLAUDE_TEXT_MODEL` | `VISION_RULE_EFFORT` |
| `hybrid_rule` | Workbook rules judged by the LLM | `CLAUDE_TEXT_MODEL` | `TEXT_RULE_EFFORT` |
| `layout` | Workbook layout mapping | `CLAUDE_TEXT_MODEL` | `LAYOUT_MAPPING_EFFORT` (`medium`) |
| `roles` | Workbook sheet-role confirmation | `CLAUDE_TEXT_MODEL` | `ROLE_ASSIGNMENT_EFFORT` |

`CLAUDE_TEXT_MODEL` defaults to `claude-sonnet-5-5`.

An empty effort means the model's own default, which is `high` on Sonnet 5.5. Effort controls how much the model thinks before answering. Thinking counts against `max_tokens` and is billed as output.

`src/providers/router.py` (`LlmRouter`) resolves the model and the effort separately. For each one, the first match wins:

1. a comparison variant's override for the rule;
2. the rule's own `model` / `effort` fields;
3. the comparison variant's setting for the purpose;
4. the comparison variant's defaults;
5. the settings default in the table above.

Steps 1, 3 and 4 only exist in the comparison tool. In production a call uses the rule's override if it has one, and the settings default otherwise.

## Per-rule overrides

Any rule in `rules/*.json` or `rules/capital_event/*.json` can set `model` and/or `effort`:

```json
{ "id": "GRAM-SPELL", "name": "...", "query": "...", "model": "claude-haiku-4-5" }
{ "id": "SOI-PERCENTAGE-TIE", "name": "...", "query": "...", "effort": "max" }
```

- The model must be listed in `config/models.yaml`.
- The effort must be one of `low`, `medium`, `high`, `xhigh` or `max`, and the model must accept it. Haiku 4.5 accepts no effort setting.
- A bad override fails when the rules are loaded, and the error names the rule.
- Overrides are taken from the server's rule files only. Rule objects round-trip through the browser, so any `model` or `effort` a client sends is replaced by the rule file's value for the same rule id.

Each rule assessment records the model and effort its calls used, in `llm_model` and `llm_effort`.

## Model registry (`config/models.yaml`)

The registry lists every model the pipeline may call. For each one it records:
- its prices per million tokens: input, output, cache read, and 5-minute cache write;
- whether it accepts an effort setting;
- whether it accepts a non-default temperature;
- whether it offers the server-side refusal fallback;
- its output-token ceiling.

The registry is used in three ways:
- **As the allowlist** for rule and variant overrides.
- **To shape each request.** Temperature is sent only where the model allows it; Sonnet 5 and 5.5 reject a non-default value with a 400. Effort is sent only where the model accepts it. Token budgets are clamped to the model's ceiling.
- **As the price list** for the cost figures (`cost_usd`) in the run's `llm_usage` and in the comparison report.

Update the prices when Anthropic's pricing page changes, and set `pricing_checked` to that date.

## Refusal fallback

Models flagged with `refusal_fallback` (Sonnet 5.5, Opus 5.5) are called through the beta endpoint, with `fallbacks: "default"` and the beta header `server-side-fallback-2026-07-01`.

If the model declines a request, the API re-runs it on a fallback model chosen by refusal category, inside the same request. The rule gets an answer instead of a needs-review.

When that happens:
- the rule's notes say which model answered;
- the usage meter prices the call at that model's rates.

## Settings

```env
ANTHROPIC_API_KEY=sk-ant-...           # also ANTHROPIC_AI_API_KEY / ANTROPIC_AI_API_KEY
CLAUDE_TEXT_MODEL=claude-sonnet-5-5
CLAUDE_VISION_MODEL=                   # falls back to CLAUDE_TEXT_MODEL
TEXT_RULE_EFFORT=                      # empty: model default
VISION_RULE_EFFORT=
ROLE_ASSIGNMENT_EFFORT=
LAYOUT_MAPPING_EFFORT=medium
CLAUDE_TEXT_MAX_TOKENS=24000           # text and hybrid rule calls
CLAUDE_STRUCTURED_MAX_TOKENS=64000     # role assignment and layout mapping (streamed)
CLAUDE_VISION_MAX_TOKENS=16000
CLAUDE_TEXT_TEMPERATURE=               # only sent to models that accept it
CLAUDE_VISION_TEMPERATURE=
```

The `max_tokens` values cover thinking as well as the answer. They are ceilings the model can't see, so headroom costs nothing.

If a budget runs out before the answer is written, the call fails with a `TruncatedResponseError` that names the setting to raise. In layout mapping, the mapper first retries one effort level lower.

## Provider interface

`src/providers/text/base.py` and `src/providers/vision/base.py` define the interfaces that the Claude providers implement (`claude.py`) and that tests fake:

```python
class TextAnalysisProvider(ABC):
    def evaluate_rule(self, document_content, rule, system_prompt, rule_context="", cache_content=False,
                      shared_context="", cache_shared_context=False, effort=None) -> dict: ...
    def complete_structured(self, system_prompt, user_content, json_schema, name="result", effort=None) -> dict: ...

class VisionProvider(ABC):
    def evaluate_rule(self, page_image, rule, system_prompt, cache_content=False, effort=None) -> dict: ...
```

- **One provider per model.** `build_text_provider(settings, model=...)` and `build_vision_provider(app_config, settings, model=...)` build a provider for one model. The router builds one on first use for each model a run needs.
- **Injecting fakes in tests.** Pass `provider=` to `TextRuleAnalyzer` or `VisionRuleAnalyzer`, or `text_provider=` to `WorkbookPipeline`. That fake receives every call.
- **Passing a router.** `ValidationService(router=...)` passes it through to every pipeline.

## Key files

- `config/models.yaml`: the model registry.
- `src/providers/models.py`: loads the registry and validates effort levels.
- `src/providers/router.py`: `LlmRouter`, `RoutingOverrides` and `FixedProviderRouter`.
- `src/providers/text/claude.py` and `src/providers/vision/claude.py`: the Claude providers.
- `src/providers/text/factory.py` and `src/providers/vision/factory.py`: build a provider for a model.
- `src/providers/errors.py`: `TruncatedResponseError`, reading an answer, and detecting a refusal fallback.
- `src/core/llm_usage.py`: per-run token and cost metering.
