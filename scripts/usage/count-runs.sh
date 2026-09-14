#!/usr/bin/env bash
#
# Count how many times the audit tool ran a validation over a time window, by
# counting the "Pipeline start:" log line the backend emits on every run
# (src/services/validation_job_service.py). Reads the Container App's logs from
# its Log Analytics workspace. Run from Azure Cloud Shell (bash) or anywhere
# with the Azure CLI logged in.
#
#   ./scripts/usage/count-runs.sh          # last 7 days (default)
#   ./scripts/usage/count-runs.sh 1        # last 24 hours
#   ./scripts/usage/count-runs.sh 30       # last 30 days
#
# Override targets via env vars (see the block below), e.g. a different app.
#
set -euo pipefail

SUBSCRIPTION="${SUBSCRIPTION:-Petra AI Tools}"
RESOURCE_GROUP="${RESOURCE_GROUP:-PET-RG-03}"
BACKEND_APP="${BACKEND_APP:-ai-audit-tool}"
DAYS="${1:-${DAYS:-7}}"
MARKER="${MARKER:-Pipeline start}"

# Log Analytics schema (override only if your workspace uses resource-specific
# tables instead of the default custom-log table).
TABLE="${TABLE:-ContainerAppConsoleLogs_CL}"
MSG_COL="${MSG_COL:-Log_s}"
APP_COL="${APP_COL:-ContainerAppName_s}"

die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
command -v az >/dev/null 2>&1 || die "'az' (Azure CLI) is not installed / on PATH."

# The KQL query needs the log-analytics CLI extension.
az extension add --name log-analytics --only-show-errors >/dev/null 2>&1 || true

az account set --subscription "${SUBSCRIPTION}" \
  || die "Could not select subscription '${SUBSCRIPTION}'."

# Resolve the Log Analytics workspace GUID from the Container App's environment.
ENV_ID="$(az containerapp show -n "${BACKEND_APP}" -g "${RESOURCE_GROUP}" \
  --query properties.environmentId -o tsv 2>/dev/null)" \
  || die "Could not find container app '${BACKEND_APP}' in '${RESOURCE_GROUP}'."
[ -n "${ENV_ID}" ] || die "Container app '${BACKEND_APP}' has no environment id."

WORKSPACE_GUID="$(az containerapp env show --ids "${ENV_ID}" \
  --query properties.appLogsConfiguration.logAnalyticsConfiguration.customerId -o tsv 2>/dev/null)" \
  || die "Could not read the Log Analytics workspace from the environment."
[ -n "${WORKSPACE_GUID}" ] \
  || die "This environment does not ship logs to Log Analytics (no workspace configured)."

base_query="${TABLE}
| where TimeGenerated > ago(${DAYS}d)
| where ${APP_COL} == '${BACKEND_APP}'
| where ${MSG_COL} has '${MARKER}'"

total="$(az monitor log-analytics query -w "${WORKSPACE_GUID}" \
  --analytics-query "${base_query} | summarize Runs = count()" \
  -o tsv 2>/dev/null | tr -d '[:space:]')"
total="${total:-0}"

printf '\nRuns of %s in the last %s day(s): %s\n\n' "${BACKEND_APP}" "${DAYS}" "${total}"

if [ "${total}" != "0" ]; then
  echo "By day:"
  az monitor log-analytics query -w "${WORKSPACE_GUID}" \
    --analytics-query "${base_query}
| summarize Runs = count() by Day = format_datetime(bin(TimeGenerated, 1d), 'yyyy-MM-dd')
| order by Day asc" \
    -o table
fi
