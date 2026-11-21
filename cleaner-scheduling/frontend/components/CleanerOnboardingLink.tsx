"use client";

import { useRef, useState } from "react";
import { api } from "../lib/api";

export function CleanerOnboardingLink({cleaner}: {cleaner: string}) {
  const [link, setLink] = useState("");
  const [expiry, setExpiry] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const key = useRef<string | null>(null);
  async function create() {
    setBusy(true); setMessage("");
    key.current ??= crypto.randomUUID();
    try {
      const result = await api<{token: string; expires_at: string}>(`/cleaners/${cleaner}/onboarding-link`, {method: "POST", headers: {"Idempotency-Key": key.current}, body: JSON.stringify({ttl_hours: 168})});
      setLink(`${window.location.origin}/onboarding#${result.token}`);
      setExpiry(new Intl.DateTimeFormat("en-US", {timeZone: "America/New_York", dateStyle: "medium", timeStyle: "short"}).format(new Date(result.expires_at)));
      key.current = null;
    } catch {setMessage("The link could not be created. Try again.");}
    finally {setBusy(false);}
  }
  return <section aria-label="Cleaner onboarding link"><h3>Cleaner setup link</h3><p>They choose their workdays, usual hours and starting address.</p>
    <button type="button" disabled={busy} onClick={() => void create()}>{busy ? "Creating…" : link ? "Replace onboarding link" : "Create onboarding link"}</button>
    {link ? <div className="cleaner-onboarding-link"><label>Private onboarding link<input readOnly value={link} onFocus={event => event.currentTarget.select()} /></label><p>Private · Expires {expiry} Eastern.</p><a href={link} target="_blank" rel="noreferrer">Open cleaner preview</a><button type="button" onClick={() => void navigator.clipboard.writeText(link).then(() => setMessage("Link copied.")).catch(() => setMessage("Select the link above and copy it manually."))}>Copy link</button></div> : null}
    {link ? <p className="muted">Replacing this link disables the previous one.</p> : null}
    {message ? <p role="status">{message}</p> : null}
  </section>;
}
