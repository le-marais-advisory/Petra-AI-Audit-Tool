from __future__ import annotations

import json
from pathlib import Path
import tempfile

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from src.core.config import get_settings
from src.document_types.registry import (
    DocumentTypeSpec,
    InvalidOptionsError,
    UnsupportedFormatError,
    ensure_format,
    get_document_type,
    prior_document_required,
    validate_options,
)
from src.schemas.validation import DocumentValidationResponse, ValidationJobResponse
from src.services.validation_service import ValidationService
from src.services.validation_job_service import validation_job_service


router = APIRouter(prefix="/validations", tags=["validations"])


def _resolve_request(document_type: str, options_json: str | None) -> tuple[DocumentTypeSpec, dict]:
    try:
        spec = get_document_type(document_type)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        options = json.loads(options_json) if options_json else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="options_json is not valid JSON.") from exc
    if not isinstance(options, dict):
        raise HTTPException(status_code=422, detail="options_json must be a JSON object.")
    try:
        return spec, validate_options(spec, options)
    except InvalidOptionsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _read_and_validate_upload(upload: UploadFile, spec: DocumentTypeSpec, max_size_mb: int,
                                    prior: bool = False) -> tuple[bytes, str]:
    content = await upload.read()
    if len(content) > max_size_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File exceeds {max_size_mb} MB limit.")
    try:
        file_format = ensure_format(spec, content, upload.filename, prior=prior)
    except UnsupportedFormatError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    return content, file_format


def _stage_upload_for_processing(filename: str, content: bytes, file_format: str, workdir: str) -> tuple[str, str]:
    default_name = f"document.{file_format}"
    safe_filename = Path(filename or default_name).name or default_name
    temp_dir = Path(workdir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="upload_", suffix=f".{file_format}", dir=str(temp_dir), delete=False) as handle:
        handle.write(content)
        return safe_filename, handle.name


async def _stage_prior(spec: DocumentTypeSpec, options: dict, prior_file: UploadFile | None, settings) -> tuple[str | None, str | None]:
    """Validate and stage the prior-event document (returns its source name and temp path)."""
    if spec.prior_document is None:
        if prior_file is not None:
            raise HTTPException(status_code=422, detail=f"{spec.label} does not take a prior document.")
        return None, None
    waiver = spec.prior_document["waived_by_option"]
    if prior_file is None:
        if prior_document_required(spec, options):
            raise HTTPException(status_code=422, detail=(
                f"Upload the {spec.prior_document['label'].lower()} or mark this as the fund's first capital event."))
        return None, None
    if options.get(waiver):
        raise HTTPException(status_code=422, detail=(
            f"A {spec.prior_document['label'].lower()} was uploaded although the run is marked as the first event."))
    content, file_format = await _read_and_validate_upload(prior_file, spec, settings.MAX_UPLOAD_SIZE_MB, prior=True)
    return _stage_upload_for_processing(
        filename=prior_file.filename or "", content=content, file_format=file_format, workdir=settings.LOCAL_WORKDIR)


def _pick_upload(file: UploadFile | None, pdf: UploadFile | None) -> UploadFile:
    upload = file or pdf
    if upload is None:
        raise HTTPException(status_code=422, detail="No file uploaded (form field 'file').")
    return upload


@router.post("", response_model=DocumentValidationResponse)
async def validate_document(
    file: UploadFile | None = File(None, description="Document to validate"),
    pdf: UploadFile | None = File(None, description="Deprecated alias of 'file'"),
    document_type: str = Form("financial_statements", description="Document type id (see GET /document-types)"),
    options_json: str | None = Form(None, description="Run options JSON for the document type"),
    rules_json: str | None = Form(None, description="Selected rules JSON"),
    prior_file: UploadFile | None = File(None, description="Prior event document, when the type takes one"),
) -> DocumentValidationResponse:
    settings = get_settings()
    spec, options = _resolve_request(document_type, options_json)
    upload = _pick_upload(file, pdf)
    content, file_format = await _read_and_validate_upload(upload, spec, settings.MAX_UPLOAD_SIZE_MB)
    prior_name, prior_path = await _stage_prior(spec, options, prior_file, settings)
    filename, file_path = _stage_upload_for_processing(
        filename=upload.filename or "", content=content, file_format=file_format, workdir=settings.LOCAL_WORKDIR,
    )
    service = ValidationService()
    try:
        result = service.validate_document(
            file_path=file_path,
            source_filename=filename,
            rules_json_str=rules_json,
            document_type=spec.id,
            options=options,
            prior_file_path=prior_path,
            prior_source_filename=prior_name,
        )
        return DocumentValidationResponse(**result)
    finally:
        for path in (file_path, prior_path):
            try:
                if path:
                    Path(path).unlink(missing_ok=True)
            except Exception:
                pass


@router.post("/jobs", response_model=ValidationJobResponse)
async def create_validation_job(
    file: UploadFile | None = File(None, description="Document to validate"),
    pdf: UploadFile | None = File(None, description="Deprecated alias of 'file'"),
    document_type: str = Form("financial_statements", description="Document type id (see GET /document-types)"),
    options_json: str | None = Form(None, description="Run options JSON for the document type"),
    rules_json: str | None = Form(None, description="Selected rules JSON"),
    prior_file: UploadFile | None = File(None, description="Prior event document, when the type takes one"),
) -> ValidationJobResponse:
    settings = get_settings()
    spec, options = _resolve_request(document_type, options_json)
    upload = _pick_upload(file, pdf)
    content, file_format = await _read_and_validate_upload(upload, spec, settings.MAX_UPLOAD_SIZE_MB)
    prior_name, prior_path = await _stage_prior(spec, options, prior_file, settings)
    filename, file_path = _stage_upload_for_processing(
        filename=upload.filename or "", content=content, file_format=file_format, workdir=settings.LOCAL_WORKDIR,
    )
    job = validation_job_service.start_job(
        file_path=file_path,
        source_filename=filename,
        rules_json_str=rules_json,
        document_type=spec.id,
        options=options,
        prior_file_path=prior_path,
        prior_source_filename=prior_name,
    )
    return ValidationJobResponse(
        job_id=job.job_id,
        status=job.status,
        message=job.message,
        progress_current=job.progress_current,
        progress_total=job.progress_total,
    )


@router.get("/jobs/{job_id}", response_model=ValidationJobResponse)
async def get_validation_job(job_id: str) -> ValidationJobResponse:
    job = validation_job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Validation job not found.")
    with job.lock:
        result = DocumentValidationResponse(**job.result) if job.result else None
        return ValidationJobResponse(
            job_id=job.job_id,
            status=job.status,
            message=job.message,
            progress_current=job.progress_current,
            progress_total=job.progress_total,
            error=job.error,
            result=result,
        )


@router.post("/jobs/{job_id}/cancel", response_model=ValidationJobResponse)
async def cancel_validation_job(job_id: str) -> ValidationJobResponse:
    job = validation_job_service.cancel_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Validation job not found.")
    with job.lock:
        result = DocumentValidationResponse(**job.result) if job.result else None
        return ValidationJobResponse(
            job_id=job.job_id,
            status=job.status,
            message=job.message,
            progress_current=job.progress_current,
            progress_total=job.progress_total,
            error=job.error,
            result=result,
        )
