import type { NextRequest } from "next/server";

export function requestOrigin(request: NextRequest): string | null {
  const host = request.headers.get("host");
  if (!host) return null;
  try {
    const incoming = new URL(`${request.nextUrl.protocol}//${host}`);
    if (incoming.host !== host) return null;
    const configured = process.env.CLEANER_PORTAL_BASE_URL;
    if (configured) {
      const portal = new URL(configured);
      if (portal.username || portal.password || !["http:", "https:"].includes(portal.protocol)) return null;
      const localPortal = ["localhost", "127.0.0.1"].includes(portal.hostname);
      if (!localPortal && portal.protocol !== "https:") return null;
      if (host === portal.host) return portal.origin;
      if (!localPortal && (process.env.WORKSPACE_SIGNUP_ENABLED === "true" || !["localhost", "127.0.0.1"].includes(incoming.hostname))) return null;
    }
    if (["localhost", "127.0.0.1"].includes(incoming.hostname)) return incoming.origin;
    if (process.env.WORKSPACE_SIGNUP_ENABLED === "true" || process.env.HOSPITABLE_READ_ONLY === "true") return null;
    return incoming.origin;
  } catch {
    return null;
  }
}

export function apiOrigin(origin: string): string | null {
  if (process.env.CLEANING_API_ORIGIN) return process.env.CLEANING_API_ORIGIN;
  return ["localhost", "127.0.0.1"].includes(new URL(origin).hostname) ? "http://127.0.0.1:8000" : null;
}
