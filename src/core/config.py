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
    model_config = ConfigDict(protected_namespaces=())

    provider: str = "openai"
    model_id: str = "gpt-5.4"
    max_images_per_request: int = 10
    temperature: float = 0.1
    seed: int = 42
    max_completion_tokens: int = 1600
    image_detail: str = "high"
    concurrent_requests: int = 12
    global_max_concurrent: int = 24


class ReportConfig(BaseModel):
    include_thumbnails: bool = False


class PipelineConfig(BaseModel):
    concurrent_requests: int = 2


class AppYaml(BaseModel):
    app: dict = {"project_name": "petra-vision"}
    pdf: PdfConfig = PdfConfig()
    vision: VisionConfig = VisionConfig()
    report: ReportConfig = ReportConfig()
    pipeline: PipelineConfig = PipelineConfig()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", enable_decoding=False)

    APP_NAME: str = "Petra Vision"
    APP_ENV: str = "development"
    APP_DEBUG: bool = False
    API_PREFIX: str = "/api/v1"
    ENABLE_UI: bool = False
    AUTH_ENABLED: bool = True

    OPENAI_API_KEY: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "OPEN_AI_API_KEY"),
    )
    ANTHROPIC_API_KEY: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "ANTHROPIC_AI_API_KEY", "ANTROPIC_AI_API_KEY"),
    )
    COHERE_API_KEY: str | None = None
    TEXT_PROVIDER: Literal["openai", "claude"] = "openai"
    VISION_PROVIDER: Literal["openai", "claude"] = "openai"
    OPENAI_TEXT_MODEL: str = "gpt-5.4-mini"
    OPENAI_VISION_MODEL: str | None = None
    OPENAI_TEXT_TEMPERATURE: float | None = None
    OPENAI_TEXT_MAX_COMPLETION_TOKENS: int | None = None
    CLAUDE_TEXT_MODEL: str = "claude-sonnet-4-6"
    CLAUDE_VISION_MODEL: str | None = None
    CLAUDE_TEXT_TEMPERATURE: float | None = None
    CLAUDE_VISION_TEMPERATURE: float | None = None
    CLAUDE_TEXT_MAX_TOKENS: int = 4096
    CLAUDE_VISION_MAX_TOKENS: int = 1600

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

    @field_validator("TEXT_PROVIDER", "VISION_PROVIDER", mode="before")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        normalized = str(value or "openai").strip().lower().replace("_", " ").replace("-", " ")
        provider_aliases = {
            "openai": "openai",
            "open ai": "openai",
            "claude": "claude",
            "anthropic": "claude",
        }
        if normalized not in provider_aliases:
            raise ValueError(f"Unsupported provider '{value}'. Expected one of: openai, claude.")
        return provider_aliases[normalized]

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
