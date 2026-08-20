declare global {
  interface Window {
    __APP_CONFIG__?: Record<string, string | undefined>;
  }
}

function normalizeValue(value: unknown): string | undefined {
  if (value === undefined || value === null) {
    return undefined;
  }

  const normalized = String(value).trim();
  return normalized ? normalized : undefined;
}

export function readEnv(name: string): string | undefined {
  const runtimeValue = normalizeValue(window.__APP_CONFIG__?.[name]);
  if (runtimeValue) {
    return runtimeValue;
  }

  return normalizeValue(import.meta.env[name]);
}


export function readBooleanEnv(name: string, defaultValue: boolean): boolean {
  const value = readEnv(name);
  if (value === undefined) {
    return defaultValue;
  }
  return !["0", "false", "no", "off"].includes(value.toLowerCase());
}
