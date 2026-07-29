# Azure Audit Tool — Deployment Instructions (7-29-26)

How to redeploy the Petra AI Audit Tool (backend + frontend) to Azure Container
Apps. This is the **verified, no-Docker path** using Azure Cloud Shell and
`az acr build`. It updates the existing live apps in place — it does **not**
create new infrastructure.

> Written after the 2026-07-29 deployment. If the resource names ever change,
> re-run the discovery commands in the "Finding your resources" section.

---

## What you're deploying

| Piece | Value |
|---|---|
| Subscription | **Petra AI Tools** (`56dff2f4-6710-41f1-bce1-b98e6bff985f`) |
| Resource group | **PET-RG-03** |
| Container registry (ACR) | **petraaitools** (`petraaitools-aebca3bgcnavanh8.azurecr.io`) |
| Backend app | **ai-audit-tool** — runs image `petra-api` |
| Frontend app | **ai-audit-tool-frontend** — runs image `petra-frontend` |

> Note the naming: the **app resources** are `ai-audit-tool*`, but the **images**
> they pull are named `petra-api` / `petra-frontend`. Don't confuse these.
> `petra-leadership` / `petra-leadership-api` in the same resource group are a
> **different application** — leave them alone.

---

## Why this method (and why not Docker / `azd up`)

- **No local Docker needed.** `az acr build` uploads the code and builds the
  image *inside* Azure Container Registry. This works from Azure Cloud Shell,
  which has no Docker daemon, and from any locked-down laptop.
- **No risk of duplicate infrastructure.** The repo's `azd`/Bicep config would
  create apps named `petra-api` / `petra-frontend`. Our live apps are named
  `ai-audit-tool*`. Running `azd up` with a fresh environment could therefore
  spin up *parallel* apps instead of updating the running ones. The
  build-and-update method below always targets the existing apps.
- **Nothing else is touched.** Environment variables, secrets, authentication
  (Entra), and the frontend runtime config all carry over automatically into
  the new revision. This method only swaps the container image.

---

## Prerequisites

1. **Azure Cloud Shell** — open <https://shell.azure.com> (or the `>_` icon in
   the Azure Portal). Everything needed (`az`, `git`) is pre-installed and you're
   already signed in. No installs required.
2. **Be on the correct subscription.** Cloud Shell often defaults to the wrong
   one. Check and switch:
   ```bash
   az account show --query name -o tsv          # what am I on?
   az account set --subscription "Petra AI Tools"
   ```

---

## Deploy — step by step

