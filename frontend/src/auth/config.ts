import { BrowserCacheLocation, type Configuration, type PopupRequest } from "@azure/msal-browser";

import { readBooleanEnv, readEnv } from "@/config/runtime";

function readRequiredEnv(name: string): string {
  const value = readEnv(name);
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}


export const authEnabled = readBooleanEnv("VITE_AUTH_ENABLED", true);
export const azureTenantId = authEnabled ? readRequiredEnv("VITE_AZURE_TENANT_ID") : "common";
export const azureApiScope = authEnabled ? readRequiredEnv("VITE_API_SCOPE") : "";
export const azurePostLogoutRedirectUri =
  readEnv("VITE_AZURE_POST_LOGOUT_REDIRECT_URI") || window.location.origin;

export const msalConfig: Configuration = {
  auth: {
    clientId: authEnabled ? readRequiredEnv("VITE_AZURE_CLIENT_ID") : "00000000-0000-0000-0000-000000000000",
    authority: readEnv("VITE_AZURE_AUTHORITY") || `https://login.microsoftonline.com/${azureTenantId}`,
    redirectUri: readEnv("VITE_AZURE_REDIRECT_URI") || window.location.origin,
    postLogoutRedirectUri: azurePostLogoutRedirectUri,
    navigateToLoginRequestUrl: false,
  },
  cache: {
    cacheLocation: BrowserCacheLocation.SessionStorage,
  },
};

export const loginRequest: PopupRequest = {
  scopes: authEnabled ? ["openid", "profile", "email", azureApiScope] : ["openid", "profile", "email"],
  prompt: "select_account",
};

export const apiTokenRequest: PopupRequest = {
  scopes: authEnabled ? [azureApiScope] : [],
};
