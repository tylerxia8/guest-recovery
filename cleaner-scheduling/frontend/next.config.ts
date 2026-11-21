import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async headers() {
    return [{source: "/(.*)", headers: [
      {key: "Referrer-Policy", value: "no-referrer"},
      {key: "X-Frame-Options", value: "DENY"},
      {key: "X-Content-Type-Options", value: "nosniff"},
      {key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()"},
    ]}, ...["/onboarding", "/cleaner"].map(source => ({source, headers: [
      {key: "Cache-Control", value: "no-store"},
    ]}))];
  },
  webpack(config) {
    if (process.env.LOCAL_DEMO === "1") config.cache = false;
    return config;
  },
};

export default nextConfig;
