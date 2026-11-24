import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { operationsToolLinks } from "../../../lib/operations-login";
import { proxyIdentityHeaders } from "../../../lib/proxy-identity";
import { apiOrigin, requestOrigin } from "../../../lib/request-boundary";
import { sessionTokenCookie, workspaceSignupEnabled } from "../../../lib/session";

const privateHeaders = {"Cache-Control": "no-store"};
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function rows(value: unknown, tool: "cleaner" | "guest" | "maintenance", base: string) {
  if (!Array.isArray(value)) throw new Error("Invalid queue");
  return value.slice(0, 200).flatMap(item => {
    if (!item || typeof item !== "object" || !uuid.test(String(item.id))
        || typeof item.title !== "string" || typeof item.next_action !== "string" || typeof item.path !== "string") return [];
    const source = tool === "guest" ? item.source : tool;
    if (tool === "guest" && source !== "guest-replies" && source !== "supplies") return [];
    if (!item.path.startsWith("/") || item.path.startsWith("//")) return [];
    const link = new URL(item.path, base);
    if (link.origin !== new URL(base).origin) return [];
    return [{
      id: item.id as string, source: source as string, title: item.title.slice(0, 160),
      next_action: item.next_action.slice(0, 160),
      property: typeof item.property === "string" ? item.property.slice(0, 160) : null,
      due_at: typeof item.due_at === "string" && Number.isFinite(Date.parse(item.due_at)) ? item.due_at : null,
      urgent: item.urgent === true, href: tool === "cleaner" ? link.pathname : link.href,
    }];
  });
}

export async function GET(request: NextRequest) {
  const origin = requestOrigin(request);
  if (!workspaceSignupEnabled || !origin) return NextResponse.json({}, {status: 404, headers: privateHeaders});
  const upstream = apiOrigin(origin);
  const session = (await cookies()).get(sessionTokenCookie)?.value;
  if (!session) return NextResponse.json({}, {status: 401, headers: privateHeaders});
  if (!upstream) return NextResponse.json({}, {status: 503, headers: privateHeaders});

  async function internal(path: string, method: "GET" | "POST") {
    const headers = await proxyIdentityHeaders(request, method, path);
    if (!headers || !upstream) throw new Error("Unavailable");
    headers.set("Authorization", `Bearer ${session}`);
    return fetch(new URL(path, upstream), {
      method, headers, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(7000),
    });
  }

  try {
    const queues = operationsToolLinks();
    const local = await internal("/v1/workspace/action-inbox", "GET");
    if (!local.ok) return NextResponse.json({}, {status: local.status >= 500 ? 503 : local.status, headers: privateHeaders});
    const cleaner = await local.json();
    const items = rows(cleaner.items, "cleaner", origin);
    let token: unknown;
    if (queues.length) {
      try {
        const grant = await internal("/v1/accounts/operations/inbox-grant", "POST");
        if (grant.ok) token = (await grant.json()).token;
      } catch {}
    }
    const external = await Promise.all(queues.map(async queue => {
      if (typeof token !== "string" || !/^opq_[A-Za-z0-9_-]{43}$/.test(token)) return {unavailable: queue.label};
      const guest = queue.label === "Guest Messages";
      try {
        const response = await fetch(new URL(guest ? "/api/action-inbox" : "/v1/operations/action-inbox", queue.href), {
          headers: {"Authorization": `Bearer ${token}`},
          cache: "no-store", redirect: "error", signal: AbortSignal.timeout(9000),
        });
        if (!response.ok) return {unavailable: queue.label};
        return {items: rows((await response.json()).items, guest ? "guest" : "maintenance", queue.href)};
      } catch {
        return {unavailable: queue.label};
      }
    }));
    const unavailable = ["Guest Messages", "Maintenance"].filter(label => !queues.some(queue => queue.label === label));
    return NextResponse.json({
      items: [...items, ...external.flatMap(result => result.items ?? [])],
      claims: cleaner.claims,
      managers: cleaner.managers,
      current_manager: cleaner.current_manager,
      unavailable: [...unavailable, ...external.flatMap(result => result.unavailable ? [result.unavailable] : [])],
    }, {headers: privateHeaders});
  } catch {
    return NextResponse.json({}, {status: 503, headers: privateHeaders});
  }
}
