import { proxyIdentityHeaders } from "../../../lib/proxy-identity";
import { readRequestBody } from "../../../lib/request-body";
import { apiOrigin, requestOrigin } from "../../../lib/request-boundary";
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { sessionTokenCookie, sessionRoleCookie, workspaceSignupEnabled, workspaceRegistrationEnabled, workspaceInviteSignupEnabled } from "../../../lib/session";

async function accountRequest(request: NextRequest, context: {params: Promise<{action: string}>}) {
  const {action} = await context.params;
  const security = action === "security";
  const method = request.method;
  const responseHeaders = {"Cache-Control": "no-store"};
  if (!workspaceSignupEnabled || !(security ? ["GET", "PATCH"].includes(method) : method === "POST" && ["signup", "login", "logout"].includes(action))) return NextResponse.json({error: {message: "Not found."}}, {status: 404, headers: responseHeaders});
  if (action === "signup" && !workspaceRegistrationEnabled && !workspaceInviteSignupEnabled) return NextResponse.json({error: {message: "Registration is unavailable."}}, {status: 403, headers: responseHeaders});
  const origin = requestOrigin(request);
  if (!origin || method !== "GET" && request.headers.get("origin") !== origin) return NextResponse.json({error: {message: "Same-origin access required."}}, {status: 403, headers: responseHeaders});
  const upstreamOrigin = apiOrigin(origin);
  if (!upstreamOrigin) return NextResponse.json({error: {message: "Connection unavailable."}}, {status: 503, headers: responseHeaders});
  const path = `/v1/accounts/${security ? "me" : action}`;
  const headers = await proxyIdentityHeaders(request, method, path);
  if (!headers) return NextResponse.json({error: {message: "Connection unavailable."}}, {status: 503, headers: responseHeaders});
  headers.set("Content-Type", "application/json");
  const key = request.headers.get("idempotency-key");
  if (key) headers.set("idempotency-key", key);
  if (action === "logout" || security) {
    const token = (await cookies()).get(sessionTokenCookie)?.value;
    if (!token) return NextResponse.json({error: {message: "Sign in to continue."}}, {status: 401, headers: responseHeaders});
    headers.set("authorization", `Bearer ${token}`);
  }
  try {
    const content = method === "GET" ? {body: undefined} : await readRequestBody(request, 32 * 1024);
    if ("response" in content) {
      content.response.headers.set("Cache-Control", "no-store");
      return content.response;
    }
    const payload = action === "logout" ? "{}" : content.body;
    const retryable = method === "GET" || Boolean(key) && action !== "logout";
    let upstream: Response | undefined;
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        upstream = await fetch(new URL(path, upstreamOrigin), {method, headers, body: payload, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(6000)});
        if (!retryable || (upstream.status !== 429 && upstream.status < 500) || attempt === 2) break;
        await upstream.body?.cancel();
      } catch (failure) {
        if (!retryable || attempt === 2) throw failure;
      }
      await new Promise(resolve => setTimeout(resolve, 250 * 2 ** attempt));
    }
    if (!upstream) throw new Error("Connection unavailable");
    if (!upstream.ok) {
      const failure = await upstream.json().catch(() => null) as {error?: {code?: string}} | null;
      const code = upstream.status === 401 && ["mfa_required", "invalid_second_factor"].includes(failure?.error?.code ?? "") ? failure?.error?.code : undefined;
      const message = code === "mfa_required" ? "Enter your authenticator code or a recovery code." : code === "invalid_second_factor" ? "That verification code is incorrect or has expired. Try again." : upstream.status === 401 ? security ? "Password or verification code is incorrect. Sign in again if your session expired." : "Email or password is incorrect." : upstream.status === 403 && action === "signup" && failure?.error?.code === "invitation_required" ? "This invitation is unavailable or expired. Ask for a new invitation and use the invited email address." : upstream.status === 409 ? security ? "Your security settings changed. Refresh the page and try again." : "An account already exists for this email. Sign in instead." : upstream.status === 429 ? "Too many attempts. Please wait and try again." : "Could not complete this request. Check your details and try again.";
      return NextResponse.json({error: {message, ...(code ? {code} : {})}}, {status: upstream.status, headers: responseHeaders});
    }
    if (security) {
      const body = await upstream.json() as {deleted?: boolean; mfa_enabled?: boolean; recovery_codes_remaining?: number; secret?: string; expires_at?: string; recovery_codes?: string[]};
      if (method === "PATCH" && body.deleted === true) {
        const response = NextResponse.json({deleted: true}, {headers: responseHeaders});
        const options = {httpOnly: true, secure: process.env.NODE_ENV === "production", sameSite: "lax" as const, path: "/", maxAge: 0};
        response.cookies.set(sessionTokenCookie, "", options);
        response.cookies.set(sessionRoleCookie, "", options);
        return response;
      }
      if (typeof body.mfa_enabled !== "boolean") throw new Error("Invalid security response");
      return NextResponse.json({mfa_enabled: body.mfa_enabled, recovery_codes_remaining: body.recovery_codes_remaining, ...(method === "PATCH" ? {secret: body.secret, expires_at: body.expires_at, recovery_codes: body.recovery_codes} : {})}, {headers: responseHeaders});
    }
    const body = action === "logout" ? null : await upstream.json() as {token?: string};
    if (action !== "logout" && !body?.token) return NextResponse.json({error: {message: "Sign-in unavailable."}}, {status: 502, headers: responseHeaders});
    const response = NextResponse.json({ok: true}, {headers: responseHeaders});
    const options = {httpOnly: true, secure: process.env.NODE_ENV === "production", sameSite: "lax" as const, path: "/"};
    response.cookies.set(sessionTokenCookie, body?.token ?? "", {...options, maxAge: action === "logout" ? 0 : 43200});
    response.cookies.set(sessionRoleCookie, action === "logout" ? "" : "owner", {...options, maxAge: action === "logout" ? 0 : 43200});
    return response;
  } catch {
    return NextResponse.json({error: {message: "Connection unavailable. Please try again."}}, {status: 503, headers: responseHeaders});
  }
}

export { accountRequest as GET, accountRequest as PATCH, accountRequest as POST };
