"use client";

import { useEffect, useState, type FormEvent } from "react";
import { api } from "../lib/api";

type Manager = {id: string; email: string};

export function WorkspaceManagers() {
  const [managers, setManagers] = useState<Manager[]>([]);
  const [email, setEmail] = useState("");
  const [link, setLink] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void api<{managers: Manager[]}>("/workspace/managers", {signal: controller.signal})
      .then(response => setManagers(response.managers))
      .catch(() => { if (!controller.signal.aborted) setError("Could not load workspace managers."); });
    return () => controller.abort();
  }, []);

  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true); setError(""); setLink(""); setCopied(false);
    try {
      const result = await api<{invitation_token: string}>("/workspace/manager-invitations", {
        method: "POST", body: JSON.stringify({email}),
      });
      setLink(new URL(`/join#invite=${encodeURIComponent(result.invitation_token)}`, window.location.origin).href);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not create the invitation.");
    } finally {
      setBusy(false);
    }
  }

  async function remove(manager: Manager) {
    if (!window.confirm(`Remove workspace access for ${manager.email}?`)) return;
    setBusy(true); setError("");
    try {
      await api(`/workspace/managers/${manager.id}`, {method: "DELETE"});
      setManagers(current => current.filter(member => member.id !== manager.id));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not remove the manager.");
    } finally {
      setBusy(false);
    }
  }

  return <section className="workspace-card" aria-labelledby="workspace-managers-title">
    <h2 id="workspace-managers-title">Workspace managers</h2>
    <p>Invite a manager to use your company’s Hospitable connection with their own sign-in across Cleaner, Guest Messages and Maintenance.</p>
    <form onSubmit={invite}>
      <label>Manager email<input type="email" maxLength={254} value={email} onChange={event => {setEmail(event.target.value); setLink("");}} required disabled={busy} /></label>
      <button disabled={busy}>Create invite link</button>
    </form>
    {link ? <>
      <label>Private invite link<input readOnly value={link} onFocus={event => event.target.select()} /></label>
      <p>Send this link privately to {email}. It expires in 24 hours.</p>
      <button type="button" onClick={async () => {
        try { await navigator.clipboard.writeText(link); setCopied(true); }
        catch { setError("Select and copy the link above."); }
      }}>{copied ? "Copied" : "Copy invite link"}</button>
    </> : null}
    {managers.length ? <ul>{managers.map(manager => <li key={manager.id}>{manager.email} <button type="button" aria-label={`Remove ${manager.email}`} disabled={busy} onClick={() => void remove(manager)}>Remove</button></li>)}</ul> : null}
    {error ? <p role="alert">{error}</p> : null}
  </section>;
}
