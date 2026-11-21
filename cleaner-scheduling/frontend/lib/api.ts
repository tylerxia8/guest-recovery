export type ApiError = Error & { status: number; body: { error?: { code?: string; message?: string } } | null };

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`/api${path}`, { ...init, signal: init.signal ?? AbortSignal.timeout(20000), headers, credentials: "same-origin" });
  const text = await response.text();
  let body: ApiError["body"] = null;
  if (text) {
    try {
      body = JSON.parse(text) as ApiError["body"];
    } catch {
      body = { error: { code: "parse_error", message: `${response.status} ${response.statusText}: ${text.slice(0, 200)}` } };
    }
  }
  if (!response.ok) {
    if (response.status === 401 && typeof window !== "undefined") window.dispatchEvent(new Event("session-expired"));
    const error = new Error(body?.error?.message ?? `${response.status} ${response.statusText}`) as ApiError;
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body as T;
}
