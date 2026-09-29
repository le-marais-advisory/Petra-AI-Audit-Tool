from __future__ import annotations

from contextlib import asynccontextmanager
import json
from pathlib import Path

import typer
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from src.api.deps import require_authenticated_principal
from src.api.errors import register_exception_handlers
from src.api.middleware import register_middleware
from src.api.routers import auth, document_types, export, feedback, health, rules, validations
from src.core.azure_auth import ensure_azure_auth_configured
from src.core.config import get_settings
from src.core.logging import configure_logging
from src.services.validation_service import ValidationService


cli = typer.Typer(help="Petra Vision service CLI")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if settings.AUTH_ENABLED:
            ensure_azure_auth_configured(settings)
        yield

    app = FastAPI(
        title=settings.APP_NAME,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    register_middleware(app)
    register_exception_handlers(app)

    app.include_router(health.router, prefix=settings.API_PREFIX)
    app.include_router(auth.router, prefix=settings.API_PREFIX)
    protected_router_dependencies = [Depends(require_authenticated_principal)] if settings.AUTH_ENABLED else []
    app.include_router(validations.router, prefix=settings.API_PREFIX, dependencies=protected_router_dependencies)
    app.include_router(rules.router, prefix=settings.API_PREFIX, dependencies=protected_router_dependencies)
    app.include_router(document_types.router, prefix=settings.API_PREFIX, dependencies=protected_router_dependencies)
    app.include_router(feedback.router, prefix=settings.API_PREFIX, dependencies=protected_router_dependencies)
    app.include_router(export.router, prefix=settings.API_PREFIX, dependencies=protected_router_dependencies)

    # Deprecated: the legacy built-in UI under src/ui is no longer mounted by the backend.
    # The supported operator UI now lives in the separate React frontend under frontend/.
    # ENABLE_UI is kept only for backward-compatible configuration parsing and is ignored here.
    @app.get("/")
    async def root() -> JSONResponse:
        return JSONResponse(
            content={
                "service": settings.APP_NAME,
                "docs": None,
                "api_prefix": settings.API_PREFIX,
                "runtime_mode": "ephemeral",
                "persistence": "disabled",
                "ui_enabled": False,
                "legacy_ui_deprecated": True,
                "authentication": "microsoft-entra-id" if settings.AUTH_ENABLED else "disabled",
            }
        )

    return app


app = create_app()


def _run_pipeline(file_path: str, document_type: str = "financial_statements", options: dict | None = None) -> dict:
    service = ValidationService()
    return service.validate_document(
        file_path=file_path,
        source_filename=Path(file_path).name,
        document_type=document_type,
        options=options,
    )


@cli.command("validate")
def cli_validate(
    file: str | None = typer.Option(None, help="Path to the document to validate"),
    pdf: str | None = typer.Option(None, help="Deprecated alias of --file"),
    document_type: str = typer.Option("financial_statements", help="Document type id (financial_statements, capital_event_workbook)"),
    event_type: str | None = typer.Option(None, help="Capital-event workbooks: capital_call | distribution | net_event"),
    out: str | None = typer.Option(None, help="Where to write the extraction JSON. If omitted, prints JSON to stdout."),
) -> None:
    path = file or pdf
    if path is None:
        raise typer.BadParameter("--file is required")
    options = {"event_type": event_type} if event_type else {}
    data = _run_pipeline(file_path=path, document_type=document_type, options=options)
    if out is None:
        typer.echo(json.dumps(data, indent=2))
        return

    output_path = Path(out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    typer.echo(f"Wrote {out}")


if __name__ == "__main__":
    cli()
