#!/bin/sh

set -eu

template_path="/usr/share/nginx/html/runtime-config.template.js"
target_path="/usr/share/nginx/html/runtime-config.js"

envsubst '${VITE_APP_NAME} ${VITE_API_BASE_URL} ${VITE_API_PREFIX} ${VITE_AUTH_ENABLED} ${VITE_SHOW_RULES_SIDEBAR} ${VITE_AZURE_CLIENT_ID} ${VITE_AZURE_TENANT_ID} ${VITE_AZURE_AUTHORITY} ${VITE_AZURE_REDIRECT_URI} ${VITE_AZURE_POST_LOGOUT_REDIRECT_URI} ${VITE_API_SCOPE}' \
  < "$template_path" \
  > "$target_path"

exec nginx -g 'daemon off;'
