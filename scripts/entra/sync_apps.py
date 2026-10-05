from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.parse
import uuid
from dataclasses import dataclass
from typing import Any


GRAPH_ROOT = "https://graph.microsoft.com/v1.0"


class CommandError(RuntimeError):
    pass


@dataclass(frozen=True)
class AppRegistration:
    object_id: str
    app_id: str
    display_name: str
    data: dict[str, Any]


def run_command(command: list[str], *, json_output: bool = False, allow_failure: bool = False) -> Any:
    env = os.environ.copy()
    env.setdefault("AZURE_CORE_ONLY_SHOW_ERRORS", "1")
    process = subprocess.run(command, capture_output=True, text=True, env=env)

    if process.returncode != 0:
        if allow_failure:
            return None
        raise CommandError(process.stderr.strip() or process.stdout.strip() or f"Command failed: {' '.join(command)}")

    if not json_output:
        return process.stdout.strip()

    stdout = process.stdout.strip()
    return json.loads(stdout) if stdout else {}


def az(*args: str, json_output: bool = False, allow_failure: bool = False) -> Any:
    command = ["az", *args]
    if json_output:
        command.extend(["-o", "json"])
    return run_command(command, json_output=json_output, allow_failure=allow_failure)


def graph(method: str, path: str, *, body: dict[str, Any] | None = None, allow_failure: bool = False) -> Any:
    command = ["az", "rest", "--method", method, "--url", f"{GRAPH_ROOT}{path}"]
    if body is not None:
        command.extend(["--headers", "Content-Type=application/json", "--body", json.dumps(body)])
    return run_command(command, json_output=True, allow_failure=allow_failure)


def shell_quote_filter_value(value: str) -> str:
    return value.replace("'", "''")


def normalize_origin(uri: str) -> str:
    uri = uri.strip()
    if not uri:
        return uri

    parsed = urllib.parse.urlparse(uri)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Unsupported redirect URI '{uri}'. Expected a full origin like https://example.com.")

    return f"{parsed.scheme}://{parsed.netloc}"


def get_environment_name() -> str:
    return os.environ.get("AZURE_ENV_NAME") or os.environ.get("AZD_ENVIRONMENT_NAME") or "dev"


def get_display_name(kind: str) -> str:
    env_name = get_environment_name()
    custom_name = os.environ.get(f"ENTRA_{kind.upper()}_APP_NAME")
    if custom_name:
        return custom_name

    label = "Frontend" if kind == "frontend" else "API"
    return f"Petra Vision {label} ({env_name})"


def get_tenant_id() -> str:
    return az("account", "show", "--query", "tenantId", "-o", "tsv")


def get_signed_in_user_object_id() -> str | None:
    response = graph("GET", "/me?$select=id,userPrincipalName", allow_failure=True)
    if not response:
        return None
    return str(response.get("id") or "").strip() or None


def find_application(display_name: str) -> AppRegistration | None:
    query = urllib.parse.quote(
        f"$filter=displayName eq '{shell_quote_filter_value(display_name)}'&$select=id,appId,displayName,api,spa,signInAudience,identifierUris,requiredResourceAccess",
        safe="=$,'()&",
    )
    response = graph("GET", f"/applications?{query}")
    items = response.get("value", [])
    if not items:
        return None
    if len(items) > 1:
        raise CommandError(f"Found multiple app registrations with display name '{display_name}'.")

    app = items[0]
    return AppRegistration(
        object_id=str(app["id"]),
        app_id=str(app["appId"]),
        display_name=str(app["displayName"]),
        data=app,
    )


def create_application(display_name: str) -> AppRegistration:
    created = graph(
        "POST",
        "/applications",
        body={
            "displayName": display_name,
            "signInAudience": "AzureADMyOrg",
        },
    )
    return AppRegistration(
        object_id=str(created["id"]),
        app_id=str(created["appId"]),
        display_name=str(created["displayName"]),
        data=created,
    )


def get_or_create_application(display_name: str) -> AppRegistration:
    return find_application(display_name) or create_application(display_name)


def patch_application(object_id: str, body: dict[str, Any]) -> None:
    graph("PATCH", f"/applications/{object_id}", body=body)


def ensure_service_principal(app_id: str) -> None:
    query = urllib.parse.quote(f"$filter=appId eq '{app_id}'&$select=id", safe="=$'&")
    response = graph("GET", f"/servicePrincipals?{query}")
    if response.get("value"):
        return
    graph("POST", "/servicePrincipals", body={"appId": app_id})


