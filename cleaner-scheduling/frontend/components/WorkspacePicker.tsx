"use client";

import Link from "next/link";
import { useEffect, useState, useSyncExternalStore } from "react";

type Workspaces = {current: string; items: {id: string; name: string}[]};

function subscribe(listener: () => void) {
  window.addEventListener("hashchange", listener);
  return () => window.removeEventListener("hashchange", listener);
}

async function workspaceRequest(path: string, body?: object) {
  const response = await fetch(`/auth/${path}`, {
    method: body ? "POST" : "GET",
    ...(body ? {headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : {}),
    cache: "no-store", signal: AbortSignal.timeout(15000),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error?.message ?? "Could not open your workspace.");
  return result;
}

export function WorkspacePicker({authenticated, invitation = false}: {authenticated: boolean; invitation?: boolean}) {
  const token = useSyncExternalStore(
    subscribe,
    () => new URLSearchParams(window.location.hash.slice(1)).get("invite"),
    () => null,
  );
  const [workspaces, setWorkspaces] = useState<Workspaces>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [joined, setJoined] = useState(false);
  const valid = Boolean(token && /^team_[A-Za-z0-9_-]{43}$/.test(token));

  useEffect(() => {
    if (invitation) return;
    let active = true;
    workspaceRequest("workspaces").then(value => {if (active) setWorkspaces(value);})
      .catch(failure => {if (active) setError(failure.message);});
    return () => {active = false;};
  }, [invitation]);

  async function submit(path: "join" | "switch-workspace", body: object) {
    setBusy(true); setError("");
    try {
      await workspaceRequest(path, body);
      if (path === "join") {
        window.history.replaceState(window.history.state, "", "/join");
        setJoined(true);
      } else {
        window.location.replace("/dashboard");
      }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not complete the request.");
    } finally {
      setBusy(false);
    }
  }

  return <main className="workspace-signup"><section className="workspace-card">
    <h1>{invitation ? "Join a workspace" : "Switch workspace"}</h1>
    {invitation ? joined ? <>
      <p role="status">You joined the workspace.</p><Link href="/workspaces">Choose workspace</Link>
    </> : valid ? authenticated ? <>
      <p>Accept the invitation with your current Cleaner login.</p>
      <button type="button" disabled={busy} onClick={() => void submit("join", {invitation_token: token})}>Accept invitation</button>
    </> : <>
      <Link href={`/login#invite=${encodeURIComponent(token!)}`}>Sign in to accept</Link>
      <Link href={`/signup#invite=${encodeURIComponent(token!)}`}>Create an account</Link>
    </> : <p>Open the private invitation link from your workspace owner.</p> : <>
      {workspaces?.items.map(item => <p key={item.id}>
        {item.id === workspaces.current ? <strong>{item.name} · Current</strong> :
          <button type="button" disabled={busy} onClick={() => void submit("switch-workspace", {workspace_id: item.id})}>{item.name}</button>}
      </p>)}
      {!workspaces && !error ? <p role="status">Loading workspaces…</p> : null}
    </>}
    {error ? <p role="alert">{error}</p> : null}
  </section></main>;
}
