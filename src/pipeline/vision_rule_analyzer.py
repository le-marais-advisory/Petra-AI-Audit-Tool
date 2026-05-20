from __future__ import annotations

import base64
import logging
import mimetypes
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path

from src.core.config import AppYaml, Settings
from src.core.prompting import load_prompt
from src.pipeline.page_classifier import rule_applies_to_page
from src.pipeline.pdf_renderer import PdfRenderer
from src.providers.vision.factory import build_vision_provider

logger = logging.getLogger("petra.pipeline.vision")


VISION_PROMPT_PATH = "config/vision_analysis_system_prompt.md"


def _build_skipped_result(rule: dict, message: str, execution_status: str = "skipped") -> dict:
    return {
        "rule_id": rule.get("id", ""),
        "rule_name": rule.get("name", rule.get("id", "")),
        "analysis_type": "vision",
        "execution_status": execution_status,
        "verdict": "needs_review",
        "summary": message,
        "reasoning": message,
        "findings": [],
        "citations": [],
        "matched_pages": [],
        "notes": [message],
    }


def _build_not_applicable_rule_result(rule: dict, reason: str) -> dict:
    return {
        "rule_id": rule.get("id", ""),
        "rule_name": rule.get("name", rule.get("id", "")),
        "analysis_type": "vision",
        "execution_status": "not_applicable",
        "verdict": "not_applicable",
        "summary": reason,
        "reasoning": reason,
        "findings": [],
        "citations": [],
        "matched_pages": [],
        "notes": [reason],
    }


