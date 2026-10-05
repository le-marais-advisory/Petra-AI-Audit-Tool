from __future__ import annotations

from src.core.config import AppYaml, Settings, get_settings, load_app_yaml
from src.document_types.registry import get_document_type, validate_options
from src.pipeline.orchestrator import ValidationPipeline
from src.providers.router import LlmRouter
from src.services.rule_service import RuleService


class ValidationService:
    def __init__(self, settings: Settings | None = None, app_config: AppYaml | None = None,
                 router: LlmRouter | None = None) -> None:
        """``router`` decides each LLM call's model and effort; the comparison tool passes one per variant."""
        self.settings = settings or get_settings()
        self.app_config = app_config or load_app_yaml()
        self.router = router or LlmRouter(self.settings, self.app_config)
        self.rule_service = RuleService()
        self.pipeline = ValidationPipeline(app_config=self.app_config, settings=self.settings, router=self.router)

    def load_rules_for(
        self,
        document_type: str,
        options: dict | None,
        rules_json_path: str | None = None,
        rules_json_str: str | None = None,
    ) -> list[dict]:
        return self.rule_service.load_rules(
            rules_json_path=rules_json_path,
            rules_json_str=rules_json_str,
            document_type=document_type,
            event_type=(options or {}).get("event_type"),
        )

    def validate_document(
        self,
        file_path: str | None = None,
        source_filename: str | None = None,
        rules_json_path: str | None = None,
        rules_json_str: str | None = None,
        document_type: str = "financial_statements",
        options: dict | None = None,
        pdf_path: str | None = None,
        prior_file_path: str | None = None,
        prior_source_filename: str | None = None,
    ) -> dict:
        file_path = file_path or pdf_path
        if file_path is None:
            raise ValueError("file_path is required")
        spec = get_document_type(document_type)
        options = validate_options(spec, options)
        selected_rules = self.load_rules_for(document_type, options, rules_json_path, rules_json_str)
        if document_type == "financial_statements":
            return self.pipeline.run(pdf_path=file_path, source_filename=source_filename, rules=selected_rules)
        pipeline = spec.pipeline_factory(router=self.router)
        return pipeline.run(file_path, rules=selected_rules, options=options, source_filename=source_filename,
                            prior_file_path=prior_file_path, prior_source_filename=prior_source_filename)