Run these in Cloud Shell. Examples use **Bash** (click the shell dropdown and
pick *Bash* if you're in PowerShell — it avoids quoting headaches). Commands are
one per line; don't chain with `&&` in PowerShell.

### 1. Get the latest code

```bash
cd ~/clouddrive
git clone https://github.com/le-marais-advisory/Petra-AI-Audit-Tool.git   # first time only
cd Petra-AI-Audit-Tool
git checkout main
git log --oneline -3        # confirm you see the latest commit on main
```

- A fresh `git clone` already contains the latest `main` — **you do not need
  `git pull` after cloning.** `git pull` only matters for updating an
  *existing* clone.
- If `git` asks for a password: use your **GitHub username** (not your email)
  and a **Personal Access Token** (not your account password). The token must be
  authorized for the `le-marais-advisory` org's SAML SSO. But usually you can
  skip this — a fresh clone needs no auth prompt for a repo you can access.

### 2. Pick a version tag

Use a fresh tag each deploy so the new revision definitely picks up the new
image. Convention: `vYYYYMMDD` (add a letter if you deploy twice in a day).

```
Example for 2026-07-29:  v20260729
```

Replace `v20260729` below with your date.

### 3. Build the two images in ACR (no Docker)

**Backend** — run from the repo root:
```bash
az acr build --registry petraaitools \
  --image petra-api:v20260729 --image petra-api:latest \
  --file docker/AppProdDockerfile .
```

**Frontend** — the build context must be the `frontend` folder, so `cd` into it:
```bash
cd frontend
az acr build --registry petraaitools \
  --image petra-frontend:v20260729 --image petra-frontend:latest \
  --file docker/AppDevProdDockerfile .
cd ..
```

Each build streams logs and ends with a success line. Both tag the image with
your dated tag **and** refresh `latest`.

> Why `cd frontend`? The frontend Dockerfile does `COPY docker/...`, which only
> resolves when `frontend` is the build context. Building from the repo root
> fails with "Unable to find 'docker/AppDevProdDockerfile'".

### 4. Point the live apps at the new images

```bash
az containerapp update --name ai-audit-tool --resource-group PET-RG-03 \
  --image petraaitools-aebca3bgcnavanh8.azurecr.io/petra-api:v20260729
```
```bash
az containerapp update --name ai-audit-tool-frontend --resource-group PET-RG-03 \
  --image petraaitools-aebca3bgcnavanh8.azurecr.io/petra-frontend:v20260729
```

Each creates a new revision running the new image.

### 5. Verify

```bash
# New revision active and running the right image?
az containerapp revision list --name ai-audit-tool --resource-group PET-RG-03 \
  --query "[].{revision:name, active:properties.active, created:properties.createdTime, image:properties.template.containers[0].image}" -o table

# Frontend URL — open it in a browser
az containerapp show --name ai-audit-tool-frontend --resource-group PET-RG-03 \
  --query "properties.configuration.ingress.fqdn" -o tsv
```

Open the printed URL and confirm the new build loads and behaves as expected.

---

## Finding your resources (if names ever change)

```bash
# All container apps in the subscription + the image each runs
az containerapp list --query "[].{name:name, rg:resourceGroup, image:properties.template.containers[0].image}" -o table

# The container registry
az acr list --query "[].{name:name, rg:resourceGroup, server:loginServer}" -o table
```

---

## Rolling back

If a deploy misbehaves, point the app back at the previous image tag (or a prior
revision):

```bash
# Roll the image back to a known-good tag
az containerapp update --name ai-audit-tool --resource-group PET-RG-03 \
  --image petraaitools-aebca3bgcnavanh8.azurecr.io/petra-api:<previous-tag>

# Or list revisions and reactivate an older one
az containerapp revision list --name ai-audit-tool --resource-group PET-RG-03 -o table
az containerapp revision activate --revision <old-revision-name> \
  --name ai-audit-tool --resource-group PET-RG-03
```

---

## Gotchas we hit (so you don't again)

- **Wrong subscription.** Cloud Shell defaulted to *Leadership Dashboard*; the
  app is in *Petra AI Tools*. Symptom: "not registered for the Microsoft.App
  resource provider". Fix: `az account set --subscription "Petra AI Tools"`.
- **PowerShell vs Bash quoting.** In Cloud Shell PowerShell, `--query` strings
  with escaped quotes and `&&` chaining fail. Switch to Bash (`bash`) for these
  commands, or run one command per line.
- **`git pull` 403 "write access not granted".** Irrelevant if you just cloned —
  a fresh clone is already current, no pull needed. If you truly need to pull a
  private repo, use a PAT (as password, with your GitHub *username*) authorized
  for the org's SSO.
- **Frontend build "Unable to find Dockerfile".** `cd frontend` before the
  frontend `az acr build` (see step 3).
- **Don't touch `petra-leadership*`.** Different app, same resource group.

---

## Alternative: the `azd` path (for reference)

The repo also supports `azd` (see `DEPLOYMENT_GUIDE.md`). `azure.yaml` now has
`remoteBuild: true`, so `azd up` / `azd deploy` can build without local Docker.
**However**, because the live app names (`ai-audit-tool*`) differ from the Bicep
defaults (`petra-*`), using `azd` safely requires reproducing the original
environment name and parameter overrides so it re-associates with the existing
resources rather than creating new ones. Until that's confirmed, the
`az acr build` + `az containerapp update` method above is the safer choice for a
routine redeploy.