def _image_to_data_url(image_path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(str(image_path))
    if not mime_type:
        mime_type = "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


class VisionRuleAnalyzer:
    def __init__(self, app_config: AppYaml, settings: Settings) -> None:
        self.app_config = app_config
        self.settings = settings
        self.renderer = PdfRenderer()
        self.system_prompt = load_prompt(VISION_PROMPT_PATH)

    def estimate_step_count(self, page_count: int, rules: list[dict]) -> int:
        vision_rules = [rule for rule in rules if rule.get("analysis_type", "text") == "vision"]
        if not vision_rules:
            return 0
        return len(vision_rules) * max(1, page_count)

    def _aggregate_rule_results(self, rule: dict, page_results: list[dict]) -> dict:
        completed_results = [item for item in page_results if item.get("execution_status") == "completed"]
        if any(item.get("verdict") == "fail" for item in completed_results):
            verdict = "fail"
        elif completed_results and all(item.get("verdict") in {"pass", "not_applicable"} for item in completed_results):
            verdict = "pass"
        else:
            verdict = "needs_review"

        findings: list[str] = []
        citations: list[dict] = []
        notes: list[str] = []
        summaries: list[str] = []
        matched_pages = sorted(
            {
                int(citation.get("page", 0))
                for item in completed_results
                for citation in item.get("citations", [])
                if int(citation.get("page", 0)) > 0
            }
            | {
                int(item.get("page", 0))
                for item in completed_results
                if int(item.get("page", 0)) > 0 and item.get("verdict") in {"pass", "fail", "needs_review", "not_applicable"}
            }
        )

        for item in page_results:
            if item.get("summary"):
                summaries.append(item["summary"])
            findings.extend(item.get("findings", [])[:2])
            citations.extend(item.get("citations", [])[:2])
            notes.extend(item.get("notes", [])[:1])

        return {
            "rule_id": rule.get("id", ""),
            "rule_name": rule.get("name", rule.get("id", "")),
            "analysis_type": "vision",
            "execution_status": "completed" if completed_results else "error",
            "verdict": verdict,
            "summary": " | ".join(summaries[:6]) or "No page-level vision analysis result was produced.",
            "reasoning": "Aggregated from page-level vision analysis results.",
            "findings": findings[:4],
            "citations": citations[:4],
            "matched_pages": matched_pages,
            "notes": notes[:4],
        }

    def _render_page_images(self, pdf_path: str) -> list[dict]:
        workdir = Path(self.settings.LOCAL_WORKDIR)
        workdir.mkdir(parents=True, exist_ok=True)
        render_dir = Path(
            tempfile.mkdtemp(prefix="vision_pages_", dir=str(workdir))
        )
        image_paths = self.renderer.render(
            pdf_path=pdf_path,
            out_dir=str(render_dir),
            dpi=self.app_config.pdf.dpi,
            image_format=self.app_config.pdf.image_format,
        )
        return [
            {
                "page": index,
                "image_path": path,
                "image_url": _image_to_data_url(Path(path)),
                "detail": self.app_config.vision.image_detail,
            }
            for index, path in enumerate(image_paths, start=1)
        ]

    def analyze(
        self,
        pdf_path: str,
        rules: list[dict],
        page_types_by_number: dict[int, list[str]] | None = None,
        on_page_result: Callable[[dict, dict[str, dict], list[dict]], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, list[dict] | dict[str, dict]]:
        page_types_by_number = page_types_by_number or {}
        vision_rules = [rule for rule in rules if rule.get("analysis_type", "text") == "vision"]
        if not vision_rules:
            return {"rule_results": {}, "page_results": []}

        unsupported_scope_results: dict[str, dict] = {}
        for rule in vision_rules:
            if rule.get("scope", "page") != "page":
                unsupported_scope_results[rule.get("id", "")] = _build_not_applicable_rule_result(
                    rule, "Multi-page scope is not yet supported for vision rules."
                )
        vision_rules = [r for r in vision_rules if r.get("scope", "page") == "page"]
        if not vision_rules:
            return {"rule_results": unsupported_scope_results, "page_results": []}

        try:
            provider = build_vision_provider(self.app_config, self.settings)
        except ValueError as exc:
            return {
                "rule_results": {
                    rule.get("id", ""): _build_skipped_result(rule, str(exc))
                    for rule in vision_rules
                },
                "page_results": [],
            }

        page_images = self._render_page_images(pdf_path)
        if not page_images:
            return {
                "rule_results": {
                    rule.get("id", ""): _build_skipped_result(
                        rule,
                        "No rendered page images were produced for vision analysis.",
                        execution_status="error",
                    )
                    for rule in vision_rules
                },
                "page_results": [],
            }

        results: dict[str, dict] = dict(unsupported_scope_results)
        page_results: list[dict] = []
        per_rule_page_results: dict[str, list[dict]] = {rule.get("id", ""): [] for rule in vision_rules}
        lock = threading.Lock()
        render_dir = Path(page_images[0]["image_path"]).parent

        # Mark rules with no applicable pages upfront; collect work items for the rest.
        applicable_pairs: list[tuple[dict, dict]] = []
        for rule in vision_rules:
            rule_id = rule.get("id", "")
            matching = [
                pi for pi in page_images
                if rule_applies_to_page(rule, page_types_by_number.get(int(pi.get("page", 0) or 0)) or [])
            ]
            if not matching:
                results[rule_id] = _build_not_applicable_rule_result(rule, "No pages matched this rule's section.")
            else:
                for pi in matching:
                    applicable_pairs.append((rule, pi))

        def _call(rule: dict, page_image: dict) -> dict | None:
            if is_cancelled and is_cancelled():
                return None
            rule_id = rule.get("id", "")
            page_number = int(page_image.get("page", 0) or 0)
            page_image_for_rule = page_image
            if rule_id == "FMT-DOUBLE-UNDERLINE":
                hints = self.renderer.extract_double_underline_hints(pdf_path, page_number - 1)
                page_image_for_rule = {**page_image, "double_underline_hints": hints}
            try:
                _t0 = time.perf_counter()
                logger.info("LLM call start: type=vision rule=%s page=%d", rule_id, page_number)
                raw_result = provider.evaluate_rule(page_image=page_image_for_rule, rule=rule, system_prompt=self.system_prompt)
                logger.info("LLM call done: type=vision rule=%s page=%d elapsed=%s", rule_id, page_number, timedelta(seconds=time.perf_counter() - _t0))
                return {
                    "page": page_number,
                    "rule_id": rule_id,
                    "rule_name": raw_result.get("rule_name", rule.get("name", rule_id)),
                    "analysis_type": "vision",
                    "execution_status": "completed",
                    "verdict": raw_result.get("verdict", "needs_review"),
                    "summary": raw_result.get("summary", ""),
                    "reasoning": raw_result.get("reasoning", ""),
                    "findings": raw_result.get("findings", []),
                    "citations": [
                        {
                            "page": int(item.get("page", page_number) or page_number),
                            "evidence": item.get("evidence", ""),
                        }
                        for item in raw_result.get("citations", [])
                        if int(item.get("page", page_number) or page_number) == page_number
                    ],
                    "notes": [f"Confidence: {raw_result.get('confidence', 'unknown')}"],
                }
            except Exception as exc:
                return {
                    "page": page_number,
                    "rule_id": rule_id,
                    "rule_name": rule.get("name", rule_id),
                    "analysis_type": "vision",
                    "execution_status": "error",
                    "verdict": "needs_review",
                    "summary": "Vision analysis failed.",
                    "reasoning": f"Vision analysis failed: {exc}",
                    "findings": [],
                    "citations": [],
                    "notes": [],
                }

        try:
            with ThreadPoolExecutor(max_workers=self.app_config.pipeline.concurrent_requests) as executor:
                future_to_rule = {executor.submit(_call, rule, pi): rule for rule, pi in applicable_pairs}
                for future in as_completed(future_to_rule):
                    if is_cancelled and is_cancelled():
                        for f in future_to_rule:
                            f.cancel()
                        break
                    rule = future_to_rule[future]
                    rule_id = rule.get("id", "")
                    page_result = future.result()
                    if page_result is None:
                        continue
                    with lock:
                        per_rule_page_results[rule_id].append(page_result)
                        page_results.append(page_result)
                        results[rule_id] = self._aggregate_rule_results(rule, per_rule_page_results[rule_id])
                        if on_page_result:
                            on_page_result(page_result, dict(results), list(page_results))

            # Any rule that had applicable pages but nothing collected (fully cancelled)
            for rule in vision_rules:
                rule_id = rule.get("id", "")
                if rule_id not in results:
                    results[rule_id] = self._aggregate_rule_results(rule, per_rule_page_results[rule_id])
        finally:
            shutil.rmtree(render_dir, ignore_errors=True)

        return {"rule_results": results, "page_results": page_results}
