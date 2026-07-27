from __future__ import annotations

import logging
import time
from datetime import timedelta
from pathlib import Path

logger = logging.getLogger("petra.pipeline")

from src.core.config import AppYaml, Settings
from src.pipeline.pdf_extractor import PdfExtractor
from src.pipeline.text_rule_analyzer import TextRuleAnalyzer
from src.pipeline.vision_rule_analyzer import VisionRuleAnalyzer
from src.pipeline.result_builder import build_document_result


def _timestamp_id() -> str:
    return time.strftime("%Y%m%dT%H%M%S")


class ValidationPipeline:
    def __init__(self, app_config: AppYaml, settings: Settings) -> None:
        self.app_config = app_config
        self.settings = settings
        self.extractor = PdfExtractor(app_config=app_config)
        self.text_rule_analyzer = TextRuleAnalyzer(app_config=app_config, settings=settings)
        self.vision_rule_analyzer = VisionRuleAnalyzer(app_config=app_config, settings=settings)

    def build_rule_assessments(
        self,
        selected_rules: list[dict],
        text_rule_results: dict[str, dict] | None = None,
        vision_rule_results: dict[str, dict] | None = None,
        default_text_status: str = "skipped",
        default_vision_status: str = "pending",
    ) -> list[dict]:
        text_rule_results = text_rule_results or {}
        vision_rule_results = vision_rule_results or {}
        rule_assessments: list[dict] = []

        for rule in selected_rules:
            rule_id = rule.get("id", "")
            analysis_type = rule.get("analysis_type", "text")
            if analysis_type == "text":
                rule_assessments.append(
                    text_rule_results.get(
                        rule_id,
                        {
                            "rule_id": rule_id,
                            "rule_name": rule.get("name", rule_id),
                            "analysis_type": "text",
                            "execution_status": default_text_status,
                            "verdict": "needs_review",
                            "summary": (
                                "Waiting for page-level text analysis results."
                                if default_text_status == "running"
                                else "No text analysis result was produced."
                            ),
                            "reasoning": (
                                ""
                                if default_text_status == "running"
                                else "No text analysis result was produced."
                            ),
                            "findings": [],
                            "citations": [],
                            "matched_pages": [],
                            "notes": [],
                        },
                    )
                )
                continue

            rule_assessments.append(
                vision_rule_results.get(
                    rule_id,
                    {
                        "rule_id": rule_id,
                        "rule_name": rule.get("name", rule_id),
                        "analysis_type": "vision",
                        "execution_status": default_vision_status,
                        "verdict": "needs_review",
                        "summary": (
                            "Waiting for page-level vision analysis results."
                            if default_vision_status == "running"
                            else "Visual rule queued for image-based analysis."
                        ),
                        "reasoning": (
                            "The rule is marked as visual and is queued for image-based analysis."
                            if default_vision_status == "running"
                            else "The rule is marked as visual and will be evaluated after PDF pages are rendered."
                        ),
                        "findings": [],
                        "citations": [],
                        "matched_pages": [],
                        "notes": (
                            ["Vision analysis in progress."]
                            if default_vision_status == "running"
                            else ["Vision analysis queued."]
                        ),
                    },
                )
            )

        return rule_assessments

    def run(
        self,
        pdf_path: str,
        rules: list[dict] | None = None,
        source_filename: str | None = None,
    ) -> dict:
        t0 = time.perf_counter()
        doc_id = Path(pdf_path).stem + f"_{_timestamp_id()}"
        selected_rules = rules or []
        logger.info(
            "Pipeline start: file=%s rules=%d",
            source_filename or Path(pdf_path).name,
            len(selected_rules),
        )
        pages = self.extractor.extract(pdf_path=pdf_path)
        text_analysis_results = self.text_rule_analyzer.analyze(pages=pages, rules=selected_rules)
        text_rule_results = text_analysis_results.get("rule_results", {})
        text_page_results = text_analysis_results.get("page_results", [])
        page_types_by_number = {int(p.get("page", 0)): (p.get("page_type") or []) for p in pages}
        vision_analysis_results = self.vision_rule_analyzer.analyze(
            pdf_path=pdf_path,
            rules=selected_rules,
            page_types_by_number=page_types_by_number,
        )
        vision_rule_results = vision_analysis_results.get("rule_results", {})
        visual_page_results = vision_analysis_results.get("page_results", [])
        rule_assessments = self.build_rule_assessments(
            selected_rules=selected_rules,
            text_rule_results=text_rule_results,
            vision_rule_results=vision_rule_results,
        )
        elapsed_seconds = time.perf_counter() - t0
        logger.info(
            "Pipeline complete: file=%s pages=%d rules=%d elapsed=%s",
            source_filename or Path(pdf_path).name,
            len(pages),
            len(selected_rules),
            timedelta(seconds=elapsed_seconds),
        )
        return build_document_result(
            document_id=doc_id,
            pages=pages,
            source_filename=source_filename,
            selected_rules=selected_rules,
            rule_assessments=rule_assessments,
            text_page_results=text_page_results,
            visual_page_results=visual_page_results,
            elapsed_seconds=elapsed_seconds,
        )
