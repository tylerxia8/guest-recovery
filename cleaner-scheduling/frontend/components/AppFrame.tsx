"use client";
import type { WorkspaceMode } from "../lib/session";

import { LayoutDashboard, Users, RefreshCw, Settings } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { ShellBanner } from "./ShellBanner";

const NAV_LINKS = [
  { label: "Dashboard", href: "/dashboard", icon: LayoutDashboard },
  { label: "Cleaners", href: "/cleaners", icon: Users },
  { label: "Hospitable", href: "/reservations", icon: RefreshCw },
] as const;

const subscribe = () => () => {};
const clientReady = () => true;
const serverReady = () => false;

export function AppFrame({ children, demo = false, connected = false, sourceMode = "mock", workspaceSignup = false }: { children: ReactNode; demo?: boolean; connected?: boolean; sourceMode?: WorkspaceMode; workspaceSignup?: boolean }) {
  const liveReadOnly = sourceMode === "hospitable_read_only";
  const pathname = usePathname();
  const router = useRouter();
  const [token, setToken] = useState("");
  const [role, setRole] = useState("manager");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [sessionExpired, setSessionExpired] = useState(false);
  useEffect(() => {
    const expired = () => setSessionExpired(true);
    window.addEventListener("session-expired", expired);
    return () => window.removeEventListener("session-expired", expired);
  }, []);
  const hydrated = useSyncExternalStore(subscribe, clientReady, serverReady);
  async function connect() {
    setBusy(true); setMessage("");
    try {
      const response = await fetch("/session", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({connection: "hospitable"}), signal: AbortSignal.timeout(20000)});
      if (!response.ok) throw new Error("Connection unavailable");
      // Reload the shell so every server-rendered permission reads the new session cookies.
      window.location.assign(new URL(sessionExpired ? pathname : liveReadOnly ? "/reservations" : "/dashboard", window.location.href).toString());
    } catch {
      setMessage(liveReadOnly ? "Could not open the local Hospitable workspace. Check that the API is running." : "Could not connect to the simulated account. Check that the demo API is running and try again.");
      setBusy(false);
    }
  }

  if (pathname === "/onboarding" || pathname === "/cleaner" || pathname === "/signup" || pathname === "/login") return <>{children}</>;

  if (workspaceSignup) return <>
    <header className="app-header"><Link href="/dashboard" className="brand"><Image src="/brand/turnover-mark.svg" alt="" width={42} height={42} className="brand-symbol" aria-hidden="true" /><span className="brand-wordmark">Turnover<small>Cleaning coordination</small></span></Link>{connected ? <div className="account-actions"><Link href="/security" aria-current={pathname === "/security" ? "page" : undefined}>Security</Link><button type="button" disabled={busy} onClick={async () => {
      setBusy(true); setMessage("");
      try {
        const response = await fetch("/auth/logout", {method: "POST", signal: AbortSignal.timeout(20000)});
        if (!response.ok) throw new Error("Could not sign out. Try again.");
        router.replace("/login"); router.refresh();
      } catch { setMessage("Could not sign out. Try again."); setBusy(false); }
    }}>Sign out</button></div> : <Link href={pathname === "/login" ? "/signup" : "/login"}>{pathname === "/login" ? "Create account" : "Sign in"}</Link>}</header>
    {connected && pathname !== "/signup" && pathname !== "/login" ? <nav aria-label="Primary">{NAV_LINKS.map(({ label, href, icon: Icon }) => <Link key={href} href={href} aria-current={pathname === href ? "page" : undefined}><Icon size={16} strokeWidth={2} aria-hidden="true" />{label}</Link>)}</nav> : null}
    {sessionExpired ? <p role="alert">Your session expired. <Link href="/login">Sign in to continue</Link>.</p> : null}
    {message ? <p role="alert">{message}</p> : null}{children}
  </>;

  return (
    <>
      <header className="app-header"><Link href="/" className="brand" aria-label="Turnover home"><Image src="/brand/turnover-mark.svg" alt="" width={42} height={42} className="brand-symbol" aria-hidden="true" /><span className="brand-wordmark">Turnover<small>Cleaning coordination</small></span></Link>{!liveReadOnly ? <div className="app-header-meta"><span className="mode-pill"><span className="mode-dot" aria-hidden="true" />{demo ? "Synthetic demo · Nov 10, 2026 · 1 PM ET" : "Mock workspace"}</span></div> : null}</header>
      {liveReadOnly ? null : <ShellBanner />}
      <nav aria-label="Primary">
        {NAV_LINKS.map(({ label, href, icon: Icon }) => (
          <Link key={href} href={href} aria-current={pathname === href ? "page" : undefined}>
            <Icon size={16} strokeWidth={2} aria-hidden="true" />{label}
          </Link>
        ))}
        {liveReadOnly ? <Link href="/setup" aria-current={pathname === "/setup" ? "page" : undefined}><Settings size={16} strokeWidth={2} aria-hidden="true" />Setup</Link> : null}
      </nav>
      {!liveReadOnly || !connected || sessionExpired ? <div className="session-bar">
        {sessionExpired ? <span role="status">Your session expired. Reconnect to continue.</span> : null}
        {(demo || liveReadOnly) && connected && !sessionExpired ? <span aria-label="Hospitable connection">Hospitable connected · {liveReadOnly ? "read-only account access" : "simulated account"}</span> : <button type="button" disabled={(!demo && !liveReadOnly) || !hydrated || busy} onClick={() => void connect()}>{busy ? "Connecting…" : sessionExpired ? "Reconnect" : "Connect to Hospitable"}</button>}
        {!liveReadOnly ? <span className="muted">{demo ? "Demo connection only. No Hospitable credentials needed." : "Live Hospitable connection is not available yet."}</span> : null}
      </div> : null}
      {!demo && !liveReadOnly ? <details className="developer-signin"><summary>Developer sign-in</summary>
      <form
        className="session-bar"
        onSubmit={(event) => {
          event.preventDefault();
          setMessage("");
          fetch("/session", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token, role }),
            signal: AbortSignal.timeout(20000),
          })
            .then(async (response) => {
              if (!response.ok) {
                setMessage("Development sign-in was rejected.");
                return;
              }
              window.location.reload();
            })
            .catch(() => setMessage("Development sign-in was rejected."));
        }}
      >
        <label>
          Development token
          <input type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} />
        </label>
        <label>
          Sign in as
          <select aria-label="Sign in as" value={role} onChange={(event) => setRole(event.target.value)}>
            <option value="viewer">viewer</option>
            <option value="manager">manager</option>
            <option value="owner">owner</option>
          </select>
        </label>
        <button type="submit">Use token</button>
      </form>
      </details> : null}
      {message ? <p role="alert">{message}</p> : null}
      {children}
      <footer>Turnover · America/New_York · {liveReadOnly ? "Read-only Hospitable import · No upstream changes" : "Synthetic data and mock integrations"}</footer>
    </>
  );
}
