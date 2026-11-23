import { NextRequest, NextResponse } from "next/server";
import { proxyIdentityHeaders } from "./proxy-identity";
import { apiOrigin, requestOrigin } from "./request-boundary";
import { workspaceSignupEnabled } from "./session";

const privateHeaders = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"};

export async function readOperationsStay(request: NextRequest, resource: "stay-context" | "guest-messages" | "repair-context") {
  const contextPath = "/v1/workspace/operations/" + resource;
  const origin = requestOrigin(request);
  if (!workspaceSignupEnabled || process.env.OPERATIONS_LOGIN_ENABLED !== "true" || !origin) {
    return new NextResponse("Shared stay context is unavailable.", {status: 404, headers: privateHeaders});
  }
  const token = request.headers.get("authorization");
  if (request.headers.has("origin") || request.headers.has("cookie")
      || !token || !(resource === "repair-context" ? /^Bearer opr_[A-Za-z0-9_-]{43}$/ : /^Bearer ops_[A-Za-z0-9_-]{43}$/).test(token)) {
    return new NextResponse("Shared workspace access is required.", {status: 401, headers: privateHeaders});
  }
  const query = new URLSearchParams();
  for (const name of ["property_external_id", "reservation_external_id"]) {
    const values = request.nextUrl.searchParams.getAll(name);
    if (values.length !== 1 || !values[0] || values[0].length > 120) {
      return new NextResponse("A property and reservation are required.", {status: 400, headers: privateHeaders});
    }
    query.set(name, values[0]);
  }
  const upstream = apiOrigin(origin);
  const headers = await proxyIdentityHeaders(request, "GET", contextPath);
  if (!upstream || !headers) {
    return new NextResponse("Shared stay context is temporarily unavailable.", {status: 503, headers: privateHeaders});
  }
  headers.set("Authorization", token);
  try {
    const target = new URL(contextPath, upstream);
    target.search = query.toString();
    const result = await fetch(target, {headers, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(6000)});
    if (!result.ok) {
      return new NextResponse("Shared stay context could not be loaded.", {
        status: result.status >= 500 ? 503 : result.status, headers: privateHeaders,
      });
    }
    return NextResponse.json(await result.json(), {headers: privateHeaders});
  } catch {
    return new NextResponse("Shared stay context is temporarily unavailable.", {status: 503, headers: privateHeaders});
  }
}
