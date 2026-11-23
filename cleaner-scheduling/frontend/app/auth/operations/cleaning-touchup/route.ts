import { NextRequest, NextResponse } from "next/server";
import { proxyIdentityHeaders } from "../../../../lib/proxy-identity";
import { apiOrigin, requestOrigin } from "../../../../lib/request-boundary";
import { readRequestBody } from "../../../../lib/request-body";
import { workspaceSignupEnabled } from "../../../../lib/session";

const path = "/v1/workspace/operations/cleaning-touchup";
const privateHeaders = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"};

export async function POST(request: NextRequest) {
  const origin = requestOrigin(request);
  if (!workspaceSignupEnabled || process.env.OPERATIONS_LOGIN_ENABLED !== "true" || !origin) {
    return new NextResponse("Cleaning handoff is unavailable.", {status: 404, headers: privateHeaders});
  }
  const token = request.headers.get("authorization");
  if (request.headers.has("origin") || request.headers.has("cookie")
      || !token || !/^Bearer opc_[A-Za-z0-9_-]{43}$/.test(token)) {
    return new NextResponse("Shared cleaning access is required.", {status: 401, headers: privateHeaders});
  }
  const upstream = apiOrigin(origin);
  const headers = await proxyIdentityHeaders(request, "POST", path);
  if (!upstream || !headers) {
    return new NextResponse("Cleaning handoff is temporarily unavailable.", {status: 503, headers: privateHeaders});
  }
  try {
    const content = await readRequestBody(request, 10000);
    if ("response" in content) return content.response;
    headers.set("Authorization", token);
    headers.set("Content-Type", "application/json");
    const result = await fetch(new URL(path, upstream), {
      method: "POST", headers, body: content.body,
      cache: "no-store", redirect: "error", signal: AbortSignal.timeout(8000),
    });
    if (!result.ok) {
      return new NextResponse("The cleaning handoff could not be completed.", {
        status: result.status >= 500 ? 503 : result.status, headers: privateHeaders,
      });
    }
    return NextResponse.json(await result.json(), {headers: privateHeaders});
  } catch {
    return new NextResponse("Cleaning handoff is temporarily unavailable.", {status: 503, headers: privateHeaders});
  }
}
