"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, useSyncExternalStore, type FormEvent } from "react";
import { api, type ApiError } from "../lib/api";
import { readingLanguages } from "./ReadingLanguages";

type Setup = {connected: boolean; house_count: number; cleaner_count: number; completed: boolean};
type Cleaner = {key: string; name: string; email: string; preferred_language: string; saved: boolean; submitted: boolean};
type SavedCleaner = {id: string; name: string; email: string | null; preferred_language: string | null};

const invitationSnapshot = () => new URLSearchParams(window.location.hash.slice(1)).get("invite") ?? "";
const serverInvitationSnapshot = () => null;
const subscribeToInvitation = (listener: () => void) => {
  window.addEventListener("hashchange", listener);
  return () => window.removeEventListener("hashchange", listener);
};

export function WorkspaceSignup({login, authenticated, internalConnection = false, registrationEnabled = true, inviteSignupEnabled = false}: {login: boolean; authenticated: boolean; internalConnection?: boolean; registrationEnabled?: boolean; inviteSignupEnabled?: boolean}) {
  const router = useRouter();
  const [setup, setSetup] = useState<Setup | null>(null);
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [connectionOpen, setConnectionOpen] = useState(false);
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [secondFactor, setSecondFactor] = useState("");
  const [mfaRequired, setMfaRequired] = useState(false);
  const [token, setToken] = useState("");
  const [invitationToken, setInvitationToken] = useState("");
  const invitationLink = useSyncExternalStore(subscribeToInvitation, invitationSnapshot, serverInvitationSnapshot);
  const invitation = invitationToken || invitationLink;
  const [cleaners, setCleaners] = useState<Cleaner[]>([]);
  const keys = useRef({account: "", connection: "", complete: ""});
  const step = !authenticated ? 1 : setup?.connected ? 3 : 2;

  useEffect(() => {
    if (!authenticated) return;
    let active = true;
    void api<Setup>("/workspace/setup").then(async value => {
      if (!active) return;
      if (value.completed) { router.replace("/dashboard"); return; }
      if (value.connected) {
        const response = await api<{cleaners: SavedCleaner[]}>("/workspace/cleaners");
        if (!active) return;
        setCleaners(current => {
          const savedRows = response.cleaners.map(row => ({key: row.id, name: row.name, email: row.email ?? "", preferred_language: row.preferred_language ?? "", saved: true, submitted: false}));
          const savedKeys = new Set(savedRows.map(row => row.key));
          const rows = [...savedRows, ...current.filter(row => !row.saved && !savedKeys.has(row.key))];
          return rows.length ? rows : [{key: crypto.randomUUID(), name: "", email: "", preferred_language: "", saved: false, submitted: false}];
        });
      }
      setError("");
      setSetup(value);
    }).catch((failure: ApiError) => {
      if (!active) return;
      if (failure.status === 401) { router.replace("/login"); return; }
      setError("Could not load your workspace. Please try again.");
    });
    return () => { active = false; };
  }, [authenticated, router, reload]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      if (step === 1) {
        if (!login && !registrationEnabled && (!inviteSignupEnabled || !invitation)) throw new Error("Open your private invitation link to create an account.");
        keys.current.account ||= crypto.randomUUID();
        const response = await fetch(`/auth/${login ? "login" : "signup"}`, {method: "POST", headers: {"Content-Type": "application/json", "Idempotency-Key": keys.current.account}, body: JSON.stringify(login ? {email, password, ...(secondFactor ? {second_factor: secondFactor} : {})} : {name, email, password, ...(invitationToken ? {invitation_token: invitationToken} : {})}), signal: AbortSignal.timeout(25000)});
        if (!response.ok) {
          const body = await response.json() as {error?: {message?: string; code?: string}};
          if (login && ["mfa_required", "invalid_second_factor"].includes(body.error?.code ?? "")) setMfaRequired(true);
          throw new Error(body.error?.message ?? "Could not sign in. Please try again.");
        }
        setPassword(""); setSecondFactor(""); router.replace("/signup"); router.refresh();
      } else if (step === 2) {
        keys.current.connection ||= crypto.randomUUID();
        await api("/workspace/hospitable", {method: "POST", headers: {"Idempotency-Key": keys.current.connection}, body: JSON.stringify({token}), signal: AbortSignal.timeout(670000)});
        setToken(""); setSetup(null); setReload(current => current + 1);
      } else {
        for (const cleaner of cleaners) {
          if (cleaner.saved || (!cleaner.name.trim() && !cleaner.email.trim())) continue;
          setCleaners(current => current.map(row => row.key === cleaner.key ? {...row, submitted: true} : row));
          let created: {id: string};
          try {
            created = await api<{id: string}>("/workspace/cleaners", {method: "POST", headers: {"Idempotency-Key": cleaner.key}, body: JSON.stringify({name: cleaner.name.trim(), email: cleaner.email.trim(), preferred_language: cleaner.preferred_language || null})});
          } catch (failure) {
            if ([400, 422].includes((failure as ApiError).status)) setCleaners(current => current.map(row => row.key === cleaner.key ? {...row, submitted: false} : row));
            throw failure;
          }
          setCleaners(current => current.filter(row => row.key !== created.id || row.key === cleaner.key).map(row => row.key === cleaner.key ? {...row, key: created.id, saved: true, submitted: false} : row));
        }
        keys.current.complete ||= crypto.randomUUID();
        await api("/workspace/complete", {method: "POST", headers: {"Idempotency-Key": keys.current.complete}, body: "{}"});
        router.replace("/dashboard"); router.refresh();
      }
    } catch (failure) { if (step === 2 && (failure as ApiError).status === 422) keys.current.connection = ""; setError(failure instanceof Error ? failure.message : "Could not save. Please try again."); }
    finally { setBusy(false); }
  }

  const invitationRequired = !authenticated && !login && !registrationEnabled;
  const unsaved = cleaners.some(row => !row.saved && (row.name.trim() || row.email.trim()));
  const saved = cleaners.some(row => row.saved);
  return <main className="workspace-signup">
    {authenticated ? <p><Link href="/security">Account settings</Link></p> : null}
    <section className={`workspace-card${step === 1 && !login && !accountOpen || step === 2 && !connectionOpen ? " workspace-card-action" : ""}${step === 2 && internalConnection && connectionOpen ? " workspace-connection" : ""}`}>
      {invitationRequired && (!inviteSignupEnabled || !invitation) ? <><h2>{invitation === null ? "Opening your invitation…" : "Invitation required"}</h2>{invitation === null ? <p role="status">Checking your invitation link.</p> : <><p>Open your private invitation link to create an account.</p><Link href="/login">Sign in</Link></>}</> : authenticated && !setup ? <><h2>Opening your workspace…</h2>{error ? <><p role="alert">{error}</p><button onClick={() => {setError(""); setReload(current => current + 1);}}>Try again</button></> : <p role="status">Loading your saved progress.</p>}</> : <>
      <form onSubmit={submit}>
        {step === 1 && !login && !accountOpen ? <><Image src="/illustrations/G11-manager-first-run-light.svg" width={320} height={200} alt="" aria-hidden="true" className="workspace-welcome-illustration" /><button type="button" onClick={() => {
          setInvitationToken(invitationLink ?? "");
          if (invitationLink) window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
          setAccountOpen(true);
        }}>Create account</button></> : null}
        {step === 1 && !login && !accountOpen ? <p className="workspace-switch">Already have an account? <Link href="/login">Sign in</Link></p> : null}
        {step === 1 && (login || accountOpen) ? <><h2>{login ? "Sign in" : "Create your account"}</h2>{!login ? <label>Your name<input value={name} onChange={event => {setName(event.target.value); keys.current.account = "";}} autoComplete="name" maxLength={120} required disabled={busy} /></label> : null}<label>Email<input type="email" value={email} onChange={event => {setEmail(event.target.value); setMfaRequired(false); setSecondFactor(""); keys.current.account = "";}} autoComplete="email" maxLength={254} required disabled={busy} /></label><label>Password<input type="password" value={password} onChange={event => {setPassword(event.target.value); keys.current.account = "";}} autoComplete={login ? "current-password" : "new-password"} minLength={login ? 1 : 12} maxLength={128} required disabled={busy} />{!login ? <small>Use at least 12 characters.</small> : null}</label>{login && mfaRequired ? <label>Authenticator or recovery code<input value={secondFactor} onChange={event => {setSecondFactor(event.target.value); keys.current.account = "";}} autoComplete="one-time-code" autoCapitalize="none" spellCheck={false} maxLength={64} required disabled={busy} /><small>Enter the six-digit code from your authenticator app, or one unused recovery code.</small></label> : null}<button disabled={busy}>{busy ? "Please wait…" : login ? "Sign in" : "Create account"}</button><p className="workspace-switch">{login ? registrationEnabled ? <>New here? <Link href="/signup">Create an account</Link></> : null : <>Already have an account? <Link href="/login">Sign in</Link></>}</p></> : null}
        {step === 2 && (!connectionOpen || !internalConnection) ? <><button type="button" disabled={!internalConnection} onClick={() => setConnectionOpen(true)}>Connect to Hospitable</button>{connectionOpen || !internalConnection ? <p className="workspace-note" role="status">Hospitable authorization is not configured yet. Your account and setup progress are saved. You can continue here once the connection is available.</p> : null}</> : null}
        {step === 2 && internalConnection && connectionOpen ? <>
          <div className="workspace-connection-top"><span className="workspace-connection-mark" aria-hidden="true">H</span><span className="workspace-read-only">Read-only access</span></div>
          <div className="workspace-connection-title"><h2>Connect Hospitable</h2><p>Bring your houses and reservations into Turnover.</p></div>
          <label>Access token<input type="password" autoComplete="off" value={token} onChange={event => {setToken(event.target.value); keys.current.connection = "";}} required disabled={busy} spellCheck={false} aria-describedby="workspace-token-hint" placeholder="Paste your read-only token" /><small id="workspace-token-hint">Encrypted and saved in your workspace.</small></label>
          <details className="workspace-token-help">
            <summary>How to get your access token</summary>
            <ol>
              <li><div><strong>Find API access</strong><p>Search for <b>API access</b> in Hospitable and open it.</p></div></li>
              <li><div><strong>Create a token</strong><p>Click <b>+ Add new</b>.</p></div></li>
              <li><div><strong>Choose Read only</strong><p>Keep <b>Write</b> access turned off.</p></div></li>
              <li><div><strong>Copy and paste</strong><p>Copy the <b>Access Token</b>, then paste it into the field above.</p></div></li>
              <li><div><strong>Connect your account</strong><p>Click <b>Connect Hospitable</b> below.</p></div></li>
            </ol>
            <a href="https://help.hospitable.com/en/articles/8609392-accessing-the-public-api-with-a-personal-access-token-pat" target="_blank" rel="noreferrer">View Hospitable’s full guide ↗</a>
          </details>
          <button className="workspace-connect-submit" disabled={busy}>{busy ? "Importing your houses…" : "Connect Hospitable"}</button>
          <p className="workspace-connection-assurance">Your Hospitable account stays unchanged.</p>
          {busy ? <small role="status">Large accounts can take up to 10 minutes. Keep this page open.</small> : null}
        </> : null}
        {step === 3 ? <><h2>Add your cleaners</h2><p>Add names and emails. We’ll email each cleaner a link to set their hours, breaks and starting location when you create cleaners and finish setup.</p><div className="workspace-cleaners">{cleaners.map((cleaner, index) => <fieldset key={cleaner.key} disabled={busy || cleaner.saved || cleaner.submitted}><legend>Cleaner {index + 1}</legend><div className="workspace-cleaner-fields"><label>Name<input autoComplete="off" value={cleaner.name} maxLength={80} required={Boolean(cleaner.email)} onChange={event => setCleaners(current => current.map(row => row.key === cleaner.key ? {...row, name: event.target.value} : row))} /></label><label>Email<input type="email" autoComplete="off" value={cleaner.email} maxLength={254} required={Boolean(cleaner.name)} onChange={event => setCleaners(current => current.map(row => row.key === cleaner.key ? {...row, email: event.target.value} : row))} /></label><label>Email language<select value={cleaner.preferred_language} onChange={event => setCleaners(current => current.map(row => row.key === cleaner.key ? {...row, preferred_language: event.target.value} : row))}><option value="">English, Spanish & Portuguese</option>{readingLanguages.map(([code, label]) => <option key={code} value={code} lang={code}>{label}</option>)}</select></label>{!cleaner.saved && !cleaner.submitted ? <button type="button" className="workspace-remove" aria-label={`Remove cleaner ${index + 1}`} onClick={() => setCleaners(current => current.filter(row => row.key !== cleaner.key))}>Remove</button> : null}</div>{cleaner.submitted && !busy ? <small role="status">Save not confirmed. Retry below to finish saving these details safely.</small> : null}</fieldset>)}</div><button type="button" className="workspace-secondary" disabled={busy} onClick={() => setCleaners(current => [...current, {key: crypto.randomUUID(), name: "", email: "", preferred_language: "", saved: false, submitted: false}])}>+ Add another cleaner</button></> : null}
      {step === 3 ? <><p className="workspace-finish-note">Finish setup enables standard cleaning times and automatic assignments.</p><button className="workspace-finish" disabled={busy || (!unsaved && !saved)}>{busy ? "Finishing setup…" : cleaners.some(row => row.submitted) ? "Retry & finish setup" : "Create cleaners & finish"}</button>{!saved && !unsaved ? <small>Add at least one cleaner to finish setup.</small> : null}</> : null}
      </form>
      {error ? <p role="alert">{error}</p> : null}</>}
    </section>
  </main>;
}
