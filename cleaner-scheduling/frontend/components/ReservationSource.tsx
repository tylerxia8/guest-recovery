"use client";

import { useEffect, useState, type FormEvent } from "react";
import { api, type ApiError } from "../lib/api";
import { DemoAutomation } from "./DemoAutomation";

type Stay = {id: string; property_name: string; checkin_at: string; checkout_at: string};
type Cleaning = {id: string; property_name: string; release_at: string; deadline_at: string | null; cleaner_name: string | null; status: string; cleaned: boolean; overdue: boolean};
type Operations = {
  service_date: string; business_date: string; min_date: string; max_date: string; occupancy_as_of: string; mode: string;
  houses: {id: string; name: string}[]; occupied: Stay[]; check_ins: Stay[]; check_outs: Stay[]; turnovers: Stay[]; tasks: Cleaning[];
  sync_health: {status: string | null; stale: boolean; source_as_of: string | null};
};
const categories = [
  ["occupied", "Currently occupied"], ["check_ins", "Check-ins"], ["check_outs", "Check-outs"],
  ["turnovers", "Same-day turnovers"], ["tasks", "Local cleaning tasks"],
] as const;
type Category = typeof categories[number][0];
const stamp = (value: string | null) => value ? new Intl.DateTimeFormat("en-US", {timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}).format(new Date(value)) : "Not set";

