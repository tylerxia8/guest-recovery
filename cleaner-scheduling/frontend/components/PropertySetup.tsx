"use client";
import type { WorkspaceMode } from "../lib/session";
import { useEffect, useState, type FormEvent } from "react";
import { api, type ApiError } from "../lib/api";
import { properties as loadProperties } from "../lib/properties";
import { EmptyState } from "./EmptyState";

type HousingContext = { source_url: string; retrieved_at: string; geography: string; items: {property_id: string; address: string; property_type: string; assessment_year: number; assessed_value: number}[] };

type House = { id: string; name: string; external_id: string; active: boolean; version: number; bedrooms: number; duration_override_minutes: number | null; cleaning_duration_minutes: number; default_cleaning_duration_minutes: number };
export function PropertySetup({ canWrite, demo = false, sourceMode = "mock" }: { canWrite: boolean; demo?: boolean; sourceMode?: WorkspaceMode }) {
  const liveReadOnly = sourceMode === "hospitable_read_only";
  const [housing, setHousing] = useState<HousingContext | null>(null);
  useEffect(() => { if (canWrite) void api<HousingContext | null>("/demo/housing").then(setHousing).catch(() => {}); }, [canWrite]);
  const [items, setItems] = useState<House[]>([]);
  const [error, setError] = useState(""); const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false); const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState("");
  async function refresh() { setItems((await loadProperties<House>()).items); }
  useEffect(() => { let active = true; loadProperties<House>().then(body => {if (active) setItems(body.items);}).catch(() => {if (active) setError("Properties could not be loaded.");}).finally(() => {if (active) setLoading(false);}); return () => {active = false;}; }, []);
  async function save(event: FormEvent<HTMLFormElement>, edit: boolean) {
    event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
    setBusy(true); setError(""); setNotice("");
    try {
      const row = items.find(item => item.id === selected);
      const duration = String(data.get("duration")).trim(); const bedrooms = Number(data.get("bedrooms"));
      if (edit && !row) return;
      await api(edit ? `/properties/${row!.id}` : "/properties", {method: edit ? "PATCH" : "POST",
        headers: edit ? {"If-Match": `"${row!.version}"`, "Idempotency-Key": crypto.randomUUID()} : {"Idempotency-Key": crypto.randomUUID()},
        body: JSON.stringify(edit ? (duration ? {duration_override_minutes: Number(duration)} : {clear_duration_override: true}) : {
          name: String(data.get("name")).trim(), external_id: String(data.get("external")).trim(),
          provider: "mock", bedrooms, duration_override_minutes: duration ? Number(duration) : null,
          location: {latitude: 35.2, longitude: -80.8}, active: true,
        })});
      await refresh(); setNotice(edit ? "House updated. Affected cleaning work has been recalculated." : "House added to the simulated account. Choose which cleaners may work there.");
      if (!edit) form.reset();
    } catch (reason) { const failure = reason as ApiError; setError(failure.status === 409 ? "This house changed. Reload the page before editing again." : failure.message); }
    finally {setBusy(false);}
  }
  const house = items.find(item => item.id === selected);
  return <main><p className="eyebrow">HOUSES FROM HOSPITABLE</p><h1>Properties</h1>
    <p>Default: 1 hour + 30 minutes per bedroom. Override only when needed. <a href="/setup">Return to setup</a>.</p>
    <p className="muted">{liveReadOnly ? <>House names and bedroom counts come from Hospitable. Cleaning-time changes are saved only in this app. Street addresses and access instructions are excluded, and scheduling remains disabled.</> : housing ? <>Public-record addresses from southeast Charlotte are used as demo examples. These homes are not verified rentals or managed properties. Bedrooms, cleaning times, coordinates, reservations and access details are demo assumptions. <a href={housing.source_url} target="_blank" rel="noreferrer">City housing data</a> retrieved {housing.retrieved_at.slice(0, 10)}. Assessed values are historical tax assessments, not current sale prices.</> : <>This workspace uses fictional properties and locations. Real addresses and access instructions will require the verified production connection.</>}</p>
    {error ? <p role="alert">{error}</p> : null}{notice ? <p role="status">{notice}</p> : null}
    {demo && canWrite ? <details><summary>Demo tools: add a fictional house</summary><section aria-label="Add a house"><h2>Add a house</h2><form method="post" onSubmit={event => void save(event, false)}>
      <label>House name<input name="name" required maxLength={120} placeholder="Example: Willow Cottage (synthetic)" /></label>
      <label>Reservation property reference<input name="external" required maxLength={120} placeholder="synthetic-house-101" /></label>
      <label>Bedrooms<input name="bedrooms" type="number" min="0" max="100" step="1" required /></label>
      <label>Cleaning duration (minutes, optional)<input name="duration" type="number" min="1" max="1440" step="1" placeholder="Based on bedrooms" /></label>
      <button disabled={busy || loading}>Add house</button>
    </form><p className="muted">The reference links reservations to the right house in the simulated account. Demo coordinates are fictional.</p></section></details> : null}
    {canWrite ? <section aria-label="Edit cleaning duration"><h2>Optional house override</h2>
      <label>House to edit<select value={selected} onChange={event => setSelected(event.target.value)}><option value="">Choose a house</option>{items.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      {house ? <form method="post" key={`${house.id}:${house.version}`} onSubmit={event => void save(event, true)}>
        <p>{house.bedrooms} bedrooms from the current source record. Update house information in Hospitable.</p>
        <label>Cleaning time (minutes)<input name="duration" type="number" min="1" max="1440" step="1" placeholder={`${house.default_cleaning_duration_minutes} (default)`} defaultValue={house.duration_override_minutes ?? ""} /></label><p className="muted">Leave blank to use the bedroom default.</p>
        <button disabled={busy || loading}>Save cleaning time</button></form> : null}</section> : null}
    <section><h2>Your houses</h2>{loading ? <p>Loading houses</p> : null}{!loading && !items.length ? <EmptyState variant="houses" title="No houses" /> : null}
      <ul>{items.map(item => <li key={item.id}><strong>{item.name}</strong> / {item.active ? "active" : "inactive"} / {item.duration_override_minutes ? `${item.duration_override_minutes} min` : `${item.cleaning_duration_minutes} min (default)`} / {item.bedrooms} bedrooms{housing?.items.filter(fact => fact.property_id === item.id).map(fact => <p key={fact.property_id} className="muted">Public record: {fact.property_type}; {fact.assessment_year} assessed value {new Intl.NumberFormat("en-US", {style: "currency", currency: "USD", maximumFractionDigits: 0}).format(fact.assessed_value)}. Bedroom count and cleaning duration above are demo assumptions.</p>)}</li>)}</ul>

    </section></main>;
}
