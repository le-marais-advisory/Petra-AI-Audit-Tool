"""Model and effort routing: defaults, rule and variant overrides, what reaches the API, cache grouping.

No API key needed: providers get stubbed clients, analyzers and pipelines get recording fakes.
"""
from __future__ import annotations

import importlib
import json
import threading
from types import SimpleNamespace as NS

import pytest

from src.core.config import AppYaml, PipelineConfig, Settings
from src.pipeline.text_rule_analyzer import TextRuleAnalyzer
from src.pipeline.vision_rule_analyzer import VisionRuleAnalyzer
from src.providers.errors import claude_response_text, served_by_fallback
from src.providers.router import FixedProviderRouter, LlmRouter, LlmTarget, RoutingOverrides
from src.providers.text.claude import REFUSAL_FALLBACK_BETA, ClaudeTextAnalysisProvider
from src.providers.text.factory import build_text_provider
from src.providers.vision.claude import ClaudeVisionProvider
from src.providers.vision.factory import build_vision_provider
from src.services.rule_service import RuleService

RESULT_JSON = json.dumps({"rule_id": "R1", "rule_name": "n", "verdict": "pass", "summary": "", "reasoning": "",
                          "findings": [], "confidence": "high", "citations": []})
RULE = {"id": "R1", "name": "Rule one", "query": "q"}


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, ANTHROPIC_API_KEY="k", **overrides)


# -- router precedence ------------------------------------------------------------------------------

def test_settings_defaults_per_purpose():
    router = LlmRouter(_settings(CLAUDE_TEXT_MODEL="claude-sonnet-5-5", CLAUDE_VISION_MODEL="claude-opus-5-5",
                                 TEXT_RULE_EFFORT="medium", LAYOUT_MAPPING_EFFORT="low"))
    assert router.rule_target(RULE, "text_rule") == LlmTarget("claude-sonnet-5-5", "medium")
    assert router.rule_target(RULE, "hybrid_rule") == LlmTarget("claude-sonnet-5-5", "medium")
    assert router.rule_target(RULE, "vision_rule") == LlmTarget("claude-opus-5-5", None)
    assert router.stage_target("layout") == LlmTarget("claude-sonnet-5-5", "low")
    assert router.stage_target("roles") == LlmTarget("claude-sonnet-5-5", None)


def test_default_model_is_sonnet_5_5_at_its_own_effort():
    router = LlmRouter(Settings(_env_file=None))
    assert router.rule_target(RULE, "text_rule") == LlmTarget("claude-sonnet-5-5", None)
    assert router.stage_target("layout").effort == "medium"


def test_each_field_resolves_through_the_layers_independently():
    overrides = RoutingOverrides(
        defaults={"model": "claude-sonnet-5"},
        stages={"text_rule": {"effort": "low"}},
        rule_overrides={"R2": {"effort": "max"}},
    )
    router = LlmRouter(_settings(TEXT_RULE_EFFORT="high"), overrides=overrides)
    assert router.rule_target(RULE, "text_rule") == LlmTarget("claude-sonnet-5", "low")
    # the rule's own field beats the variant stage; the variant's rule override beats the rule
    rule = {"id": "R2", "model": "claude-opus-5-5", "effort": "medium"}
    assert router.rule_target(rule, "text_rule") == LlmTarget("claude-opus-5-5", "max")
    # variant defaults reach purposes it sets no stage for
    assert router.rule_target(RULE, "vision_rule") == LlmTarget("claude-sonnet-5", None)


def test_variant_can_ignore_the_rules_own_overrides():
    rule = {"id": "R1", "model": "claude-opus-5-5", "effort": "max"}
    router = LlmRouter(_settings(), overrides=RoutingOverrides(ignore_rule_overrides=True))
    assert router.rule_target(rule, "text_rule") == LlmTarget("claude-sonnet-5-5", None)


def test_effort_is_dropped_for_models_without_it():
    router = LlmRouter(_settings(TEXT_RULE_EFFORT="medium"))
    assert router.rule_target({"id": "R1", "model": "claude-haiku-4-5"}, "text_rule") == LlmTarget(
        "claude-haiku-4-5", None)


def test_prompt_cache_override():
    assert LlmRouter(_settings()).prompt_cache(True) is True
    assert LlmRouter(_settings(), overrides=RoutingOverrides(prompt_cache=False)).prompt_cache(True) is False


# -- providers are shaped by the model registry -------------------------------------------------------

