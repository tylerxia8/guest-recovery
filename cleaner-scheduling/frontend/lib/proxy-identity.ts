export async function proxyIdentityHeaders(request: Request, method: string, path: string): Promise<Headers | null> {
  const secret = process.env.WEB_PROXY_SECRET;
  if (!secret) return process.env.NODE_ENV === "production" && process.env.WORKSPACE_SIGNUP_ENABLED === "true" && process.env.HOSPITABLE_READ_ONLY === "true" ? null : new Headers();
  const ip = request.headers.get("x-real-ip")?.trim();
  if (!ip || !/^[0-9a-fA-F:.]{3,45}$/.test(ip)) return null;
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", encoder.encode(secret), {name: "HMAC", hash: "SHA-256"}, false, ["sign"]);
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(`${timestamp}\n${method}\n${path}\n${ip}`));
  return new Headers({
    "x-turnover-client-ip": ip,
    "x-turnover-proxy-time": timestamp,
    "x-turnover-proxy-signature": Array.from(new Uint8Array(signature), byte => byte.toString(16).padStart(2, "0")).join(""),
  });
}
