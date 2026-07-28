# Configuration Reference

Petra Vision is configured through environment variables (`.env` files) and a YAML configuration file (`config/app.yaml`).

## Backend Environment Variables

Set these in the root `.env` file (see `env.example` for a template).

### Application

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `APP_NAME` | string | `Petra Vision` | Application display name |
| `APP_ENV` | string | `development` | Environment (`development`, `production`) |
| `APP_DEBUG` | bool | `false` | Enable debug mode |
| `API_PREFIX` | string | `/api/v1` | API route prefix |
| `AUTH_ENABLED` | bool | `true` | Enable authentication |
| `MAX_UPLOAD_SIZE_MB` | int | `50` | Maximum PDF upload size in MB |
| `LOCAL_WORKDIR` | string | `data/tmp` | Temporary directory for PDF processing |
| `API_ALLOWED_ORIGINS` | string | `http://localhost:5173` | Comma-separated CORS origins |

### AI Providers

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `TEXT_PROVIDER` | string | `openai` | Text analysis provider (`openai` or `claude`) |
| `VISION_PROVIDER` | string | `openai` | Vision analysis provider (`openai` or `claude`) |

### OpenAI

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `OPENAI_API_KEY` | string | - | OpenAI API key (required if using OpenAI) |
| `OPENAI_TEXT_MODEL` | string | `gpt-5.4-mini` | Model for text analysis |
| `OPENAI_VISION_MODEL` | string | - | Model for vision analysis (falls back to text model) |
| `OPENAI_TEXT_TEMPERATURE` | float | - | Temperature for text analysis |
| `OPENAI_TEXT_MAX_COMPLETION_TOKENS` | int | - | Max tokens for text analysis |

### Anthropic (Claude)

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `ANTHROPIC_API_KEY` | string | - | Anthropic API key (required if using Claude) |
| `CLAUDE_TEXT_MODEL` | string | `claude-sonnet-5` | Model for text analysis |
| `CLAUDE_VISION_MODEL` | string | - | Model for vision analysis (falls back to text model) |
| `CLAUDE_TEXT_TEMPERATURE` | float | - | Temperature for text analysis. Unsupported on Sonnet 5 — leave unset |
| `CLAUDE_VISION_TEMPERATURE` | float | - | Temperature for vision analysis. Unsupported on Sonnet 5 — leave unset |
| `CLAUDE_TEXT_MAX_TOKENS` | int | `1600` | Max tokens for text analysis |
| `CLAUDE_VISION_MAX_TOKENS` | int | `1600` | Max tokens for vision analysis |

Claude Sonnet 5 rejects a non-default `temperature` with a 400. Both temperature
settings are omitted from the request when unset (the default) — only set them if
you have pointed `CLAUDE_TEXT_MODEL` / `CLAUDE_VISION_MODEL` at an older model
that accepts sampling parameters.

### Azure Authentication

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `AZURE_TENANT_ID` | string | - | Azure AD tenant ID |
| `AZURE_CLIENT_ID` | string | - | Backend app registration client ID |
| `AZURE_AUDIENCE` | string | - | Expected token audience (e.g., `api://{client_id}`) |
| `AZURE_REQUIRED_SCOPE` | string | `access_as_user` | Required scope in the token |
| `AZURE_ALLOWED_CLIENT_APP_IDS` | string | - | Comma-separated allowed client app IDs |
| `AZURE_AUTHORITY_HOST` | string | `https://login.microsoftonline.com` | Azure authority host |
| `AZURE_ACCEPTED_TOKEN_VERSIONS` | string | `1.0,2.0` | Accepted token versions |

### Legacy/Internal

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `JWT_SECRET_KEY` | string | `change-me` | JWT secret for local auth (legacy) |
| `JWT_ALGORITHM` | string | `HS256` | JWT algorithm |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | int | `30` | JWT expiry |
| `APP_ADMIN_EMAIL` | string | `admin@example.com` | Legacy admin email |
| `APP_ADMIN_PASSWORD` | string | `admin` | Legacy admin password |
| `ENABLE_UI` | bool | `false` | Deprecated. Legacy built-in UI. |

## Frontend Environment Variables