def test_factory_drops_temperature_where_rejected_and_clamps_budgets():
    settings = _settings(CLAUDE_TEXT_TEMPERATURE=0.2, CLAUDE_STRUCTURED_MAX_TOKENS=100000)
    sonnet = build_text_provider(settings, model="claude-sonnet-5-5")
    haiku = build_text_provider(settings, model="claude-haiku-4-5")
    assert sonnet._temperature is None and sonnet._refusal_fallback is True
    assert haiku._temperature == 0.2 and haiku._refusal_fallback is False
    assert haiku._structured_max_tokens == 64000 and sonnet._structured_max_tokens == 100000
    vision = build_vision_provider(AppYaml(), _settings(CLAUDE_VISION_TEMPERATURE=0.3), model="claude-sonnet-5")
    assert vision._temperature is None and vision._refusal_fallback is False


def test_missing_key_is_a_value_error():
    with pytest.raises(ValueError, match="Anthropic API key"):
        LlmRouter(Settings(_env_file=None, ANTHROPIC_API_KEY=None)).text_provider("claude-sonnet-5-5")


def _response(text=RESULT_JSON, model="claude-sonnet-5-5", content=None):
    return NS(model=model, stop_reason="end_turn", content=content or [NS(type="text", text=text)],
              usage=NS(input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0))


def _client(sent: dict, response=None):
    def create(**kwargs):
        sent.update(kwargs)
        return response or _response()
    api = NS(create=create)
    return NS(messages=api, beta=NS(messages=api))


def test_effort_reaches_output_config_on_text_and_vision_rule_calls():
    text = ClaudeTextAnalysisProvider(api_key="k", model_id="claude-sonnet-5", temperature=None, max_tokens=100)
    vision = ClaudeVisionProvider(api_key="k", model_id="claude-sonnet-5", temperature=None, max_tokens=100,
                                  max_concurrent=2)
    page = {"page": 1, "image_url": "data:image/png;base64,AAAA"}
    for effort in ("low", None):
        sent: dict = {}
        text._client = _client(sent)
        text.evaluate_rule("PAGE", RULE, "SYSTEM", effort=effort)
        assert sent["output_config"].get("effort") == effort
        sent = {}
        vision._client = _client(sent)
        vision.evaluate_rule(page, RULE, "SYSTEM", effort=effort)
        assert sent["output_config"].get("effort") == effort


def test_refusal_fallback_uses_the_beta_and_notes_who_answered():
    text = ClaudeTextAnalysisProvider(api_key="k", model_id="claude-sonnet-5-5", temperature=None, max_tokens=100,
                                      refusal_fallback=True)
    sent: dict = {}
    fell_back = _response(model="claude-opus-4-8", content=[
        NS(type="text", text='{"partial'), NS(type="fallback"), NS(type="text", text=RESULT_JSON)])
    text._client = _client(sent, fell_back)
    result = text.evaluate_rule("PAGE", RULE, "SYSTEM")
    assert sent["betas"] == [REFUSAL_FALLBACK_BETA] and sent["fallbacks"] == "default"
    assert result["verdict"] == "pass" and result["served_by"] == "claude-opus-4-8"
    # without the flag the plain endpoint is used and nothing is noted
    plain = ClaudeTextAnalysisProvider(api_key="k", model_id="claude-sonnet-5", temperature=None, max_tokens=100)
    sent = {}
    plain._client = _client(sent)
    assert "served_by" not in plain.evaluate_rule("PAGE", RULE, "SYSTEM") and "fallbacks" not in sent


def test_only_the_answer_after_the_last_fallback_block_is_read():
    response = _response(content=[NS(type="text", text="declined part"), NS(type="fallback"),
                                  NS(type="text", text="answer")])
    assert claude_response_text(response, 100, "X") == "answer"
    assert served_by_fallback(_response()) is None


def test_cache_salt_changes_the_system_prompt_only_when_set():
    for salt, expected in (("abc", "[run abc]\nSYSTEM"), ("", "SYSTEM")):
        provider = ClaudeTextAnalysisProvider(api_key="k", model_id="claude-sonnet-5", temperature=None,
                                              max_tokens=100, cache_salt=salt)
        sent: dict = {}
        provider._client = _client(sent)
        provider.evaluate_rule("PAGE", RULE, "SYSTEM")
        assert sent["system"][0]["text"] == expected


# -- rule overrides ------------------------------------------------------------------------------------

def _rules_json(**fields) -> str:
    return json.dumps({"rules": [{"id": "R1", "name": "Rule", "query": "q", **fields}]})


