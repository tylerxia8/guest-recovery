import { timingSafeEqual } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { operationsFlowCookie, operationsOrigins, operationsValue } from "../../../../lib/operations-login";

const headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"};

export async function POST(request: NextRequest) {
  const origins = operationsOrigins();
  const apiOrigin = process.env.API_INTERNAL_ORIGIN;
  if (!origins || !apiOrigin || request.headers.get("host") !== new URL(origins.maintenance).host
      || request.headers.get("origin") !== origins.maintenance) {
    return new NextResponse("Shared sign-in is unavailable.", {status: 403, headers});
  }
  const [state, verifier] = (request.cookies.get(operationsFlowCookie)?.value ?? "").split(".");
  const code = request.nextUrl.searchParams.get("code") ?? "";
  const suppliedState = request.nextUrl.searchParams.get("state") ?? "";
  if (![state, verifier, code, suppliedState].every(value => value && operationsValue.test(value))
      || !timingSafeEqual(Buffer.from(state), Buffer.from(suppliedState))) {
    return new NextResponse("Start shared sign-in again.", {status: 400, headers});
  }
  try {
    const result = await fetch(new URL("/v1/auth/operations/redeem", apiOrigin), {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({code, verifier}), cache: "no-store", redirect: "error", signal: AbortSignal.timeout(10000),
    });
    if (!result.ok) return new NextResponse("Shared sign-in could not be completed.", {status: 401, headers});
    const {token} = await result.json();
    if (typeof token !== "string" || !token) throw new Error("Invalid session");
    const response = NextResponse.json({token}, {headers});
    response.cookies.set(operationsFlowCookie, "", {httpOnly: true, secure: true, sameSite: "lax", path: "/", maxAge: 0});
    return response;
  } catch {
    return new NextResponse("Shared sign-in is temporarily unavailable.", {status: 503, headers});
  }
}
