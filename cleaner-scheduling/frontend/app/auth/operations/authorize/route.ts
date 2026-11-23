import { NextRequest, NextResponse } from "next/server";
import { operationsAuthorization } from "../../../../lib/operations-login";
import { proxyIdentityHeaders } from "../../../../lib/proxy-identity";
import { apiOrigin, requestOrigin } from "../../../../lib/request-boundary";
import { sessionTokenCookie, workspaceSignupEnabled } from "../../../../lib/session";

const privateHeaders = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"};
const grantPath = "/v1/accounts/operations/grants";

export async function GET(request: NextRequest) {
  const flow = operationsAuthorization(request.nextUrl.searchParams);
  const origin = requestOrigin(request);
  if (!workspaceSignupEnabled || !flow || !origin) {
    return new NextResponse("Shared sign-in is unavailable.", {status: 404, headers: privateHeaders});
  }
  const {client, challenge, state, callback} = flow;
  const parameters = new URLSearchParams({client, challenge, state});
  const signIn = () => NextResponse.redirect(new URL("/login?" + parameters, origin), {status: 303, headers: privateHeaders});
  const token = request.cookies.get(sessionTokenCookie)?.value;
  if (!token) return signIn();
  const upstream = apiOrigin(origin);
  const headers = await proxyIdentityHeaders(request, "POST", grantPath);
  if (!upstream || !headers) {
    return new NextResponse("Turnover is temporarily unavailable.", {status: 503, headers: privateHeaders});
  }
  headers.set("Content-Type", "application/json");
  headers.set("Authorization", `Bearer ${token}`);
  try {
    const result = await fetch(new URL(grantPath, upstream), {
      method: "POST", headers, body: JSON.stringify({client, challenge}),
      cache: "no-store", redirect: "error", signal: AbortSignal.timeout(6000),
    });
    if (result.status === 401) {
      const response = signIn();
      response.cookies.delete(sessionTokenCookie);
      return response;
    }
    if (!result.ok) throw new Error("Grant unavailable");
    const payload: unknown = await result.json();
    if (!payload || typeof payload !== "object" || !("code" in payload)
      || typeof payload.code !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(payload.code)) {
      throw new Error("Invalid grant");
    }
    callback.searchParams.set("code", payload.code);
    callback.searchParams.set("state", state);
    return NextResponse.redirect(callback, {status: 303, headers: privateHeaders});
  } catch {
    return new NextResponse("Shared sign-in is temporarily unavailable.", {status: 503, headers: privateHeaders});
  }
}
