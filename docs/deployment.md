# Deployment

## Local Development (Docker Compose)

The simplest way to run the full stack:

```bash
docker compose up --build
```

This starts two services:

| Service | Container | Port | Description |
|---------|-----------|------|-------------|
| `petra_vision_api` | `petra_vision_api` | 8000 | FastAPI backend |
| `petra_vision_frontend` | `petra_vision_frontend` | 5173 | React dev server |

### Volume Mounts

The Docker Compose file mounts source directories for live-reload development:

- `./src` -> `/app/src` (backend source)
- `./config` -> `/app/config` (app.yaml, prompts)
- `./rules` -> `/app/rules` (rule definitions)
- `./data` -> `/app/data` (temporary working directory)
- `./frontend` -> `/app` (frontend source, excluding `node_modules`)

### Environment Overrides

The `docker-compose.yml` sets these defaults:

```yaml
petra_vision_api:
  environment:
    ENABLE_UI: "false"
    LOCAL_WORKDIR: /app/data/tmp
    APP_ENV: development
  env_file:
    - .env

petra_vision_frontend:
  environment:
    VITE_APP_NAME: Petra Vision
    VITE_API_BASE_URL: http://localhost:8000
    VITE_API_PREFIX: /api/v1
    VITE_AUTH_ENABLED: "false"
    VITE_SHOW_RULES_SIDEBAR: "true"
```

### Rules sidebar visibility

The Validation Rules sidebar is a development affordance. It is read at container
start from `VITE_SHOW_RULES_SIDEBAR` (via `runtime-config.js`), so the same image
serves both cases — no rebuild needed to change it:

- Local dev (`docker-compose.yml`, `frontend/.env`): `true`
- Azure deployments: the `showRulesSidebar` Bicep param defaults to `false`, so the
  sidebar is hidden and every run evaluates the full rule set. To show it in a
  non-client environment, deploy with `showRulesSidebar=true` or set the frontend
  container app env var directly:

```bash
az containerapp update -n <frontend-app> -g <resource-group> \
  --set-env-vars VITE_SHOW_RULES_SIDEBAR=true
```

Note that with the sidebar hidden, per-rule `bypassable` toggles are unavailable,
so no rule is bypassed.

## Azure Container Apps

Petra Vision is designed for deployment on **Azure Container Apps**.

### Infrastructure-as-Code

The `infra/` directory contains Azure Bicep templates:

- `infra/main.bicep` - Main infrastructure definition
- `infra/main.parameters.json` - Parameter values

Resources provisioned:
- Azure Container Apps Environment
- Azure Container App (backend API)
- Azure Container Registry
- Log Analytics Workspace

### Azure Developer CLI (azd)

The project includes an `azure.yaml` configuration for the Azure Developer CLI:

```bash
# Initialize (first time)
azd init

# Deploy
azd up

# Update after code changes
azd deploy
```

### Dockerfiles

| Dockerfile | Purpose |
|------------|---------|
| `docker/AppDevDockerfile` | Backend container |
| `frontend/docker/AppDevDockerfile` | Frontend container |

### Production Environment Variables

When deploying to Azure, set these environment variables on the container app:

**Required:**
```env
AUTH_ENABLED=true
AZURE_TENANT_ID=<your-tenant-id>
AZURE_CLIENT_ID=<your-backend-client-id>
AZURE_AUDIENCE=api://<your-backend-client-id>
AZURE_REQUIRED_SCOPE=access_as_user
AZURE_ALLOWED_CLIENT_APP_IDS=<your-frontend-client-id>
```

**AI Provider (at least one):**
```env
OPENAI_API_KEY=sk-...
TEXT_PROVIDER=openai
VISION_PROVIDER=openai
```

or

```env
ANTHROPIC_API_KEY=sk-ant-...
TEXT_PROVIDER=claude
VISION_PROVIDER=claude
```

**Application:**
```env
APP_ENV=production
API_ALLOWED_ORIGINS=https://your-frontend-domain.com
LOCAL_WORKDIR=/app/data/tmp
```

### Azure App Registrations

See [Authentication](authentication.md) for setting up the required Entra ID app registrations. The `scripts/entra/sync_apps.py` script can automate this process.

## Key Files

- `docker-compose.yml` - Local dev orchestration
- `docker/AppDevDockerfile` - Backend Docker image
- `frontend/docker/AppDevDockerfile` - Frontend Docker image
- `azure.yaml` - Azure Developer CLI configuration
- `infra/main.bicep` - Azure infrastructure definition
- `infra/main.parameters.json` - Infrastructure parameters
- `scripts/entra/sync_apps.py` - Entra ID app registration automation