Set these in `frontend/.env` (see `frontend/.env.example` for a template).

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `VITE_APP_NAME` | string | `Petra Vision` | Application display name |
| `VITE_API_BASE_URL` | string | `http://localhost:8000` | Backend API base URL |
| `VITE_API_PREFIX` | string | `/api/v1` | API route prefix |
| `VITE_AUTH_ENABLED` | string | `false` | Enable auth gate (`true`/`false`) |
| `VITE_AZURE_CLIENT_ID` | string | - | Frontend app registration client ID |
| `VITE_AZURE_TENANT_ID` | string | - | Azure AD tenant ID |
| `VITE_AZURE_AUTHORITY` | string | - | Azure authority URL |
| `VITE_AZURE_REDIRECT_URI` | string | - | Post-login redirect URI |
| `VITE_AZURE_POST_LOGOUT_REDIRECT_URI` | string | - | Post-logout redirect URI |
| `VITE_API_SCOPE` | string | - | API scope to request from Azure |

## Application YAML (`config/app.yaml`)

This file controls PDF processing, vision analysis, and report generation settings.

### PDF Settings

```yaml
pdf:
  dpi: 300              # Resolution for page rendering (higher = better quality, slower)
  image_format: "png"   # Output format for rendered pages (png or jpeg)
  text_layout: true     # Preserve spatial layout during text extraction
  text_x_density: 7.25  # Horizontal character density for layout analysis
  text_y_density: 13.0  # Vertical character density for layout analysis
```

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `dpi` | int | `300` | PDF rendering resolution in dots per inch |
| `image_format` | string | `png` | Image format for rendered pages |
| `text_layout` | bool | `true` | Preserve text positioning during extraction |
| `text_x_density` | float | `7.25` | Horizontal density for layout-aware extraction |
| `text_y_density` | float | `13.0` | Vertical density for layout-aware extraction |

### Vision Settings

```yaml
vision:
  provider: "openai"
  model_id: "gpt-5.4"
  max_images_per_request: 10
  temperature: 0.1
  seed: 42
  max_completion_tokens: 1600
  image_detail: "high"
  concurrent_requests: 12
  global_max_concurrent: 24
```

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `provider` | string | `openai` | Default vision provider |
| `model_id` | string | `gpt-5.4` | Default vision model |
| `max_images_per_request` | int | `10` | Max images in a single LLM request |
| `temperature` | float | `0.1` | LLM temperature (lower = more deterministic) |
| `seed` | int | `42` | Seed for reproducible results |
| `max_completion_tokens` | int | `1600` | Max tokens in LLM response |
| `image_detail` | string | `high` | Image detail level (OpenAI-specific) |
| `concurrent_requests` | int | `12` | Concurrent requests per rule |
| `global_max_concurrent` | int | `24` | Global concurrency cap across all rules |

### Report Settings

```yaml
report:
  include_thumbnails: false
```

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `include_thumbnails` | bool | `false` | Include page thumbnails in exported PDF reports |

### Pipeline Settings

```yaml
pipeline:
  concurrent_requests: 12
```

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `concurrent_requests` | int | `12` | Concurrent text-analysis LLM calls |

Notes:

- Both page-scope and broad-scope (`multi_page` / `document`) text rules share this one
  pool, so it governs the whole text phase.
- Floored at 1 and capped at `MAX_TEXT_WORKERS` (24) in
  `src/pipeline/text_rule_analyzer.py`, so a typo cannot launch hundreds of threads.
- **Jobs run one at a time** (`ValidationJobService` holds a single slot), so this is also
  the pipeline's total in-flight ceiling regardless of how many uploads are queued.
- Override without touching the YAML by setting `PIPELINE_CONCURRENT_REQUESTS` in `.env`.
  This matters in deployment: `config/` is baked into the container image, so the env var
  is the only way to change the value without a rebuild.
- `load_app_yaml()` is cached for the process lifetime, so a YAML edit needs a restart.
- If you see HTTP 429s from the provider, lower this to 8 and re-measure.

## Key Files

- `env.example` - Backend environment template
- `frontend/.env.example` - Frontend environment template
- `config/app.yaml` - Application YAML configuration
- `src/core/config.py` - Settings class and YAML loader
