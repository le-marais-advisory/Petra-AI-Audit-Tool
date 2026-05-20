from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("petra.pipeline")

from src.pipeline.result_builder import build_document_result
from src.services.validation_service import ValidationService


@dataclass
class ValidationJob:
    job_id: str
    status: str = "queued"
    message: str = "Queued"
    progress_current: int = 0
    progress_total: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None
    cancel_requested: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


class ValidationJobService:
    def __init__(self) -> None:
        self._jobs: dict[str, ValidationJob] = {}
        self._jobs_lock = threading.Lock()

    def start_job(
        self,
        pdf_path: str,
        source_filename: str | None,
        rules_json_str: str | None,
    ) -> ValidationJob:
        job = ValidationJob(job_id=uuid.uuid4().hex)
        with self._jobs_lock:
            self._jobs[job.job_id] = job
        thread = threading.Thread(
            target=self._run_job,
            args=(job.job_id, pdf_path, source_filename, rules_json_str),
            daemon=True,
        )
        thread.start()
        return job

    def get_job(self, job_id: str) -> ValidationJob | None:
        with self._jobs_lock:
            return self._jobs.get(job_id)

    def cancel_job(self, job_id: str) -> ValidationJob | None:
        job = self.get_job(job_id)
        if job is None:
            return None
        with job.lock:
            job.cancel_requested = True
            if job.status in {"queued", "running"}:
                job.message = "Stopping analysis"
        return job

    def _run_job(
        self,
        job_id: str,
        pdf_path: str,
        source_filename: str | None,
        rules_json_str: str | None,
    ) -> None:
        job = self.get_job(job_id)
        if job is None:
            return

        try:
            t0 = time.perf_counter()
            service = ValidationService()
            selected_rules = service.rule_service.load_rules(rules_json_str=rules_json_str)
            logger.info(
                "Pipeline start: file=%s rules=%d",
                source_filename or Path(pdf_path).name,
                len(selected_rules),
            )
            text_rules = [rule for rule in selected_rules if rule.get("analysis_type", "text") == "text"]
            total_steps = max(1, len(text_rules))

            with job.lock:
                job.status = "running"
                job.message = "Extracting PDF"
                job.progress_total = total_steps

            pages = service.pipeline.extractor.extract(pdf_path=pdf_path)
            page_scope_text_rules = [r for r in text_rules if r.get("scope", "page") == "page"]
            broad_scope_text_rules = [r for r in text_rules if r.get("scope", "page") in ("multi_page", "document")]
            text_total_steps = len(page_scope_text_rules) * max(1, len(pages)) + len(broad_scope_text_rules)
            vision_total_steps = service.pipeline.vision_rule_analyzer.estimate_step_count(len(pages), selected_rules)
            total_steps = max(1, text_total_steps + vision_total_steps)
            with job.lock:
                job.progress_total = total_steps
            with job.lock:
                job.result = build_document_result(
                    document_id=Path(pdf_path).stem,
                    pages=pages,
                    source_filename=source_filename,
                    selected_rules=selected_rules,
                    rule_assessments=service.pipeline.build_rule_assessments(
                        selected_rules=selected_rules,
                        text_rule_results={},
                        vision_rule_results={},
                        default_text_status="running",
                        default_vision_status="pending",
                    ),
                    text_page_results=[],
                    visual_page_results=[],
                )

            def on_page_result(page_result: dict, current_rule_results: dict[str, dict], current_page_results: list[dict]) -> None:
                current_page = page_result.get("page", 0)
                current_rule = page_result.get("rule_name", page_result.get("rule_id", ""))
                with job.lock:
                    completed_pages = len(current_page_results)
                    job.message = f"Analyzing page {current_page} - {current_rule}"
                    job.progress_current = min(completed_pages, job.progress_total)
                    job.result = build_document_result(
                        document_id=Path(pdf_path).stem,
                        pages=pages,
                        source_filename=source_filename,
                        selected_rules=selected_rules,
                        rule_assessments=service.pipeline.build_rule_assessments(
                            selected_rules=selected_rules,
                            text_rule_results=current_rule_results,
                            vision_rule_results={},
                            default_text_status="running",
                            default_vision_status="pending",
                        ),
                        text_page_results=current_page_results,
                        visual_page_results=[],
                    )

            text_analysis_results = service.pipeline.text_rule_analyzer.analyze(
                pages=pages,
                rules=selected_rules,
                on_page_result=on_page_result,
                is_cancelled=lambda: bool(job.cancel_requested),
            )

            text_rule_results = text_analysis_results.get("rule_results", {})
            text_page_results = text_analysis_results.get("page_results", [])
            vision_progress_offset = len(text_page_results)

            with job.lock:
                job.message = "Rendering pages for visual analysis"
                job.progress_current = min(vision_progress_offset, job.progress_total)
                job.result = build_document_result(
                    document_id=Path(pdf_path).stem,
                    pages=pages,
                    source_filename=source_filename,
                    selected_rules=selected_rules,
                    rule_assessments=service.pipeline.build_rule_assessments(
                        selected_rules=selected_rules,
                        text_rule_results=text_rule_results,
                        vision_rule_results={},
                        default_text_status="completed",
                        default_vision_status="running",
                    ),
                    text_page_results=text_page_results,
                    visual_page_results=[],
                )

            def on_vision_page_result(page_result: dict, current_rule_results: dict[str, dict], current_page_results: list[dict]) -> None:
                current_rule = page_result.get("rule_name", page_result.get("rule_id", ""))
                current_page = page_result.get("page", 0)
                with job.lock:
                    job.message = f"Analyzing visual page {current_page} - {current_rule}"
                    job.progress_current = min(vision_progress_offset + len(current_page_results), job.progress_total)
                    job.result = build_document_result(
                        document_id=Path(pdf_path).stem,
                        pages=pages,
                        source_filename=source_filename,
                        selected_rules=selected_rules,
                        rule_assessments=service.pipeline.build_rule_assessments(
                            selected_rules=selected_rules,
                            text_rule_results=text_rule_results,
                            vision_rule_results=current_rule_results,
                            default_text_status="completed",
                            default_vision_status="running",
                        ),
                        text_page_results=text_page_results,
                        visual_page_results=current_page_results,
                    )

            page_types_by_number = {int(p.get("page", 0)): (p.get("page_type") or []) for p in pages}
            vision_analysis_results = service.pipeline.vision_rule_analyzer.analyze(
                pdf_path=pdf_path,
                rules=selected_rules,
                page_types_by_number=page_types_by_number,
                on_page_result=on_vision_page_result,
                is_cancelled=lambda: bool(job.cancel_requested),
            )
            vision_rule_results = vision_analysis_results.get("rule_results", {})
            visual_page_results = vision_analysis_results.get("page_results", [])
            final_status = "cancelled" if job.cancel_requested else "completed"
            final_message = "Analysis stopped" if job.cancel_requested else "Analysis complete"
            logger.info(
                "Pipeline complete: file=%s pages=%d rules=%d elapsed=%s",
                source_filename or Path(pdf_path).name,
                len(pages),
                len(selected_rules),
                timedelta(seconds=time.perf_counter() - t0),
            )

            with job.lock:
                job.status = final_status
                job.message = final_message
                job.progress_current = job.progress_total
                job.result = build_document_result(
                    document_id=Path(pdf_path).stem,
                    pages=pages,
                    source_filename=source_filename,
                    selected_rules=selected_rules,
                    rule_assessments=service.pipeline.build_rule_assessments(
                        selected_rules=selected_rules,
                        text_rule_results=text_rule_results,
                        vision_rule_results=vision_rule_results,
                    ),
                    text_page_results=text_page_results,
                    visual_page_results=visual_page_results,
                )
        except Exception as exc:
            with job.lock:
                job.status = "failed"
                job.message = "Analysis failed"
                job.error = str(exc)
        finally:
            try:
                Path(pdf_path).unlink(missing_ok=True)
            except Exception:
                pass


validation_job_service = ValidationJobService()
