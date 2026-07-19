export const OPENAI_KEY_SLOT = "kyn.openai.api-key.v1";

export class ApiError extends Error {
  constructor(status, payload) {
    const error = payload?.error ?? {};
    super(error.message || `The runtime returned HTTP ${status}.`);
    this.name = "ApiError";
    this.status = status;
    this.code = error.code || "request_failed";
    this.detail = error.detail ?? null;
  }
}

export function browserKey() {
  try {
    return sessionStorage.getItem(OPENAI_KEY_SLOT) ?? "";
  } catch {
    return "";
  }
}

export function saveBrowserKey(value) {
  const normalized = value.trim();
  if (normalized) sessionStorage.setItem(OPENAI_KEY_SLOT, normalized);
  else sessionStorage.removeItem(OPENAI_KEY_SLOT);
}

export async function api(
  path,
  { method = "GET", body, keyMode = "none", signal } = {}
) {
  if (!path.startsWith("/api/v1/")) {
    throw new Error("The browser client permits only same-origin /api/v1 requests.");
  }
  const headers = { Accept: "application/json" };
  const key = browserKey();
  if (keyMode === "required" && !key) {
    throw new ApiError(401, {
      error: {
        code: "openai_key_required",
        message: "Add your OpenAI API key in Settings before starting this model-backed operation."
      }
    });
  }
  if (key && keyMode !== "none") headers["X-OpenAI-API-Key"] = key;
  const options = {
    method,
    credentials: "same-origin",
    headers,
    signal
  };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError(response.status, {
      error: {
        code: "invalid_response",
        message: "The runtime returned a response that was not JSON."
      }
    });
  }
  if (!response.ok) throw new ApiError(response.status, payload);
  return payload.data;
}

export async function health() {
  const response = await fetch("/healthz", {
    credentials: "same-origin",
    headers: { Accept: "application/json" }
  });
  if (!response.ok) throw new Error("Runtime health check failed.");
  return response.json();
}

export function commandId(prefix = "browser") {
  const id = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
  return `${prefix}:${id}`;
}
