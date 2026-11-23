import { createHash, randomBytes } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { operationsFlowCookie, operationsOrigins } from "../../../../lib/operations-login";

export function GET(request: NextRequest) {
  const origins = operationsOrigins();
  if (!origins || request.headers.get("host") !== new URL(origins.maintenance).host) {
    return new NextResponse("Shared sign-in is unavailable.", {status: 404});
  }
  const verifier = randomBytes(32).toString("base64url");
  const state = randomBytes(32).toString("base64url");
  const challenge = createHash("sha256").update(verifier).digest("base64url");
  const target = new URL("/auth/operations/authorize", origins.turnover);
  target.search = new URLSearchParams({client: "maintenance", challenge, state}).toString();
  const response = NextResponse.redirect(target, {status: 303, headers: {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}});
  response.cookies.set(operationsFlowCookie, `${state}.${verifier}`, {
    httpOnly: true, secure: true, sameSite: "lax", path: "/", maxAge: 300,
  });
  return response;
}
