import type { ReactNode } from "react";
import "./globals.css";
import { operationsOrigins } from "../lib/operations-login";
import WorkspaceShell from "./workspace-shell";

export const metadata = {
  title: "Lucena Repairs",
  description:
    "Report a problem and follow the repair.",
};

export const dynamic = "force-dynamic";

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a className="skip-link" href="#main">
          Skip to content
        </a>
        <WorkspaceShell
          demo={process.env.NEXT_PUBLIC_DEMO_FIXTURE === "sample"}
          turnoverOrigin={operationsOrigins()?.turnover}
        >
          {children}
        </WorkspaceShell>
      </body>
    </html>
  );
}