def test_unknown_model_or_bad_effort_fails_at_load_with_the_rule_id(tmp_path):
    for fields, message in (({"model": "claude-nope"}, "not in config/models.yaml"),
                            ({"effort": "extreme"}, "unknown effort"),
                            ({"model": "claude-haiku-4-5", "effort": "low"}, "does not accept an effort")):
        path = tmp_path / "rules.json"
        path.write_text(_rules_json(**fields))
        with pytest.raises(ValueError, match=message) as exc:
            RuleService().load_rules(rules_json_path=str(path))
        assert "'R1'" in str(exc.value)


def test_client_supplied_overrides_are_replaced_by_the_server_rule_files(monkeypatch, tmp_path):
    server_file = tmp_path / "server.json"
    server_file.write_text(json.dumps({"rules": [
        {"id": "R1", "name": "Rule", "query": "q", "effort": "low"},
        {"id": "R2", "name": "Other", "query": "q"},
    ]}))
    monkeypatch.setattr("src.services.rule_service.DEFAULT_RULE_FILES", [str(server_file)])
    payload = json.dumps({"rules": [
        {"id": "R1", "name": "Rule", "query": "q", "model": "claude-opus-5-5", "effort": "max"},
        {"id": "R2", "name": "Other", "query": "q", "model": "claude-opus-5-5"},
    ]})
    rules = {r["id"]: r for r in RuleService().load_rules(rules_json_str=payload)}
    assert rules["R1"].get("model") is None and rules["R1"]["effort"] == "low"
    assert "model" not in rules["R2"] and "effort" not in rules["R2"]


def test_rule_schema_exposes_the_overrides():
    from src.schemas.rule import RuleSchema

    schema = RuleSchema(id="R1", name="n", query="q", model="claude-sonnet-5", effort="low")
    assert schema.model == "claude-sonnet-5" and schema.effort == "low"


# -- analyzers: per-target providers, effort and cache groups -------------------------------------------

class RecordingText:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self._lock = threading.Lock()

    def evaluate_rule(self, document_content, rule, system_prompt, rule_context="", cache_content=False,
                      shared_context="", cache_shared_context=False, effort=None):
        with self._lock:
            self.requests.append({"rule": rule["id"], "cache": cache_content, "effort": effort})
        return {"verdict": "pass", "summary": "", "reasoning": "", "findings": [], "citations": [],
                "confidence": "high"}


PAGES = [{"page": 1, "text": "balance sheet page", "tables": [], "page_type": ["balance_sheet"]}]


def _page_rule(rule_id, **fields):
    return {"id": rule_id, "name": rule_id, "analysis_type": "text", "scope": "page", "section": "All Statements",
            **fields}


def test_text_rules_on_another_target_neither_share_nor_count_toward_a_cache_group():
    provider = RecordingText()
    analyzer = TextRuleAnalyzer(app_config=AppYaml(pipeline=PipelineConfig(prompt_cache=True)),
                                settings=_settings(), provider=provider)
    rules = [_page_rule("A"), _page_rule("B"), _page_rule("C", effort="low")]
    result = analyzer.analyze(pages=PAGES, rules=rules)
    by_rule = {r["rule"]: r for r in provider.requests}
    assert by_rule["A"]["cache"] and by_rule["B"]["cache"]  # A and B share the page on one target
    assert not by_rule["C"]["cache"] and by_rule["C"]["effort"] == "low"  # C is alone on its target
    assessments = result["rule_results"]
    assert assessments["C"]["llm_effort"] == "low" and assessments["A"]["llm_effort"] is None
    assert assessments["A"]["llm_model"] == "claude-sonnet-5-5"


def test_text_rules_reach_one_provider_per_model():
    built: list[str] = []
    providers: dict[str, RecordingText] = {}

    class Router(LlmRouter):
        def text_provider(self, model):
            built.append(model)
            return providers.setdefault(model, RecordingText())

    analyzer = TextRuleAnalyzer(app_config=AppYaml(), settings=_settings(), router=Router(_settings()))
    analyzer.analyze(pages=PAGES, rules=[_page_rule("A"), _page_rule("B", model="claude-haiku-4-5")])
    assert sorted(built) == ["claude-haiku-4-5", "claude-sonnet-5-5"]
    assert [r["rule"] for r in providers["claude-haiku-4-5"].requests] == ["B"]


class RecordingVision:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self._lock = threading.Lock()

    def evaluate_rule(self, page_image, rule, system_prompt, cache_content=False, effort=None):
        with self._lock:
            self.requests.append({"rule": rule["id"], "cache": cache_content, "effort": effort})
        return {"verdict": "pass", "summary": "", "reasoning": "", "findings": [], "citations": [],
                "confidence": "high"}


