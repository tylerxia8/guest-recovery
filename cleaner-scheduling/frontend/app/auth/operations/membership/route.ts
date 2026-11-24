import { NextRequest, NextResponse } from "next/server";
import { proxyIdentityHeaders } from "../../../../lib/proxy-identity";
import { apiOrigin, requestOrigin } from "../../../../lib/request-boundary";
import { workspaceSignupEnabled } from "../../../../lib/session";

const path = "/v1/accounts/operations/membership";
const privateHeaders = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"};

export async function GET(request: NextRequest) {
  const origin = requestOrigin(request);
  if (!workspaceSignupEnabled || process.env.OPERATIONS_LOGIN_ENABLED !== "true" || !origin) {
    return new NextResponse("Shared membership is unavailable.", {status: 404, headers: privateHeaders});
  }
  const token = request.headers.get("authorization");
  if (request.headers.has("origin") || request.headers.has("cookie")
      || !token || !/^Bearer opm_[A-Za-z0-9_-]{43}$/.test(token)) {
    return new NextResponse("Shared workspace access is required.", {status: 401, headers: privateHeaders});
  }
  const upstream = apiOrigin(origin);
  const headers = await proxyIdentityHeaders(request, "GET", path);
  if (!upstream || !headers) {
    return new NextResponse("Shared membership is temporarily unavailable.", {status: 503, headers: privateHeaders});
  }
  headers.set("Authorization", token);
  try {
    const result = await fetch(new URL(path, upstream), {
      headers, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(6000),
    });
    if (!result.ok) {
      return new NextResponse("Shared membership could not be loaded.", {
        status: result.status >= 500 ? 503 : result.status, headers: privateHeaders,
      });
    }
    return NextResponse.json(await result.json(), {headers: privateHeaders});
  } catch {
    return new NextResponse("Shared membership is temporarily unavailable.", {status: 503, headers: privateHeaders});
  }
}
