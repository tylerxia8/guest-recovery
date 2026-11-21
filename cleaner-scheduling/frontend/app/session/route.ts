import { readRequestBody } from "../../lib/request-body";
import { sessionTokenCookie, sessionRoleCookie } from "../../lib/session";
import { NextResponse } from "next/server";
import { readFile } from "node:fs/promises";

export async function POST(request: Request) {
  if (process.env.WORKSPACE_SIGNUP_ENABLED === "true") return NextResponse.json({error: {message: "Use manager sign-in."}}, {status: 403});
  const liveReadOnly = process.env.HOSPITABLE_READ_ONLY === "true";
  if (liveReadOnly && !["localhost", "127.0.0.1"].includes(new URL(`http://${request.headers.get("host")}`).hostname)) {
    return NextResponse.json({ error: { message: "Local sign-in required." } }, { status: 403 });
  }
  if (request.headers.get("origin") && request.headers.get("origin") !== `${new URL(request.url).protocol}//${request.headers.get("host")}`) {
    return NextResponse.json({ error: { message: "Same-origin sign-in required." } }, { status: 403 });
  }
  const content = await readRequestBody(request, 32 * 1024);
  if ("response" in content) return content.response;
  let payload: {token?: string; role?: string; connection?: string};
  try {
    const value: unknown = JSON.parse(content.body);
    if (typeof value !== "object" || value === null || Array.isArray(value) || Object.values(value).some(item => typeof item !== "string")) throw new Error("Invalid session request");
    payload = value;
  } catch {
    return NextResponse.json({error: {message: "Enter valid session details."}}, {status: 400});
  }
  let token = payload.token?.trim() ?? "";
  if (payload.connection === "hospitable" && !liveReadOnly && process.env.LOCAL_DEMO !== "1") {
    return NextResponse.json({error: {message: "Live Hospitable connection is not available yet."}}, {status: 503});
  }
  if (liveReadOnly) {
    if (payload.connection !== "hospitable" || !process.env.LOCAL_APP_TOKEN_FILE) {
      return NextResponse.json({ error: { message: "Use the local Hospitable connection." } }, { status: 403 });
    }
    try {
      token = (await readFile(process.env.LOCAL_APP_TOKEN_FILE, "utf8")).trim();
    } catch {
      return NextResponse.json({ error: { message: "Start the read-only API first." } }, { status: 503 });
    }
  }
  const requestedRole = liveReadOnly ? "owner" : payload.connection === "hospitable" ? "manager" : payload.role;
  const role = requestedRole === "owner" || requestedRole === "manager" || requestedRole === "viewer" ? requestedRole : "";
  if (process.env.LOCAL_DEMO === "1" && role) {
    try {
      const upstream = await fetch(`${process.env.CLEANING_API_ORIGIN ?? "http://127.0.0.1:8000"}/v1/demo/session`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ role }), cache: "no-store", signal: AbortSignal.timeout(10000),
      });
      if (!upstream.ok) throw new Error("Demo unavailable");
      token = ((await upstream.json()) as { token: string }).token;
    } catch {
      return NextResponse.json({ error: { message: "Start the isolated demo API first." } }, { status: 503 });
    }
  }
  if (!token || !role) {
    return NextResponse.json({ error: { message: "A development token and role are required." } }, { status: 400 });
  }
  const response = NextResponse.json({ ok: true });
  response.cookies.set(sessionTokenCookie, token, { httpOnly: true, sameSite: "lax", path: "/" });
  response.cookies.set(sessionRoleCookie, role, { httpOnly: false, sameSite: "lax", path: "/" });
  return response;
}
