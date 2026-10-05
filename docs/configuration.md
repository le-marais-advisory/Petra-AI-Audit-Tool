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

### Anthropic (Claude)

Every LLM call goes to Claude. How a call's model and effort are chosen, and how a rule overrides them, is covered in [AI Models](providers.md).

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `ANTHROPIC_API_KEY` | string | - | Anthropic API key (required) |
| `CLAUDE_TEXT_MODEL` | string | `claude-sonnet-5-5` | Default model for text rules, workbook hybrid rules, role assignment and layout mapping. It must be listed in `config/models.yaml` for cost figures |
| `CLAUDE_VISION_MODEL` | string | - | Model for vision analysis (falls back to text model) |
| `CLAUDE_TEXT_TEMPERATURE` | float | - | Temperature for text calls. Sent only to models that accept it; Sonnet 5 and 5.5 don't |
| `CLAUDE_VISION_TEMPERATURE` | float | - | Temperature for vision calls. Same rule |
| `CLAUDE_TEXT_MAX_TOKENS` | int | `24000` | Max tokens for text analysis. Covers reasoning as well as the response on models that think by default. Measured peak across fixtures is 15.2k output tokens; 4096 truncated 6 of 328 calls and 8192 truncated 3 |
| `CLAUDE_VISION_MAX_TOKENS` | int | `16000` | Max tokens for vision calls. Covers thinking as well as the answer; 1600 left no room to think at the default effort |
| `CLAUDE_STRUCTURED_MAX_TOKENS` | int | `64000` | Max tokens for workbook role assignment and layout mapping (streamed). A wide ITD sheet (~32 event blocks) used 20.3k at medium effort |
| `LAYOUT_MAPPING_EFFORT` | effort or empty | `medium` | Effort for workbook layout mapping. On a truncated answer the mapper retries one level lower. Empty uses the model default (`high`), which ran out of tokens on a wide ITD sheet on Sonnet 5 |
| `TEXT_RULE_EFFORT` | effort or empty | empty | Default effort for PDF text rules and workbook hybrid rules. Empty is the model default. A rule's `effort` field overrides it |
| `VISION_RULE_EFFORT` | effort or empty | empty | Default effort for PDF vision rules |
| `ROLE_ASSIGNMENT_EFFORT` | effort or empty | empty | Effort for workbook sheet-role confirmation |

The effort levels are `low`, `medium`, `high`, `xhigh` and `max`. Models that don't accept effort (Haiku 4.5) never receive it.

Claude Sonnet 5 and 5.5 reject a non-default `temperature` with a 400. The temperature settings are sent only to models whose `config/models.yaml` entry has `sampling: true`.

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
| `VITE_SHOW_RULES_SIDEBAR` | string | `false` | Show the Validation Rules sidebar (`true`/`false`). Off means every run uses the full rule set |
| `VITE_SHOW_TOKEN_USAGE` | string | `false` | Show the Token Usage tab with the run's LLM token consumption (`true`/`false`) |
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
  max_images_per_request: 10
  concurrent_requests: 12
  global_max_concurrent: 24
```

The vision model and its token budget are env settings (`CLAUDE_VISION_MODEL`, `CLAUDE_VISION_MAX_TOKENS`).

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `max_images_per_request` | int | `10` | Max images in a single LLM request |
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
  prompt_cache: true
```

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `concurrent_requests` | int | `12` | Concurrent text-analysis LLM calls |
| `prompt_cache` | bool | `true` | Cache the shared start of rule prompts and start one call per shared prefix first (text, vision and workbook hybrid rules) |

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

#### Prompt caching

Every rule prompt runs from the most shared content to the least: the document content
(page text and tables, a page image, or sheet excerpts), then content only some rules
need (layout metadata), then the rule's own context and the rule. With `prompt_cache`
on:

- the shared parts carry Claude `cache_control` markers, but only when another call will
  read them, because a cache write costs 1.25x plain input and a read 0.1x;
- one call per shared prefix (per page, per broad-scope payload, per hybrid sheet set)
  runs first and the rest of its group starts when it finishes, because calls that start
  together cannot read each other's cache entries. Layout rules go first on their page,
  so the first call writes both entries.

Off sends no markers and starts every call at once; the prompt order stays content-first.
Check the effect in the Token Usage tab (`VITE_SHOW_TOKEN_USAGE`): cached input is 0
without caching. A prefix shorter than the model's minimum (1024 tokens on Claude Sonnet
5) is not cached even when marked. Caches are per model, so rules that a `model` or
`effort` override sends elsewhere are grouped separately.

## Key Files

- `env.example` - Backend environment template
- `frontend/.env.example` - Frontend environment template
- `config/app.yaml` - Application YAML configuration
- `src/core/config.py` - Settings class and YAML loader
