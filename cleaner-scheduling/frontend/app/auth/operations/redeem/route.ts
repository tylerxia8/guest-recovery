import { NextRequest, NextResponse } from "next/server";
import { proxyIdentityHeaders } from "../../../../lib/proxy-identity";
import { apiOrigin, requestOrigin } from "../../../../lib/request-boundary";
import { readRequestBody } from "../../../../lib/request-body";
import { workspaceSignupEnabled } from "../../../../lib/session";

const privateHeaders = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"};
const redeemPath = "/v1/accounts/operations/redeem";

export async function POST(request: NextRequest) {
  const origin = requestOrigin(request);
  if (!workspaceSignupEnabled || process.env.OPERATIONS_LOGIN_ENABLED !== "true" || !origin) {
    return new NextResponse("Shared sign-in is unavailable.", {status: 404, headers: privateHeaders});
  }
  if (request.headers.has("origin") || request.headers.has("cookie")) {
    return new NextResponse("Server request required.", {status: 403, headers: privateHeaders});
  }
  const upstream = apiOrigin(origin);
  const headers = await proxyIdentityHeaders(request, "POST", redeemPath);
  if (!upstream || !headers) {
    return new NextResponse("Shared sign-in is temporarily unavailable.", {status: 503, headers: privateHeaders});
  }
  try {
    const content = await readRequestBody(request, 1024);
    if ("response" in content) return content.response;
    headers.set("Content-Type", "application/json");
    const result = await fetch(new URL(redeemPath, upstream), {
      method: "POST", headers, body: content.body,
      cache: "no-store", redirect: "error", signal: AbortSignal.timeout(6000),
    });
    if (!result.ok) {
      const status = result.status >= 500 ? 503 : result.status;
      return new NextResponse("Shared sign-in could not be completed.", {status, headers: privateHeaders});
    }
    return NextResponse.json(await result.json(), {headers: privateHeaders});
  } catch {
    return new NextResponse("Shared sign-in is temporarily unavailable.", {status: 503, headers: privateHeaders});
  }
}
