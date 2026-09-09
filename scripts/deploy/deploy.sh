#!/usr/bin/env bash
#
# Deploy Petra Vision to Azure Container Apps.
#
# Builds the container image(s) remotely in ACR (no local Docker needed — works
# from Azure Cloud Shell) and rolls the running Container App(s) onto a fresh,
# uniquely-tagged image. Intended to be run from Azure Cloud Shell (bash).
#
#   ./scripts/deploy/deploy.sh backend    # API only
#   ./scripts/deploy/deploy.sh frontend   # SPA only
#   ./scripts/deploy/deploy.sh all        # both (default)
#
# Override any target/identifier with env vars, e.g.:
#   SUBSCRIPTION="Petra AI Tools" RESOURCE_GROUP=PET-RG-03 ./scripts/deploy/deploy.sh backend
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration (override via environment variables)
# ---------------------------------------------------------------------------
SUBSCRIPTION="${SUBSCRIPTION:-Petra AI Tools}"
RESOURCE_GROUP="${RESOURCE_GROUP:-PET-RG-03}"
ACR_NAME="${ACR_NAME:-petraaitools}"
ACR_SERVER="${ACR_SERVER:-petraaitools-aebca3bgcnavanh8.azurecr.io}"

BACKEND_APP="${BACKEND_APP:-ai-audit-tool}"
BACKEND_IMAGE="${BACKEND_IMAGE:-petra-api}"
BACKEND_DIR="${BACKEND_DIR:-.}"                       # build context (repo root)
BACKEND_DOCKERFILE="${BACKEND_DOCKERFILE:-docker/AppProdDockerfile}"

FRONTEND_APP="${FRONTEND_APP:-ai-audit-tool-frontend}"
FRONTEND_IMAGE="${FRONTEND_IMAGE:-petra-frontend}"
FRONTEND_DIR="${FRONTEND_DIR:-frontend}"             # build context (frontend/)
FRONTEND_DOCKERFILE="${FRONTEND_DOCKERFILE:-docker/AppDevProdDockerfile}"

# Unique, sortable tag for every run. A fresh tag guarantees the Container App
# rolls to a genuinely new revision (re-using a tag can silently no-op).
TAG="${TAG:-v$(date -u +%Y%m%d-%H%M%S)}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
die()  { printf '\n\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

require() { command -v "$1" >/dev/null 2>&1 || die "'$1' is not installed / on PATH."; }

set_subscription() {
  log "Subscription: ${SUBSCRIPTION}"
  az account set --subscription "${SUBSCRIPTION}" \
    || die "Could not select subscription '${SUBSCRIPTION}'. Run 'az account list -o table' to see options."
}

# deploy_service <label> <image-repo> <build-dir> <dockerfile> <app-name>
deploy_service() {
  local label="$1" image="$2" build_dir="$3" dockerfile="$4" app="$5"
  local ctx="${REPO_ROOT}/${build_dir}"

  [ -f "${ctx}/${dockerfile}" ] \
    || die "${label}: '${dockerfile}' not found under '${build_dir}/'. Are you on the right commit?"

  log "${label}: building ${image}:${TAG} in ACR '${ACR_NAME}' (remote build, no local Docker)"
  ( cd "${ctx}" && az acr build \
      --registry "${ACR_NAME}" \
      --image "${image}:${TAG}" \
      --image "${image}:latest" \
      --file "${dockerfile}" \
      . ) || die "${label}: ACR build failed — NOT updating the app."

  log "${label}: pointing '${app}' at ${image}:${TAG}"
  az containerapp update \
    --name "${app}" \
    --resource-group "${RESOURCE_GROUP}" \
    --image "${ACR_SERVER}/${image}:${TAG}" \
    --output none \
    || die "${label}: container app update failed."

  wait_ready "${label}" "${app}"
}

# wait_ready <label> <app-name> — poll until the newest revision is the ready one.
wait_ready() {
  local label="$1" app="$2" ready latest i
  log "${label}: waiting for the new revision to become ready..."
  for i in $(seq 1 20); do
    ready="$(az containerapp show -n "${app}" -g "${RESOURCE_GROUP}" --query properties.latestReadyRevisionName -o tsv 2>/dev/null || true)"
    latest="$(az containerapp show -n "${app}" -g "${RESOURCE_GROUP}" --query properties.latestRevisionName -o tsv 2>/dev/null || true)"
    if [ -n "${ready}" ] && [ "${ready}" = "${latest}" ]; then
      log "${label}: live on ${ready}"
      return 0
    fi
    printf '    ready=%s latest=%s (retry %s/20)\n' "${ready:-?}" "${latest:-?}" "${i}"
    sleep 12
  done
  printf '\n\033[1;33mWARNING:\033[0m %s: revision did not report ready within timeout.\n' "${label}"
  printf '    Check: az containerapp revision list -n %s -g %s -o table\n' "${app}" "${RESOURCE_GROUP}"
}

show_url() {
  local app="$1" fqdn
  fqdn="$(az containerapp show -n "${app}" -g "${RESOURCE_GROUP}" --query properties.configuration.ingress.fqdn -o tsv 2>/dev/null || true)"
  [ -n "${fqdn}" ] && log "${app} URL: https://${fqdn}"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
TARGET="${1:-all}"
require az

log "Repo:   ${REPO_ROOT}"
log "Tag:    ${TAG}"
log "Target: ${TARGET}"
set_subscription

case "${TARGET}" in
  backend)
    deploy_service "backend" "${BACKEND_IMAGE}" "${BACKEND_DIR}" "${BACKEND_DOCKERFILE}" "${BACKEND_APP}"
    show_url "${BACKEND_APP}"
    ;;
  frontend)
    deploy_service "frontend" "${FRONTEND_IMAGE}" "${FRONTEND_DIR}" "${FRONTEND_DOCKERFILE}" "${FRONTEND_APP}"
    show_url "${FRONTEND_APP}"
    ;;
  all)
    deploy_service "backend"  "${BACKEND_IMAGE}"  "${BACKEND_DIR}"  "${BACKEND_DOCKERFILE}"  "${BACKEND_APP}"
    deploy_service "frontend" "${FRONTEND_IMAGE}" "${FRONTEND_DIR}" "${FRONTEND_DOCKERFILE}" "${FRONTEND_APP}"
    show_url "${BACKEND_APP}"
    show_url "${FRONTEND_APP}"
    ;;
  *)
    die "Unknown target '${TARGET}'. Use: backend | frontend | all"
    ;;
esac

log "Done."
