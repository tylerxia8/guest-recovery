import { sessionTokenCookie, workspaceMode, workspaceSignupEnabled } from "../lib/session";
import { cookies } from "next/headers";
import type { ReactNode } from "react";
import type { Metadata, Viewport } from "next";
import { AppFrame } from "../components/AppFrame";
import "./globals.css";

export const metadata: Metadata = {
  title: "Turnover · Cleaning coordination",
  description: "Cleaning schedules and cleaner coordination for short-term rentals",
  manifest: "/manifest.webmanifest",
  icons: {
    icon: [
      { url: "/favicon.svg", type: "image/svg+xml" },
      { url: "/favicon-32.png", sizes: "32x32", type: "image/png" },
      { url: "/favicon-16.png", sizes: "16x16", type: "image/png" },
    ],
    apple: [
      { url: "/brand/apple-touch-icon.png", sizes: "180x180", type: "image/png" },
    ],
  },
};

export const viewport: Viewport = {
  themeColor: "#253122",
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  const connected = Boolean((await cookies()).get(sessionTokenCookie)?.value);
  return (
    <html lang="en">
      <head>
        <link rel="preload" href="/fonts/Inter-Variable.woff2" as="font" type="font/woff2" crossOrigin="anonymous" />
      </head>
      <body>
        <AppFrame workspaceSignup={workspaceSignupEnabled} connected={connected} demo={process.env.LOCAL_DEMO === "1"} sourceMode={workspaceMode}>{children}</AppFrame>
      </body>
    </html>
  );
}
