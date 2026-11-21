"use client";
import { useEffect, useState } from "react";
import { api } from "../lib/api";

type Account = { account_name: string; enabled: boolean; busy: boolean; last_run_at: string | null; last_error: string | null; last_outcome: string; scope: string; routing?: {mode: string; captured_at: string; property_count: number; source_url: string; staging_name: string; staging_source_url: string}; last_evening_dispatch?: {recorded: number; service_date: string} | null };
export function DemoAutomation({ demo, canWrite }: {demo: boolean; canWrite: boolean}) {
  const [account, setAccount] = useState<Account | null>(null); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  useEffect(() => {if(!demo) return; let active = true; let pending = false;
    async function refresh() {if(pending) return; pending = true; try {const row = await api<Account>("/demo/account"); if(active) {setAccount(row); setError("");}} catch {if(active) setError("Automation status could not be checked. Do not assume it is running.");} finally {pending = false;}}
    void refresh(); const timer = setInterval(() => void refresh(), 5000); return () => {active = false; clearInterval(timer);};
  }, [demo]);
  async function toggle() {setBusy(true); setError(""); try {await api("/demo/automation", {method: "POST", body: JSON.stringify({enabled: !account?.enabled})}); setAccount(await api<Account>("/demo/account"));} catch {setError("Automation could not be changed. Refresh its status before trying again.");} finally {setBusy(false);}}
  async function evening() {setBusy(true); setError(""); try {await api("/demo/evening", {method: "POST"}); setAccount(await api<Account>("/demo/account"));} catch {setError("Evening simulation could not finish. Refresh status and review exceptions before retrying.");} finally {setBusy(false);}}
  return <section aria-label="Automation status"><h2>{demo ? "Demo automation" : "Automation readiness"}</h2>
    {demo ? <><p><strong>{account?.account_name ?? "Simulated Hospitable account"}</strong> / simulated connection</p>
      <p>Checks fictional reservations and prepares next-day plans every 30 seconds. Evening schedules are recorded at or after 8 PM Eastern, with each house, cleaning time and deadline.</p>
      <p><span className={`badge ${account?.enabled ? "" : "attention"}`}>{account ? account.enabled ? "Running" : "Paused" : "Checking status"}</span> {account?.busy ? "Cycle in progress" : account?.last_outcome}</p>
      {account?.last_run_at ? <p>Last completed cycle: {new Intl.DateTimeFormat("en-US", {timeZone: "America/New_York", hour: "numeric", minute: "2-digit", second: "2-digit"}).format(new Date(account.last_run_at))} ET</p> : null}
      <p className="muted">Runs on the local API while it is open; pauses on API restart or cycle failure. The business clock stays fixed for this scenario. No external account, carrier messages, or money transfers.</p>
      {canWrite ? <button disabled={busy || !account} onClick={() => void toggle()}>{account?.enabled ? "Pause demo automation" : "Start demo automation"}</button> : null}
      {canWrite ? <button disabled={busy || !account || account.busy} onClick={() => void evening()}>Simulate 8 PM schedule delivery</button> : null}
      {account?.last_evening_dispatch ? <p role="status">Evening schedule for {account.last_evening_dispatch.service_date}: {account.last_evening_dispatch.recorded} new mock messages recorded this run. No external send.</p> : null}
      {account?.routing?.mode === "saved_driving_baseline" ? <aside aria-label="Driving baseline"><h3>Saved driving baseline</h3><p>{account.routing.property_count} Charlotte houses. Captured {account.routing.captured_at.slice(0, 10)}; reused offline for allocations, travel gaps and duty limits.</p><p>Assumed public demo starting point: <a href={account.routing.staging_source_url} target="_blank" rel="noreferrer">{account.routing.staging_name}</a>. This is not a cleaner home or an affiliation with the venue.</p><p className="muted">Static OSRM driving estimates from road-snapped parcel centers, without live traffic or parking/access time. Times are rounded up to whole minutes in the schedule. Missing routes block affected allocations.</p><p><a href="https://project-osrm.org/" target="_blank" rel="noreferrer">OSRM</a> / Map data &copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap contributors</a></p></aside> : null}
      {account?.last_error ? <p role="alert">{account.last_error}</p> : null}
    </> : <p>Live automation is not available yet. Complete the verified connection, routing, consent, security, recovery, and pilot checks first.</p>}
    {error ? <p role="alert">{error}</p> : null}<p><a href="/reservations">Review the reservation source</a> / <a href="/exceptions">Review exceptions</a></p>
  </section>;
}
