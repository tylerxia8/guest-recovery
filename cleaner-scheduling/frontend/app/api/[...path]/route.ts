import { proxyIdentityHeaders } from "../../../lib/proxy-identity";
import { readRequestBody } from "../../../lib/request-body";
import { apiOrigin, requestOrigin } from "../../../lib/request-boundary";
import { sessionTokenCookie, workspaceSignupEnabled } from "../../../lib/session";
import { cookies } from "next/headers";
import { type NextRequest } from "next/server";

async function proxy(request: NextRequest, path: string[]) {
  if (path.some(segment => !/^[a-zA-Z0-9][a-zA-Z0-9._-]*$/.test(segment))) return Response.json({error: {message: "Invalid API path."}}, {status: 400});
  if (path[0] === "health") return Response.json({error: {message: "Not found."}}, {status: 404});
  if (path[0] === "accounts") return Response.json({error: {message: "Use manager authentication."}}, {status: 404});
  const cleanerRequest = (path.length === 1 && ["cleaner-portal", "cleaner-onboarding"].includes(path[0])) || (path.length === 2 && path[0] === "cleaner-portal" && path[1] === "language");
  const origin = requestOrigin(request);
  if (!origin) return Response.json({error: {message: "Access unavailable."}}, {status: 403});
  if (process.env.HOSPITABLE_READ_ONLY === "true" && !workspaceSignupEnabled && !cleanerRequest && !["localhost", "127.0.0.1"].includes(new URL(origin).hostname)) {
    return Response.json({error: {message: "Local manager access required."}}, {status: 403});
  }
  if (request.method !== "GET" && request.headers.get("origin") !== origin && (workspaceSignupEnabled || cleanerRequest || process.env.HOSPITABLE_READ_ONLY === "true")) {
    return Response.json({error: {message: "Same-origin access required."}}, {status: 403});
  }
  const token = (await cookies()).get(sessionTokenCookie)?.value;
  if (workspaceSignupEnabled && !cleanerRequest && !token) return Response.json({error: {message: "Sign in to continue."}}, {status: 401});
  const headers = await proxyIdentityHeaders(request, request.method, `/v1/${path.join("/")}`);
  if (!headers) return Response.json({error: {message: "Connection unavailable."}}, {status: 503});
  const contentType = request.headers.get("content-type");
  if (contentType) {
    headers.set("content-type", contentType);
  }
  const ifMatch = request.headers.get("if-match");
  if (ifMatch) {
    headers.set("if-match", ifMatch);
  }
  const idempotencyKey = request.headers.get("idempotency-key");
  if (idempotencyKey) {
    headers.set("idempotency-key", idempotencyKey);
  }
  if (cleanerRequest) {
    const cleanerToken = request.headers.get("authorization");
    if (cleanerToken) headers.set("authorization", cleanerToken);
  } else if (token) {
    headers.set("authorization", `Bearer ${token}`);
  }
  const upstreamOrigin = apiOrigin(origin);
  if (!upstreamOrigin) return Response.json({error: {message: "Connection unavailable."}}, {status: 503});
  const target = new URL(`/v1/${path.join("/")}`, upstreamOrigin);
  target.search = request.nextUrl.search;
  const content = await readRequestBody(request, 6 * 1024 * 1024);
  if ("response" in content) return content.response;
  const body = request.method === "GET" || request.method === "HEAD" ? undefined : content.body;
  const retryable = request.method === "GET" || Boolean(idempotencyKey);
  let response: Response | undefined;
  const deadline = AbortSignal.timeout(request.method === "POST" ? 660000 : 20000);
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      response = await fetch(target, {method: request.method, headers, body, cache: "no-store", redirect: "error", signal: deadline});
      if (!retryable || (response.status !== 429 && response.status < 500) || attempt === 2) break;
      await response.body?.cancel();
    } catch {
      if (!retryable || deadline.aborted || attempt === 2) return Response.json({error: {message: "Connection unavailable. Please try again."}}, {status: 503});
    }
    await new Promise(resolve => setTimeout(resolve, 250 * 2 ** attempt));
  }
  if (!response) return Response.json({error: {message: "Connection unavailable."}}, {status: 503});
  const text = await response.text();
  return new Response(text, {
    status: response.status,
    headers: {
      "content-type": response.headers.get("content-type") ?? "application/json",
      "cache-control": "no-store",
      ...(response.headers.get("content-disposition")
        ? { "content-disposition": response.headers.get("content-disposition")! } : {}),
      ...(response.headers.get("x-content-sha256")
        ? { "x-content-sha256": response.headers.get("x-content-sha256")! } : {}),
    },
  });
}

export async function GET(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return proxy(request, path);
}

export async function POST(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return proxy(request, path);
}

export async function PATCH(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return proxy(request, path);
}
