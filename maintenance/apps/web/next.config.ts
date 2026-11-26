import type { NextConfig } from "next";

const demoSample = process.env.NEXT_PUBLIC_DEMO_FIXTURE === "sample";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  experimental: { proxyClientMaxBodySize: "210mb", proxyTimeout: 120_000 },
  async rewrites() {
    const configuredOrigin = process.env.API_INTERNAL_ORIGIN;
    const origin = configuredOrigin || (demoSample ? "http://api:8000" : null);
    if (!origin) return [];
    const target = new URL(origin);
    if (!["http:", "https:"].includes(target.protocol) || target.username || target.password
        || target.pathname !== "/" || target.search || target.hash) {
      throw new Error("API_INTERNAL_ORIGIN must be an HTTP(S) origin.");
    }
    return [{ source: "/v1/:path*", destination: `${target.origin}/v1/:path*` }];
  },
};

export default nextConfig;