def ensure_owner(application_object_id: str, owner_object_id: str | None) -> None:
    if not owner_object_id:
        return

    response = graph(
        "POST",
        f"/applications/{application_object_id}/owners/$ref",
        body={
            "@odata.id": f"{GRAPH_ROOT}/directoryObjects/{owner_object_id}",
        },
        allow_failure=True,
    )
    if response is None:
        return


def get_scope_definition(existing_app: AppRegistration, scope_name: str) -> dict[str, Any]:
    existing_scopes = existing_app.data.get("api", {}).get("oauth2PermissionScopes", [])
    current_scope = next((scope for scope in existing_scopes if scope.get("value") == scope_name), None)
    scope_id = current_scope.get("id") if current_scope else str(uuid.uuid4())

    return {
        "adminConsentDescription": "Allows the Petra Vision frontend to call the protected API on behalf of the signed-in user.",
        "adminConsentDisplayName": "Access Petra Vision API",
        "id": scope_id,
        "isEnabled": True,
        "type": "User",
        "userConsentDescription": "Allow Petra Vision to call the protected API on your behalf.",
        "userConsentDisplayName": "Access Petra Vision API",
        "value": scope_name,
    }


def ensure_api_registration(scope_name: str, owner_object_id: str | None) -> AppRegistration:
    api_app = get_or_create_application(get_display_name("backend"))
    scope = get_scope_definition(api_app, scope_name)

    patch_application(
        api_app.object_id,
        {
            "signInAudience": "AzureADMyOrg",
            "identifierUris": [f"api://{api_app.app_id}"],
            "api": {
                "requestedAccessTokenVersion": 2,
                "oauth2PermissionScopes": [scope],
            },
        },
    )

    api_app = find_application(api_app.display_name) or api_app
    ensure_service_principal(api_app.app_id)
    ensure_owner(api_app.object_id, owner_object_id)
    return api_app


def merge_resource_access(existing: list[dict[str, Any]], resource_app_id: str, scope_id: str) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    found = False

    for item in existing:
        if item.get("resourceAppId") != resource_app_id:
            merged.append(item)
            continue

        found = True
        resource_access = item.get("resourceAccess", [])
        if not any(access.get("id") == scope_id for access in resource_access):
            resource_access = [*resource_access, {"id": scope_id, "type": "Scope"}]

        merged.append(
            {
                "resourceAppId": resource_app_id,
                "resourceAccess": resource_access,
            }
        )

    if not found:
        merged.append(
            {
                "resourceAppId": resource_app_id,
                "resourceAccess": [
                    {
                        "id": scope_id,
                        "type": "Scope",
                    }
                ],
            }
        )

    return merged


def ensure_frontend_registration(
    api_app: AppRegistration,
    scope_name: str,
    owner_object_id: str | None,
    redirect_uris: list[str],
) -> AppRegistration:
    frontend_app = get_or_create_application(get_display_name("frontend"))
    existing_required_access = frontend_app.data.get("requiredResourceAccess", [])
    api_scope = get_scope_definition(api_app, scope_name)
    existing_redirects = frontend_app.data.get("spa", {}).get("redirectUris", [])
    merged_redirects = []
    for redirect_uri in [*existing_redirects, *redirect_uris]:
        normalized = normalize_origin(redirect_uri)
        if normalized not in merged_redirects:
            merged_redirects.append(normalized)

    patch_application(
        frontend_app.object_id,
        {
            "signInAudience": "AzureADMyOrg",
            "spa": {
                "redirectUris": merged_redirects,
            },
            "requiredResourceAccess": merge_resource_access(existing_required_access, api_app.app_id, api_scope["id"]),
        },
    )

    frontend_app = find_application(frontend_app.display_name) or frontend_app
    ensure_service_principal(frontend_app.app_id)
    ensure_owner(frontend_app.object_id, owner_object_id)
    return frontend_app


def get_frontend_endpoint_from_azd() -> str | None:
    direct_value = os.environ.get("FRONTEND_ENDPOINT") or os.environ.get("SERVICE_FRONTEND_ENDPOINT")
    if direct_value:
        return normalize_origin(direct_value)

    values = load_azd_environment_values()
    frontend_endpoint = values.get("FRONTEND_ENDPOINT")
    if frontend_endpoint:
        return normalize_origin(frontend_endpoint)

    return None


def load_azd_environment_values() -> dict[str, str]:
    raw_values = run_command(["azd", "env", "get-values"], json_output=False, allow_failure=True)
    if not raw_values:
        return {}

    parsed: dict[str, str] = {}

    for raw_line in raw_values.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        normalized_key = key.strip()
        normalized_value = value.strip().strip('"').strip("'")
        if normalized_key and normalized_value:
            parsed[normalized_key] = normalized_value

    return parsed


