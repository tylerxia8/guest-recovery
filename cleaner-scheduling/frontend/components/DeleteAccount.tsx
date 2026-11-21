"use client";

import { useState, type FormEvent } from "react";

export function DeleteAccount({mfaEnabled, onCancel}: {mfaEnabled: boolean; onCancel: () => void}) {
  const [password, setPassword] = useState("");
  const [secondFactor, setSecondFactor] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [pending, setPending] = useState<{key: string; body: string} | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || confirmation !== "DELETE") return;
    const change = pending ?? {key: crypto.randomUUID(), body: JSON.stringify({action: "delete_account", password, confirmation, ...(mfaEnabled ? {second_factor: secondFactor} : {})})};
    setPending(change); setBusy(true); setError("");
    try {
      const response = await fetch("/auth/security", {method: "PATCH", headers: {"Content-Type": "application/json", "Idempotency-Key": change.key}, body: change.body, cache: "no-store", signal: AbortSignal.timeout(25000)});
      const body = await response.json() as {deleted?: boolean; error?: {message?: string}};
      if (!response.ok) {
        if (response.status < 500 && ![408, 429].includes(response.status)) {
          setPending(null);
          throw new Error(body.error?.message ?? "Could not delete your account. Check your details and try again.");
        }
        throw new Error("Deletion is not confirmed. Wait a moment, then retry the same request below.");
      }
      if (body.deleted !== true) throw new Error("Deletion is not confirmed. Retry the same request below.");
      window.location.replace("/login");
    } catch (failure) {
      setError(failure instanceof Error && failure.name === "Error" ? failure.message : "Deletion is not confirmed. Retry the same request below.");
      setBusy(false);
    }
  }

  return <>
    <h3>Are you sure?</h3>
    <p>Deleting your account permanently removes your access to this workspace and signs out all your sessions. Automatic workspace activity and future automated emails and text messages will stop. Messages already sent cannot be recalled.</p>
    <p>Some historical operational and audit records are retained. This action cannot be undone.</p>
    <form onSubmit={submit}>
      <label>Current password<input type="password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="current-password" maxLength={128} required disabled={busy || Boolean(pending)} /></label>
      {mfaEnabled ? <label>Authenticator or recovery code<input value={secondFactor} onChange={event => setSecondFactor(event.target.value)} autoComplete="one-time-code" autoCapitalize="none" spellCheck={false} maxLength={64} required disabled={busy || Boolean(pending)} /></label> : null}
      <label>Type DELETE to confirm<input value={confirmation} onChange={event => setConfirmation(event.target.value)} autoComplete="off" autoCapitalize="characters" spellCheck={false} pattern="DELETE" maxLength={6} required disabled={busy || Boolean(pending)} /></label>
      {pending && !busy ? <p role="status">Deletion may already have completed. Your original inputs are held unchanged so you can safely retry the same request.</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      <div className="security-actions">
        <button disabled={busy || confirmation !== "DELETE"}>{busy ? "Deleting account…" : pending ? "Retry the same deletion request" : "Delete account permanently"}</button>
        <button type="button" className="workspace-secondary" onClick={onCancel} disabled={busy || Boolean(pending)}>Cancel</button>
      </div>
    </form>
  </>;
}
