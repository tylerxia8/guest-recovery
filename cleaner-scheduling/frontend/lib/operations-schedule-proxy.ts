import { NextRequest, NextResponse } from "next/server";
import { proxyIdentityHeaders } from "./proxy-identity";
import { apiOrigin, requestOrigin } from "./request-boundary";
import { readRequestBody } from "./request-body";
import { workspaceSignupEnabled } from "./session";

const privateHeaders = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"};

export async function postOperationsCommand(
  request: NextRequest, operation: "cleaning-touchup" | "stay-change" | "guest-reply",
) {
  const path = "/v1/workspace/operations/" + operation;
  const origin = requestOrigin(request);
  if (!workspaceSignupEnabled || process.env.OPERATIONS_LOGIN_ENABLED !== "true" || !origin) {
    return new NextResponse("Request is unavailable.", {status: 404, headers: privateHeaders});
  }
  const reply = operation === "guest-reply";
  const accessPattern = reply ? /^Bearer opg_[A-Za-z0-9_-]{43}$/ : /^Bearer opc_[A-Za-z0-9_-]{43}$/;
  const token = request.headers.get("authorization");
  if (request.headers.has("origin") || request.headers.has("cookie")
      || !token || !accessPattern.test(token)) {
    return new NextResponse("Shared workspace access is required.", {status: 401, headers: privateHeaders});
  }
  const upstream = apiOrigin(origin);
  const headers = await proxyIdentityHeaders(request, "POST", path);
  if (!upstream || !headers) {
    return new NextResponse("Request is temporarily unavailable.", {status: 503, headers: privateHeaders});
  }
  try {
    const content = await readRequestBody(request, 10000);
    if ("response" in content) return content.response;
    headers.set("Authorization", token);
    headers.set("Content-Type", "application/json");
    const result = await fetch(new URL(path, upstream), {
      method: "POST", headers, body: content.body,
      cache: "no-store", redirect: "error", signal: AbortSignal.timeout(reply ? 13000 : 8000),
    });
    if (!result.ok) {
      return new NextResponse("The request could not be completed.", {
        status: result.status >= 500 ? 503 : result.status, headers: privateHeaders,
      });
    }
    return NextResponse.json(await result.json(), {headers: privateHeaders});
  } catch {
    return new NextResponse("Request is temporarily unavailable.", {status: 503, headers: privateHeaders});
  }
}
