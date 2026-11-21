import { proxyIdentityHeaders } from "./lib/proxy-identity";
import { apiOrigin, requestOrigin } from "./lib/request-boundary";
import { workspaceRegistrationEnabled, workspaceInviteSignupEnabled } from "./lib/session";
import { NextRequest, NextResponse } from "next/server";

const PUBLIC_PATHS = [
  "/health",
  "/brand/turnover.png",
  "/brand/turnover-mark.svg",
  "/brand/icon-192.png",
  "/brand/icon-512.png",
  "/brand/icon-maskable-512.png",
  "/brand/apple-touch-icon.png",
  "/favicon.svg",
  "/favicon-32.png",
  "/favicon-16.png",
  "/manifest.webmanifest",
  "/fonts/Inter-Variable.woff2",
  "/illustrations/G5-empty-dashboard-light.svg",
  "/illustrations/G5-empty-dashboard-light@2x.png",
  "/illustrations/G6-empty-cleaners-light.svg",
  "/illustrations/G6-empty-cleaners-light@2x.png",
  "/illustrations/G7-empty-houses-light.svg",
  "/illustrations/G7-empty-houses-light@2x.png",
  "/illustrations/G8-empty-exceptions-light.svg",
  "/illustrations/G8-empty-exceptions-light@2x.png",
  "/illustrations/G9-portal-no-jobs-light.svg",
  "/illustrations/G9-portal-no-jobs-light@2x.png",
  "/illustrations/G10-onboarding-welcome-light.svg",
  "/illustrations/G10-onboarding-welcome-light@2x.png",
  "/illustrations/G11-manager-first-run-light.svg",
  "/illustrations/G11-manager-first-run-light@2x.png",
  "/illustrations/G12-success-light.svg",
  "/illustrations/G12-success-light@2x.png",
];

async function workspaceAccess(request: NextRequest, requestHeaders: Headers) {
  if (PUBLIC_PATHS.includes(request.nextUrl.pathname)) return NextResponse.next({request: {headers: requestHeaders}});
  if (process.env.WORKSPACE_SIGNUP_ENABLED !== "true") return NextResponse.next({request: {headers: requestHeaders}});
  const origin = requestOrigin(request);
  if (!origin) return new NextResponse("Access unavailable.", {status: 403});
  const path = request.nextUrl.pathname;
  if (path.startsWith("/api/") || path.startsWith("/auth/") || ["/onboarding", "/cleaner"].includes(path)) return NextResponse.next({request: {headers: requestHeaders}});
  const token = request.cookies.get("manager-session")?.value;
  const accountPage = path === "/" || path === "/signup" || path === "/login";
  if (!token) return accountPage && (path !== "/signup" || workspaceRegistrationEnabled || workspaceInviteSignupEnabled) ? NextResponse.next({request: {headers: requestHeaders}}) : NextResponse.redirect(new URL(workspaceRegistrationEnabled ? "/signup" : "/login", origin));
  const upstreamOrigin = apiOrigin(origin);
  if (!upstreamOrigin) return new NextResponse("Your workspace is unavailable. Please refresh to try again.", {status: 503});
  const proxyHeaders = await proxyIdentityHeaders(request, "GET", "/v1/workspace/setup");
  if (!proxyHeaders) return new NextResponse("Your workspace is unavailable. Please refresh to try again.", {status: 503});
  proxyHeaders.set("authorization", `Bearer ${token}`);
  let upstream: Response | undefined;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      upstream = await fetch(new URL("/v1/workspace/setup", upstreamOrigin), {
        headers: proxyHeaders, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(4000),
      });
      if ((upstream.status !== 429 && upstream.status < 500) || attempt === 1) break;
      await upstream.body?.cancel();
    } catch {
      if (attempt === 1) return new NextResponse("Your workspace is unavailable. Please refresh to try again.", {status: 503});
    }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  if (upstream?.status === 401) {
    const response = path === "/login" ? NextResponse.next({request: {headers: requestHeaders}}) : NextResponse.redirect(new URL("/login", origin));
    response.cookies.delete("manager-session");
    response.cookies.delete("manager-role");
    return response;
  }
  if (!upstream?.ok) return new NextResponse("Your workspace is unavailable. Please refresh to try again.", {status: 503});
  let completed: boolean;
  try {
    const setup: unknown = await upstream.json();
    if (typeof setup !== "object" || setup === null || !("completed" in setup) || typeof setup.completed !== "boolean") throw new Error("Invalid workspace status");
    completed = setup.completed;
  } catch {
    return new NextResponse("Your workspace is unavailable. Please refresh to try again.", {status: 503});
  }
  if (!completed) return ["/signup", "/security"].includes(path) ? NextResponse.next({request: {headers: requestHeaders}}) : NextResponse.redirect(new URL("/signup", origin));
  if (accountPage) return NextResponse.redirect(new URL("/dashboard", origin));
  if (path === "/") return NextResponse.redirect(new URL("/dashboard", origin));
  return NextResponse.next({request: {headers: requestHeaders}});
}

export async function middleware(request: NextRequest) {
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const development = process.env.NODE_ENV === "development";
  const secureOrigin = process.env.NODE_ENV === "production" && process.env.CLEANER_PORTAL_BASE_URL?.startsWith("https://");
  const csp = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${development ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    `connect-src 'self'${development ? " ws: wss:" : ""}`,
    "img-src 'self' data: blob:",
    "font-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    ...(secureOrigin ? ["upgrade-insecure-requests"] : []),
  ].join("; ");
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);
  const response = await workspaceAccess(request, requestHeaders);
  response.headers.set("Content-Security-Policy", csp);
  response.headers.set("Cache-Control", "no-store");
  if (secureOrigin) response.headers.set("Strict-Transport-Security", "max-age=31536000");
  return response;
}

export const config = {matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"]};