def test_vision_rules_group_pages_per_target(tmp_path):
    provider = RecordingVision()
    analyzer = VisionRuleAnalyzer(app_config=AppYaml(pipeline=PipelineConfig(prompt_cache=True)),
                                  settings=_settings(), provider=provider)
    image = tmp_path / "page_1.png"
    image.write_bytes(b"png")
    analyzer._render_page_images = lambda pdf_path: [
        {"page": 1, "image_path": str(image), "image_url": "data:image/png;base64,AAAA"}]
    rules = [{"id": r, "name": r, "analysis_type": "vision", "scope": "page", "section": "All Statements", **f}
             for r, f in (("V1", {}), ("V2", {}), ("V3", {"effort": "low"}))]
    result = analyzer.analyze("doc.pdf", rules, page_types_by_number={1: ["balance_sheet"]})
    by_rule = {r["rule"]: r for r in provider.requests}
    assert by_rule["V1"]["cache"] and by_rule["V2"]["cache"] and not by_rule["V3"]["cache"]
    assert by_rule["V3"]["effort"] == "low"
    assert result["rule_results"]["V3"]["llm_effort"] == "low"


class RecordingHybrid(RecordingText):
    def evaluate_rule(self, document_content, rule, system_prompt, rule_context="", cache_content=False,
                      shared_context="", cache_shared_context=False, effort=None):
        super().evaluate_rule(document_content, rule, system_prompt, rule_context, cache_content, effort=effort)
        return {"rule_id": rule["id"], "rule_name": rule["name"], "verdict": "needs_review", "summary": "fake",
                "reasoning": "fake", "findings": [], "confidence": "low", "citations": []}


def test_hybrid_rule_on_another_target_leaves_its_sheet_set_group(capital_event_fixtures):
    layout = importlib.import_module("src.pipeline.workbook.layout")
    from src.pipeline.workbook.pipeline import WorkbookPipeline

    manifest = capital_event_fixtures.get("capital_call")
    provider = RecordingHybrid()
    rules = RuleService().load_rules(document_type="capital_event_workbook", event_type="capital_call")
    allocation_only = [r["id"] for r in rules
                       if r.get("evaluator") == "hybrid" and r.get("required_roles") == ["allocation"]]
    assert len(allocation_only) >= 3
    moved = allocation_only[0]
    router = FixedProviderRouter(text_provider=provider, settings=_settings(),
                                 overrides=RoutingOverrides(rule_overrides={moved: {"effort": "low"}}))
    pipeline = WorkbookPipeline(router=router, role_assigner=lambda m, i, p: manifest.sheet_roles,
                                layout_mapper=lambda m, s, r: layout.parse_layout(manifest.layouts[s]),
                                prompt_cache=True)
    result = pipeline.run(manifest.path, rules=rules, options={"event_type": "capital_call"},
                          source_filename=manifest.path.name)
    by_rule = {r["rule"]: r for r in provider.requests}
    assert by_rule[moved]["effort"] == "low" and not by_rule[moved]["cache"]
    assert all(by_rule[r]["cache"] for r in allocation_only[1:])
    assessments = {a["rule_id"]: a for a in result["analysis"]["rule_assessments"]}
    assert assessments[moved]["llm_effort"] == "low"
    deterministic = next(r["id"] for r in rules if r.get("evaluator") == "deterministic")
    assert assessments[deterministic].get("llm_model") is None


# -- layout cache --------------------------------------------------------------------------------------

def test_layout_cache_is_keyed_by_model_and_effort(capital_event_fixtures):
    from src.pipeline.workbook import layout_mapper
    from src.pipeline.workbook.loader import load_workbook_model

    manifest = capital_event_fixtures.get()
    model = load_workbook_model(manifest.path)
    golden = next(lay for lay in manifest.layouts.values() if lay["role"] == "allocation")

    class Provider:
        def __init__(self, name):
            self.model, self.calls = name, 0

        def complete_structured(self, system_prompt, user_content, json_schema, name="result", effort=None):
            self.calls += 1
            return json.loads(json.dumps(golden))

    layout_mapper._CACHE.clear()
    a, b = Provider("claude-sonnet-5-5"), Provider("claude-sonnet-5")
    for provider, effort in ((a, "medium"), (a, "medium"), (a, "low"), (b, "medium")):
        layout_mapper.map_sheet_layout(model, "Allocation", "allocation", provider, effort=effort)
    assert a.calls == 2 and b.calls == 1  # the repeat at (a, medium) was served from the cache
    layout_mapper._CACHE.clear()
