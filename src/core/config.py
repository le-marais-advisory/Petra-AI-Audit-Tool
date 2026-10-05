from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PdfConfig(BaseModel):
    dpi: int = 300
    image_format: str = "png"
    text_layout: bool = True
    text_x_density: float = 7.25
    text_y_density: float = 13.0


class VisionConfig(BaseModel):
    # The vision model and its budget are Settings (CLAUDE_VISION_MODEL, CLAUDE_VISION_MAX_TOKENS).
    model_config = ConfigDict(extra="ignore")

    max_images_per_request: int = 10
    concurrent_requests: int = 12
    global_max_concurrent: int = 24


class ReportConfig(BaseModel):
    include_thumbnails: bool = False


class PipelineConfig(BaseModel):
    # Concurrent text-analysis LLM calls. Matches vision.concurrent_requests, which
    # already runs at 12 in production. Floored and capped by
    # TextRuleAnalyzer._max_workers, and overridable via PIPELINE_CONCURRENT_REQUESTS.
    concurrent_requests: int = 12
    # Mark the shared start of rule prompts (document content, page image, sheet excerpts)
    # for Claude's prompt cache, and start one call per shared prefix before the rest so
    # they can read it. Applies to text, vision and workbook hybrid rules. Off sends no
    # markers and starts every call at once; the prompt order is content-first either way.
    prompt_cache: bool = True


class WorkbookConfig(BaseModel):
    layout_concurrency: int = 6  # parallel layout-mapping calls (one per relevant sheet)
    hybrid_concurrency: int = 6  # parallel hybrid-rule LLM calls


class AppYaml(BaseModel):
    app: dict = {"project_name": "petra-vision"}
    pdf: PdfConfig = PdfConfig()
    vision: VisionConfig = VisionConfig()
    report: ReportConfig = ReportConfig()
    pipeline: PipelineConfig = PipelineConfig()
    workbook: WorkbookConfig = WorkbookConfig()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", enable_decoding=False)

    APP_NAME: str = "Petra Vision"
    APP_ENV: str = "development"
    APP_DEBUG: bool = False
    API_PREFIX: str = "/api/v1"
    ENABLE_UI: bool = False
    AUTH_ENABLED: bool = True

    ANTHROPIC_API_KEY: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "ANTHROPIC_AI_API_KEY", "ANTROPIC_AI_API_KEY"),
    )
    COHERE_API_KEY: str | None = None
    # Default models; a rule or a comparison variant can override them (see config/models.yaml).
    # The text model also runs workbook hybrid rules, role assignment and layout mapping.
    CLAUDE_TEXT_MODEL: str = "claude-sonnet-5-5"
    CLAUDE_VISION_MODEL: str | None = None  # falls back to CLAUDE_TEXT_MODEL
    # Only sent to models that accept a non-default temperature (Sonnet 5 and 5.5 do not).
    CLAUDE_TEXT_TEMPERATURE: float | None = None
    CLAUDE_VISION_TEMPERATURE: float | None = None
    # Covers reasoning as well as the response on models that think by default, so too
    # low a value truncates the JSON mid-response. Measured over 328 calls across two
    # fixtures: median 598 output tokens, p95 3.1k, but a long tail — the peak was
    # 15,219 (SOI-PERCENTAGE-TIE, which recomputes a percentage per investment).
    # 4096 truncated 6 calls and 8192 truncated 3; 24000 truncated none.
    # This is a ceiling the model cannot see, not a target, so headroom costs nothing.
    CLAUDE_TEXT_MAX_TOKENS: int = 24000
    # Role assignment and workbook layout mapping (streamed). The widest ITD sheet seen so
    # far (FTV V, ~32 event blocks) used 20.3k output tokens at medium effort.
    CLAUDE_STRUCTURED_MAX_TOKENS: int = 64000
    # Reasoning effort for layout mapping. At the model default (high) the FTV V ITD sheet
    # thought past 24k tokens without answering; medium mapped it correctly; low finished
    # fast but mis-mapped the block headers. On a truncated answer the mapper retries one
    # level lower. Empty means the model default.
    LAYOUT_MAPPING_EFFORT: Literal["low", "medium", "high", "xhigh", "max", ""] = "medium"
    # Default effort for rule calls and role assignment. Empty means the model default (high on
    # Sonnet 5.5). A rule's own "effort" field overrides these.
    TEXT_RULE_EFFORT: Literal["low", "medium", "high", "xhigh", "max", ""] = ""  # PDF text and workbook hybrid rules
    VISION_RULE_EFFORT: Literal["low", "medium", "high", "xhigh", "max", ""] = ""
    ROLE_ASSIGNMENT_EFFORT: Literal["low", "medium", "high", "xhigh", "max", ""] = ""
    # Covers thinking as well as the answer, like CLAUDE_TEXT_MAX_TOKENS. 1600 left no room to
    # think at the default effort; a ceiling the model cannot see, so headroom costs nothing.
    CLAUDE_VISION_MAX_TOKENS: int = 16000

    # Overrides pipeline.concurrent_requests from app.yaml. config/ is baked into the
    # container image, so without this the most tuning-prone number in the pipeline
    # can only be rolled back by rebuilding and redeploying.
    PIPELINE_CONCURRENT_REQUESTS: int | None = None

    JWT_SECRET_KEY: str = "change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    APP_ADMIN_EMAIL: str = "admin@example.com"
    APP_ADMIN_PASSWORD: str = "admin"

    MAX_UPLOAD_SIZE_MB: int = 50
    LOCAL_WORKDIR: str = "data/tmp"
    API_ALLOWED_ORIGINS: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    AZURE_TENANT_ID: str | None = None
    AZURE_CLIENT_ID: str | None = None
    AZURE_AUDIENCE: str | None = None
    AZURE_REQUIRED_SCOPE: str = "access_as_user"
    AZURE_ALLOWED_CLIENT_APP_IDS: list[str] = Field(default_factory=list)
    AZURE_AUTHORITY_HOST: str = "https://login.microsoftonline.com"
    AZURE_ACCEPTED_TOKEN_VERSIONS: list[Literal["1.0", "2.0"]] = Field(default_factory=lambda: ["1.0", "2.0"])

    @field_validator("API_ALLOWED_ORIGINS", "AZURE_ALLOWED_CLIENT_APP_IDS", "AZURE_ACCEPTED_TOKEN_VERSIONS", mode="before")
    @classmethod
    def parse_csv_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        normalized = str(value).strip()
        if not normalized:
            return []
        if normalized.startswith("["):
            parsed = yaml.safe_load(normalized)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        return [item.strip() for item in normalized.split(",") if item.strip()]

    @property
    def azure_expected_audiences(self) -> list[str]:
        audiences: list[str] = []
        if self.AZURE_CLIENT_ID:
            audiences.append(self.AZURE_CLIENT_ID)
        if self.AZURE_AUDIENCE and self.AZURE_AUDIENCE not in audiences:
            audiences.append(self.AZURE_AUDIENCE)
        return audiences


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def load_app_yaml(path: str = "config/app.yaml") -> AppYaml:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return AppYaml(**data)