def normalize_env_value(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = value.strip()
    return normalized or None


def parse_bool_env(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def ensure_environment_defaults(current_values: dict[str, str]) -> dict[str, str]:
    defaults = {
        "APP_NAME": "Petra Vision",
        "API_PREFIX": "/api/v1",
        "AUTH_ENABLED": "true",
    }

    updated_values = dict(current_values)
    for key, default_value in defaults.items():
        existing_value = normalize_env_value(os.environ.get(key)) or updated_values.get(key)
        if existing_value is not None:
            continue
        run_command(["azd", "env", "set", key, default_value], json_output=False)
        updated_values[key] = default_value

    return updated_values


def sync_azd_environment(tenant_id: str, api_app: AppRegistration, frontend_app: AppRegistration, scope_name: str) -> None:
    azd_env_values = ensure_environment_defaults(load_azd_environment_values())
    auth_enabled = parse_bool_env(normalize_env_value(os.environ.get("AUTH_ENABLED")) or azd_env_values.get("AUTH_ENABLED"), default=True)

    values = {
        "AZURE_TENANT_ID": tenant_id,
        "AZURE_CLIENT_ID": api_app.app_id,
        "AZURE_FRONTEND_CLIENT_ID": frontend_app.app_id,
        "AZURE_AUDIENCE": f"api://{api_app.app_id}",
        "AZURE_REQUIRED_SCOPE": scope_name,
        "AZURE_ALLOWED_CLIENT_APP_IDS": frontend_app.app_id,
        "VITE_AUTH_ENABLED": "true" if auth_enabled else "false",
        "VITE_AZURE_CLIENT_ID": frontend_app.app_id,
        "VITE_AZURE_TENANT_ID": tenant_id,
        "VITE_AZURE_AUTHORITY": f"https://login.microsoftonline.com/{tenant_id}",
        "VITE_API_SCOPE": f"api://{api_app.app_id}/{scope_name}",
        "ENTRA_BACKEND_APP_ID": api_app.app_id,
        "ENTRA_FRONTEND_APP_ID": frontend_app.app_id,
        "ENTRA_BACKEND_APP_NAME": api_app.display_name,
        "ENTRA_FRONTEND_APP_NAME": frontend_app.display_name,
    }

    for key, value in values.items():
        run_command(["azd", "env", "set", key, value], json_output=False)


def build_redirect_uri_list(mode: str, local_frontend_uri: str) -> list[str]:
    redirect_uris = [normalize_origin(local_frontend_uri)]
    if mode != "postprovision":
        return redirect_uris

    deployed_frontend_endpoint = get_frontend_endpoint_from_azd()
    if deployed_frontend_endpoint and deployed_frontend_endpoint not in redirect_uris:
        redirect_uris.append(deployed_frontend_endpoint)
    return redirect_uris


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or update Microsoft Entra app registrations for Petra Vision.")
    parser.add_argument("--mode", choices=["preprovision", "postprovision", "manual"], default="manual")
    parser.add_argument("--scope-name", default=os.environ.get("AZURE_REQUIRED_SCOPE", "access_as_user"))
    parser.add_argument("--local-frontend-uri", default=os.environ.get("LOCAL_FRONTEND_URI", "http://localhost:5173"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    tenant_id = get_tenant_id()
    owner_object_id = get_signed_in_user_object_id()
    redirect_uris = build_redirect_uri_list(args.mode, args.local_frontend_uri)

    api_app = ensure_api_registration(args.scope_name, owner_object_id)
    frontend_app = ensure_frontend_registration(api_app, args.scope_name, owner_object_id, redirect_uris)
    sync_azd_environment(tenant_id, api_app, frontend_app, args.scope_name)

    deployed_redirect_note = ""
    if len(redirect_uris) > 1:
        deployed_redirect_note = f"\n  deployed redirect URI: {redirect_uris[-1]}"

    owner_note = owner_object_id or "unavailable for the current Azure principal"
    print(
        f"Configured Microsoft Entra applications for tenant {tenant_id}."
        f"\n  frontend app: {frontend_app.display_name} ({frontend_app.app_id})"
        f"\n  backend app: {api_app.display_name} ({api_app.app_id})"
        f"\n  API scope: api://{api_app.app_id}/{args.scope_name}"
        f"\n  local redirect URI: {redirect_uris[0]}"
        f"{deployed_redirect_note}"
        f"\n  owner object ID: {owner_note}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CommandError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
