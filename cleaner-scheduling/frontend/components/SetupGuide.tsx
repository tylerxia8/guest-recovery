"use client";
import type { WorkspaceMode } from "../lib/session";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { DemoAutomation } from "./DemoAutomation";

type Setup = {cleaning_minutes_by_bedrooms?: Record<string, number>; default_cleaning_minutes?: number; local_routing?: {configured: boolean; houses_ready: number; cleaners_ready: number}; tomorrow_date?: string; tomorrow_cleaners_without_workday?: number; business_date: string; properties: number; properties_needing_cleaning_time: number; cleaners: number; cleaners_without_eligibility: number; cleaners_without_workday: number; cleaners_without_consent: number; policy_approved: boolean; source_fresh: boolean; blockers: string[]};
export function SetupGuide({demo, canWrite, sourceMode = "mock"}: {demo: boolean; canWrite: boolean; sourceMode?: WorkspaceMode}) {
  const liveReadOnly = sourceMode === "hospitable_read_only";
 const [data, setData] = useState<Setup | null>(null); const [error, setError] = useState("");
 useEffect(() => {let active = true; api<Setup>("/setup").then(row => {if(active) setData(row);}).catch(() => {if(active) setError("Setup status could not be loaded. Sign in and try again.");}); return () => {active = false;};}, []);
 const cleanersReady = !!data && data.cleaners > 0 && data.cleaners_without_eligibility === 0 && (data.tomorrow_cleaners_without_workday ?? data.cleaners_without_workday) === 0;
 const cleaningReady = !!data && data.properties > 0 && data.properties_needing_cleaning_time === 0;
 const routingReady = !!data && data.properties > 0 && data.cleaners > 0 && !!data.local_routing?.configured && data.local_routing.houses_ready >= data.properties && data.local_routing.cleaners_ready >= data.cleaners;
 if (liveReadOnly) return <main className="operation-setup">
  <header className="setup-heading"><div><h1>Setup</h1></div></header>
  {error ? <p role="alert">{error}</p> : null}{!data && !error ? <p role="status">Loading your setup…</p> : null}
  <div className="setup-steps">
   <section className="setup-step-card"><div className="setup-card-top"><span className="setup-number" aria-label="Step 1">01</span><span className={`setup-state${cleanersReady ? " is-ready" : ""}`}>{!data ? "Loading" : cleanersReady ? "Hours ready" : "Needs attention"}</span></div><h2>Cleaners</h2>{data && !cleanersReady && data.cleaners > 0 ? <p className="setup-card-detail">{[(data.tomorrow_cleaners_without_workday ?? data.cleaners_without_workday) > 0 ? `${data.tomorrow_cleaners_without_workday ?? data.cleaners_without_workday} need hours` : "", data.cleaners_without_eligibility > 0 ? `${data.cleaners_without_eligibility} need house access` : ""].filter(Boolean).join(" · ")}</p> : null}<a className="setup-card-action" href="/cleaners">Manage cleaners <span aria-hidden="true">→</span></a></section>
   <section className="setup-step-card"><div className="setup-card-top"><span className="setup-number" aria-label="Step 2">02</span><span className={`setup-state${cleaningReady ? " is-ready" : ""}`}>{!data ? "Loading" : cleaningReady ? "Times ready" : "Needs attention"}</span></div><h2>Cleaning times</h2><a className="setup-card-action" href="/properties">Edit exceptions <span aria-hidden="true">→</span></a></section>
   <section className="setup-step-card"><div className="setup-card-top"><span className="setup-number" aria-label="Step 3">03</span><span className={`setup-state${routingReady ? " is-ready" : ""}`}>{!data ? "Loading" : routingReady ? "Routes ready" : "Needs attention"}</span></div><h2>Travel</h2>{data && !routingReady ? <p className="setup-card-detail">{data.local_routing?.cleaners_ready ?? 0}/{data.cleaners} starting locations ready</p> : null}<a className="setup-card-action" href="/locations">Review locations <span aria-hidden="true">→</span></a></section>
  </div>
  <section className="setup-source-strip"><div><span className="setup-source-mark" aria-hidden="true">H</span><div><h2>Hospitable</h2><p>{data ? `${data.properties} houses` : "Loading"}{data ? ` · ${data.source_fresh ? "Up to date" : "Review imports"}` : ""}</p></div></div><a href="/reservations">Open <span aria-hidden="true">↗</span></a></section>
  <p className="setup-attribution">OSRM · Map data © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a> · No live traffic</p>
 </main>;
 return <main><p className="eyebrow">SET UP YOUR OPERATION</p><h1>Set up your operation</h1><p>Use Hospitable for houses and bookings, set up your cleaners, then supervise automatic scheduling.</p>
  {error ? <p role="alert">{error}</p> : null}{!data && !error ? <p>Loading setup checklist</p> : null}
  <div className="card-grid tour">
   <section><span className="step">01 / HOSPITABLE</span><h2>One source for houses and bookings</h2><p>{demo ? "Your simulated account already supplies houses and reservations. No duplicate entry is needed." : "Live Hospitable access is not configured. The current houses and bookings are mock data."}</p><p>{data ? `${data.properties} active houses. ${data.properties_needing_cleaning_time} need a cleaning time before they can be scheduled.` : "Defaults follow the bedroom count unless overridden."}</p>{data && !data.source_fresh ? <p role="alert">Reservation data needs attention before schedules can be published.</p> : null}<a href="/reservations">Review Hospitable</a></section>
   <section><span className="step">02 / CLEANERS</span><h2>Confirm your team</h2><p>{data ? `${data.cleaners} active cleaners. ${data.cleaners_without_eligibility} need eligible houses; ${data.tomorrow_cleaners_without_workday ?? data.cleaners_without_workday} need hours for ${data.tomorrow_date ?? data.business_date}; ${data.cleaners_without_consent} need consent.` : "Set eligible houses, working hours, capacity, starting points and consent."}</p><a href="/cleaners">Set up cleaners</a></section>
   <section><span className="step">03 / RUN</span><h2>Start once, supervise exceptions</h2><p>The system prepares assignments using deadlines, availability and driving time, then records evening schedules in the demo. Review the dashboard when work needs attention.</p>{data && !data.policy_approved ? <p role="alert">An owner must approve scheduling policies before publication.</p> : null}<a href="/tomorrow">View tomorrow?s schedule</a> / <a href="/dashboard">Open operational dashboard</a></section>
  </div>
  <DemoAutomation demo={demo} canWrite={canWrite} />
  <section><h2>Supervise by exception</h2><p>The dashboard highlights uncovered work, missing acceptance, late completion, and source problems. Cleaner acceptance and completion require explicit replies. Missing acceptance alone never authorizes reassignment.</p></section>
  <details><summary>Live operation requirements</summary><section><h2>Before real unattended operation</h2><p>The local demonstration is available now. Live operation still requires:</p><ul>{(data?.blockers ?? []).map(item => <li key={item}>{item}</li>)}</ul><p className="muted">Complete scheduling acceptance and production integrations remain in progress.</p></section></details>
 </main>;
}
