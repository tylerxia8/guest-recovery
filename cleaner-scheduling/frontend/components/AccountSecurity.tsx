"use client";

import { useEffect, useState, type FormEvent } from "react";
import { DeleteAccount } from "./DeleteAccount";

type SecurityState = {stage: "disabled"} | {stage: "enabled"; remaining: number} | {stage: "setup"; secret: string; expiresAt: string} | {stage: "recovery"; codes: string[]};
type PendingChange = {action: "begin_mfa" | "confirm_mfa" | "disable_mfa"; key: string; body: string};
type SecurityResponse = {mfa_enabled?: boolean; recovery_codes_remaining?: number; secret?: string; expires_at?: string; recovery_codes?: string[]; error?: {message?: string}};

export function AccountSecurity() {
  const [security, setSecurity] = useState<SecurityState | null>(null);
  const [password, setPassword] = useState("");
  const [secondFactor, setSecondFactor] = useState("");
  const [pending, setPending] = useState<PendingChange | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [reload, setReload] = useState(0);
  const [deletionOpen, setDeletionOpen] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch("/auth/security", {cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(25000)])});
        if (!response.ok) throw new Error(response.status === 401 ? "Sign in again to manage your security settings." : "Could not load your security settings. Please try again.");
        const body = await response.json() as SecurityResponse;
        const remaining = body.recovery_codes_remaining;
        if (typeof body.mfa_enabled !== "boolean" || typeof remaining !== "number" || !Number.isInteger(remaining) || remaining < 0 || remaining > 8) throw new Error("Could not load your security settings. Please try again.");
        if (!controller.signal.aborted) setSecurity(body.mfa_enabled ? {stage: "enabled", remaining} : {stage: "disabled"});
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Could not load your security settings. Please try again.");
      }
    }
    void load();
    return () => controller.abort();
  }, [reload]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!security || security.stage === "recovery" || busy) return;
    const action = security.stage === "setup" ? "confirm_mfa" : security.stage === "enabled" ? "disable_mfa" : "begin_mfa";
    const change = pending ?? {action, key: crypto.randomUUID(), body: JSON.stringify({action, password, ...(action !== "begin_mfa" ? {second_factor: secondFactor} : {})})};
    setPending(change); setBusy(true); setError(""); setMessage("");
    try {
      const response = await fetch("/auth/security", {method: "PATCH", headers: {"Content-Type": "application/json", "Idempotency-Key": change.key}, body: change.body, cache: "no-store", signal: AbortSignal.timeout(25000)});
      const body = await response.json() as SecurityResponse;
      if (!response.ok) {
        if (response.status < 500 && ![408, 429].includes(response.status)) setPending(null);
        throw new Error(response.status >= 500 || [408, 429].includes(response.status) ? "The result is not confirmed. Wait a moment, then retry the same request below." : body.error?.message ?? "Could not update your security settings.");
      }
      if (change.action === "begin_mfa") {
        if (body.mfa_enabled !== false || typeof body.secret !== "string" || !/^[A-Z2-7]+$/.test(body.secret) || typeof body.expires_at !== "string" || !Number.isFinite(Date.parse(body.expires_at))) throw new Error("The result is not confirmed. Retry the same request below.");
        setSecurity({stage: "setup", secret: body.secret, expiresAt: body.expires_at});
      } else if (change.action === "confirm_mfa") {
        if (body.mfa_enabled !== true || !Array.isArray(body.recovery_codes) || body.recovery_codes.length !== 8 || !body.recovery_codes.every(code => typeof code === "string" && code.length > 0)) throw new Error("The result is not confirmed. Retry the same request below.");
        setSecurity({stage: "recovery", codes: body.recovery_codes});
        setPassword("");
      } else {
        if (body.mfa_enabled !== false) throw new Error("The result is not confirmed. Retry the same request below.");
        setSecurity({stage: "disabled"}); setPassword("");
        setMessage("Two-factor authentication is disabled. Your other sessions have been signed out.");
      }
      setPending(null); setSecondFactor("");
    } catch (failure) {
      setError(failure instanceof Error && failure.name === "Error" ? failure.message : "The result is not confirmed. Retry the same request below.");
    } finally {
      setBusy(false);
    }
  }

  return <main className="account-security">
    <h1>Account security</h1>
    <section className="workspace-card" aria-labelledby="mfa-title" hidden={deletionOpen}>
      <h2 id="mfa-title">Two-factor authentication</h2>
      {!security ? <>{error ? <button onClick={() => {setError(""); setReload(current => current + 1);}}>Try again</button> : <p role="status">Loading your security settings…</p>}</> : security.stage === "recovery" ? <>
        <p role="status">Two-factor authentication is enabled. Your other sessions have been signed out.</p>
        <h3>Save your recovery codes</h3>
        <p>Keep these codes somewhere safe, such as your password manager. Each code can be used once if you cannot access your authenticator app. They will not be shown again after you leave this page.</p>
        <label>Recovery codes<textarea readOnly value={security.codes.join("\n")} rows={8} spellCheck={false} autoComplete="off" /></label>
        <div className="security-actions"><button type="button" onClick={async () => {
          try { await navigator.clipboard.writeText(security.codes.join("\n")); setMessage("Recovery codes copied."); setError(""); }
          catch { setError("Could not copy. Select the codes above and copy them manually."); }
        }}>Copy recovery codes</button><button type="button" onClick={() => {setSecurity({stage: "enabled", remaining: security.codes.length}); setMessage("Recovery codes saved."); setError("");}}>I’ve saved my recovery codes</button></div>
      </> : <>
        <p role="status">{security.stage === "enabled" ? `Enabled · ${security.remaining} recovery codes remaining` : "Not enabled"}</p>
        {security.stage === "disabled" ? <p>Protect your account with a six-digit code from an authenticator app. Confirm your password to start setup.</p> : security.stage === "enabled" ? <p>Turning this off removes your authenticator and all remaining recovery codes. Confirm your password and an authenticator or recovery code to continue.</p> : <>
          <p>In your authenticator app, add an account with this manual setup key. Choose time-based codes. This setup key expires at {new Date(security.expiresAt).toLocaleTimeString([], {hour: "numeric", minute: "2-digit"})}, 10 minutes after setup starts.</p>
          <label>Manual setup key<input readOnly value={security.secret} autoComplete="off" spellCheck={false} /></label>
          <p>Enter the six-digit code from your app to finish enabling two-factor authentication.</p>
        </>}
        <form onSubmit={submit}>
          <label>Current password<input type="password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="current-password" maxLength={128} required disabled={busy || Boolean(pending)} /></label>
          {security.stage !== "disabled" ? <label>{security.stage === "setup" ? "Authenticator code" : "Authenticator or recovery code"}<input value={secondFactor} onChange={event => setSecondFactor(event.target.value)} autoComplete="one-time-code" autoCapitalize="none" spellCheck={false} inputMode={security.stage === "setup" ? "numeric" : "text"} pattern={security.stage === "setup" ? "[0-9]{6}" : undefined} minLength={security.stage === "setup" ? 6 : 1} maxLength={security.stage === "setup" ? 6 : 64} required disabled={busy || Boolean(pending)} /></label> : null}
          {pending && !busy ? <p role="status">Your original inputs are held unchanged so retrying cannot apply a different request.</p> : null}
          <button disabled={busy}>{busy ? "Please wait…" : pending ? "Retry the same request" : security.stage === "setup" ? "Enable two-factor authentication" : security.stage === "enabled" ? "Disable two-factor authentication" : "Set up authenticator"}</button>
          {security.stage === "setup" && !pending ? <button type="button" className="workspace-secondary" onClick={() => {setSecurity({stage: "disabled"}); setPassword(""); setSecondFactor(""); setError("");}}>Cancel setup</button> : null}
        </form>
      </>}
      {error ? <p role="alert">{error}</p> : null}
      {message ? <p role="status">{message}</p> : null}
    </section>
    {security && ["enabled", "disabled"].includes(security.stage) && !pending && !busy ? <section className="workspace-card" aria-labelledby="delete-account-title">
      <h2 id="delete-account-title">Delete account</h2>
      {deletionOpen ? <DeleteAccount mfaEnabled={security.stage === "enabled"} onCancel={() => setDeletionOpen(false)} /> : <>
        <p>Permanently close your account and stop automated workspace communications.</p>
        <button type="button" onClick={() => {setPassword(""); setSecondFactor(""); setDeletionOpen(true);}}>Delete account</button>
      </>}
    </section> : null}
  </main>;
}
