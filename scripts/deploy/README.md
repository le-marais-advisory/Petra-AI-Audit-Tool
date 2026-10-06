# Deploy scripts

Runnable deployment for Petra Vision on Azure Container Apps. Builds the image
in ACR (remote build — **no local Docker required**, works from Azure Cloud
Shell) and rolls the Container App onto a fresh, uniquely-tagged revision.

## Usage

From **Azure Cloud Shell** (bash), in a clone of this repo:

```bash
git pull                              # get the code you want to ship
./scripts/deploy/deploy.sh backend    # API only
./scripts/deploy/deploy.sh frontend   # SPA only
./scripts/deploy/deploy.sh all        # both (default if no arg)
```

That's it. The script:

1. Selects the `Petra AI Tools` subscription.
2. Builds the correct image from the correct folder with a **fresh, unique tag**
   (`vYYYYMMDD-HHMMSS`) — backend from the repo root (`docker/AppProdDockerfile`),
   frontend from `frontend/` (`docker/AppDevProdDockerfile`).
3. **Only** updates the Container App if the build succeeded (`set -e`), so a
   failed build can never point the app at a missing image.
4. Waits until the new revision is the ready one and prints the app URL.

## First time on a fresh Cloud Shell instance

Cloud Shell's home resets between sessions. Clone into the persistent drive:

```bash
cd ~/clouddrive
git clone https://github.com/le-marais-advisory/Petra-AI-Audit-Tool.git
cd Petra-AI-Audit-Tool
./scripts/deploy/deploy.sh all
```

## Configuration

Sensible defaults are baked in; override any of them with environment variables:

| Var | Default |
|---|---|
| `SUBSCRIPTION` | `Petra AI Tools` |
| `RESOURCE_GROUP` | `PET-RG-03` |
| `ACR_NAME` / `ACR_SERVER` | `petraaitools` / `petraaitools-aebca3bgcnavanh8.azurecr.io` |
| `BACKEND_APP` / `BACKEND_IMAGE` | `ai-audit-tool` / `petra-api` |
| `FRONTEND_APP` / `FRONTEND_IMAGE` | `ai-audit-tool-frontend` / `petra-frontend` |
| `TAG` | `vYYYYMMDD-HHMMSS` (auto) |

Example:

```bash
SUBSCRIPTION="Petra AI Tools" TAG=hotfix-1 ./scripts/deploy/deploy.sh backend
```

## Notes

- Backend changes (rules, PDF report, pipeline) ship with `backend`. Frontend
  changes (UI, branding, logo) ship with `frontend`. When in doubt, run `all`.
- Container-app **env vars** (provider keys, `VITE_*`, auth) are not touched by
  this script — they carry over into the new revision. Manage those separately.
- For the full `azd`-based infrastructure provisioning path, see
  `DEPLOYMENT_GUIDE.md` at the repo root.