export function ReservationSource({demo, canWrite}: {demo: boolean; canWrite: boolean}) {
  const [day, setDay] = useState("");
  const [category, setCategory] = useState<Category>("check_outs");
  const [refreshTick, setRefreshTick] = useState(0);
  const [snapshot, setSnapshot] = useState<{key: string; data: Operations | null; error: string} | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [actionError, setActionError] = useState("");
  useEffect(() => {
    let active = true; let pending = false;
    async function refresh() {
      if(pending) return;
      pending = true;
      try {
        const data = await api<Operations>(`/daily-operations${day ? `?service_date=${encodeURIComponent(day)}` : ""}`);
        if(active) setSnapshot({key: day, data, error: ""});
      } catch {
        if(active) setSnapshot(previous => ({key: day, data: previous?.data ?? null, error: "Daily operations could not be refreshed. Connect above if needed; any displayed records are from the last successful refresh."}));
      } finally {pending = false;}
    }
    void refresh();
    const timer = setInterval(() => void refresh(), 30000);
    return () => {active = false; clearInterval(timer);};
  }, [day, refreshTick]);
  const selectedDate = day || snapshot?.data?.business_date || "";
  const data = snapshot?.key === day && snapshot.data?.service_date === selectedDate ? snapshot.data : null;
  function moveDay(offset: number) {
    const bounds = snapshot?.data;
    if(!bounds) return;
    setDay(previous => {
      const date = new Date(`${previous || bounds.business_date}T00:00:00Z`);
      date.setUTCDate(date.getUTCDate() + offset);
      const next = date.toISOString().slice(0, 10);
      return next >= bounds.min_date && next <= bounds.max_date ? next : previous;
    });
  }
  const error = snapshot?.key === day ? snapshot.error : "";
  const readOnly = data?.mode === "hospitable_read_only";
  const title = category === "occupied" && data?.service_date !== data?.business_date
    ? "Occupied at start of day" : categories.find(([key]) => key === category)![1];
  async function sync() {
    setBusy(true); setNotice(""); setActionError("");
    try {
      const result = await api<{status: string}>("/reservation-syncs", {method: "POST", headers: {"Idempotency-Key": crypto.randomUUID()}, body: JSON.stringify({provider: "hospitable", mode: "full"})});
      setRefreshTick(value => value + 1);
      setNotice(result.status === "succeeded" ? "Bookings updated from Hospitable." : result.status === "partial" ? "Bookings updated. Some source records need review." : "Import failed. Previously imported bookings are retained.");
    } catch {setActionError("Import could not finish. Previously imported bookings are retained.");}
    finally {setBusy(false);}
  }
  function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const values = new FormData(form);
    setBusy(true); setNotice(""); setActionError("");
    void api("/demo/turnovers", {method: "POST", headers: {"Idempotency-Key": crypto.randomUUID()}, body: JSON.stringify({property_id: values.get("property"), checkout_date: values.get("date"), checkout_time: values.get("checkout"), next_checkin_time: values.get("checkin")})})
      .then(() => {setRefreshTick(value => value + 1); setNotice("Synthetic booking added. No external booking was created."); form.reset();})
      .catch(reason => setActionError((reason as ApiError).message)).finally(() => setBusy(false));
  }
  return <main className="daily-operations">
    <header className="daily-operations-heading"><div><p className="eyebrow">HOSPITABLE</p><h1>Daily operations</h1><p className="muted">{demo ? "Simulated bookings" : readOnly ? "Imported bookings · Read-only connection" : "Reservation activity"} · Eastern time</p></div>
      <div className="daily-date-control" role="group" aria-label="Daily operations date">
        <button type="button" className="daily-date-arrow" aria-label="Previous day" title="Previous day" disabled={!snapshot?.data || selectedDate <= snapshot.data.min_date} onClick={() => moveDay(-1)}>←</button>
        <label>View date<input type="date" value={selectedDate} min={snapshot?.data?.min_date} max={snapshot?.data?.max_date} disabled={!snapshot?.data} onChange={event => setDay(event.target.value)} /></label>
        <button type="button" className="daily-date-arrow" aria-label="Next day" title="Next day" disabled={!snapshot?.data || selectedDate >= snapshot.data.max_date} onClick={() => moveDay(1)}>→</button>
        <button type="button" onClick={() => setDay("")}>Today</button>
      </div>
    </header>
    {error ? <p role="alert">{error} <button onClick={() => setRefreshTick(value => value + 1)}>Try again</button></p> : null}
    <div className="daily-operation-categories" role="group" aria-label="Daily operations categories">
      {categories.map(([key, label]) => <button key={key} type="button" className={`daily-operation-category${category === key ? " is-selected" : ""}`} aria-pressed={category === key} disabled={!data} onClick={() => setCategory(key)}>
        <span>{key === "occupied" && data?.service_date !== data?.business_date ? "Occupied at start of day" : label}</span><strong>{data ? data[key].length : "—"}</strong>
      </button>)}
    </div>
    {!data && !error ? <p className="daily-loading">Loading daily operations…</p> : null}
    {data ? <>
      <section className="daily-operation-list" aria-label={title}><div className="daily-operation-list-heading"><h2>{title}</h2><span className="badge">{data[category].length} {category === "tasks" ? "tasks" : category === "turnovers" ? "turnovers" : "stays"}</span></div>
        {category === "occupied" ? <p className="muted">Booked occupancy as of {stamp(data.occupancy_as_of)} Eastern.</p> : null}
        {category === "tasks" ? <p className="muted">Cleaning records maintained in this app, including carryover work and current status.</p> : null}
        {!data[category].length ? <div className="daily-operation-empty"><h3>{category === "tasks" ? "No local cleaning tasks" : category === "turnovers" ? "No imported same-day turnovers" : "No imported stays"} for this view</h3><p>Choose another category or date. This view uses imported bookings and local cleaning records.</p></div> : category === "tasks" ?
          <div className="daily-operation-table"><table><thead><tr><th scope="col">House</th><th scope="col">Ready to clean</th><th scope="col">Deadline</th><th scope="col">Cleaner</th><th scope="col">Status</th></tr></thead><tbody>{data.tasks.map(task => <tr key={task.id}><th scope="row">{task.property_name}</th><td>{stamp(task.release_at)}</td><td>{stamp(task.deadline_at)}</td><td>{task.cleaner_name ?? "Unassigned"}</td><td><span className={`badge${!task.cleaned && task.overdue ? " is-overdue" : !task.cleaned && task.status === "ready" ? " is-ready" : ""}`}>{task.cleaned ? "Cleaned" : task.overdue ? "Overdue" : task.status.replaceAll("_", " ")}</span></td></tr>)}</tbody></table></div>
          : <div className="daily-operation-table"><table><thead><tr><th scope="col">House</th><th scope="col">{category === "turnovers" ? "Check-out" : "Check-in"}</th><th scope="col">{category === "turnovers" ? "Next check-in" : "Check-out"}</th></tr></thead><tbody>{data[category].map(stay => <tr key={stay.id}><th scope="row">{stay.property_name}</th><td>{stamp(category === "turnovers" ? stay.checkout_at : stay.checkin_at)}</td><td>{stamp(category === "turnovers" ? stay.checkin_at : stay.checkout_at)}</td></tr>)}</tbody></table></div>}
      </section>
      <div className="daily-source-status"><span>Imported records only · Last source read {data.sync_health.source_as_of ? stamp(data.sync_health.source_as_of) : "not yet"}</span>{data.sync_health.stale || data.sync_health.status !== "succeeded" ? <a href="/exceptions">Import needs review</a> : <span>Latest import is fresh</span>}</div>
      <details className="daily-source-tools"><summary>Connection details &amp; source tools</summary><p>{data.houses.length} active houses. This view uses imported bookings; quarantined records and stays outside the import window may be missing.</p><p><a href="/properties">Cleaning times</a> · <a href="/exceptions">Import exceptions</a> · <a href="/setup">Setup</a></p>
        {readOnly ? <><p>Hospitable access is read-only. Automatic imports run while the local API is running; no account changes are sent.</p>{canWrite ? <button disabled={busy} onClick={() => void sync()}>{busy ? "Updating…" : "Sync from Hospitable"}</button> : null}</> : <p>{demo ? "Fictional reservations in the isolated demo." : "Mock reservation source; live access is not configured."}</p>}
        {notice ? <p role="status">{notice}</p> : null}{actionError ? <p role="alert">{actionError}</p> : null}
        <details><summary>Connected houses ({data.houses.length})</summary><ul className="daily-source-houses">{data.houses.map(house => <li key={house.id}>{house.name}</li>)}</ul></details>
        {demo ? <DemoAutomation demo={demo} canWrite={canWrite} /> : null}
        {demo && canWrite ? <details><summary>Demo tools: simulate a booking</summary><form className="toolbar" method="post" onSubmit={create}><label>Reservation house<select name="property" required><option value="">Choose a house</option>{data.houses.map(house => <option key={house.id} value={house.id}>{house.name}</option>)}</select></label><label>Checkout date<input name="date" type="date" min={data.business_date} required /></label><label>Checkout time (Eastern)<input name="checkout" type="time" required /></label><label>Next arrival time (Eastern)<input name="checkin" type="time" required /></label><button disabled={busy}>Add synthetic bookings</button></form></details> : null}
      </details>
    </> : null}
  </main>;
}
