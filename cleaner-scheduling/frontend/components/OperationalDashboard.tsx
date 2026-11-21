"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, type ApiError } from "../lib/api";
import { OnboardingEmailNotice } from "./OnboardingEmailNotice";
import { EarlyCheckout } from "./EarlyCheckout";
import { DemoAutomation } from "./DemoAutomation";
import { EmptyState } from "./EmptyState";

export type TaskRow = { version?: number; confirmed_checkout_at?: string | null; can_confirm_early_checkout?: boolean; duration_minutes?: number | null; id: string; property_name: string; cleaner_name: string | null; planned_start: string | null; planned_end: string | null; acknowledged: boolean; status: string; pending: boolean; deadline_at: string | null; release_at: string; cleaned?: boolean; completed_at?: string | null; overdue?: boolean; assignment_status?: string | null; needs_assignment?: boolean; deadline_conflict?: boolean; travel_before_seconds?: number | null; travel_from?: string | null; travel_source?: string | null };
const time = (value: string | null) => value ? new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" }).format(new Date(value)) : "No deadline";
const datedTime = (value: string | null | undefined) => value ? new Intl.DateTimeFormat("en-US", {timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}).format(new Date(value)) : "Not scheduled";
export function TaskList({ tasks }: { tasks: TaskRow[] }) {
  return <div className="cleaning-table-wrap"><table className="cleaning-table"><thead><tr><th scope="col">House</th><th scope="col">Ready to clean</th><th scope="col">Cleaning deadline</th><th scope="col">Drive before cleaning</th><th scope="col">Assigned cleaner / planned time</th><th scope="col">Cleaned?</th></tr></thead>
    <tbody>{tasks.map(task => <tr key={task.id} className={task.overdue ? "cleaning-overdue" : ""}>
      <th scope="row">{task.property_name}</th><td>{datedTime(task.release_at)}</td>
      <td>{datedTime(task.deadline_at)}{task.deadline_conflict ? <strong className="deadline-alert">Planned finish misses deadline</strong> : null}{task.overdue ? <strong className="deadline-alert">Overdue: manager action needed</strong> : null}</td>
      <td>{task.travel_before_seconds != null ? <><strong>{Math.ceil(task.travel_before_seconds / 60)} min</strong><small>From {task.travel_from ?? "starting point"}</small><small>{task.travel_source === "osrm_cached" ? "Saved driving baseline" : "Synthetic travel estimate"}</small></> : "Awaiting allocation"}</td>
      <td><strong>{task.cleaner_name ?? "Unassigned"}</strong><small>{task.planned_start ? `${datedTime(task.planned_start)} to ${time(task.planned_end)}` : "Needs a feasible assignment"}</small>{task.acknowledged ? <small>Cleaner confirmed</small> : null}{task.assignment_status === "blocked" ? <strong className="deadline-alert">Assignment blocked</strong> : null}</td>
      <td><span className={`badge ${task.status === "completed" ? "" : "attention"}`}>{task.status === "completed" ? "Cleaned" : "Not yet cleaned"}</span>{task.completed_at ? <small>{datedTime(task.completed_at)}</small> : null}</td>
    </tr>)}</tbody></table></div>;
}
function Counts({ values }: { values: Record<string, number> }) { return <div>{Object.entries(values).length ? Object.entries(values).map(([label, count]) => <p key={label}><strong>{count}</strong> {label.replaceAll("_", " ")}</p>) : <p className="muted">No activity yet</p>}</div>; }
type Dashboard = {
  email_automation?: {enabled: boolean; dispatch_mode?: "after_onboarding" | "evening"; local_time: string; delivery_counts: Record<string, number>; problems: {id: string; code: string; cleaner_name?: string | null; property_name?: string | null; service_date?: string | null}[]};
  service_date: string;
  business_date: string;
  mode: string;
  tomorrow_date: string;
  today: TaskRow[];
  tomorrow: TaskRow[];
  overdue?: TaskRow[];
  unassigned_count?: number;
  availability: Record<string, number>;
  assignment_counts: Record<string, number>;
  ack_counts: { acknowledged: number; awaiting: number };
  completion_counts: { completed: number; open: number };
  exceptions: { id: string; code: string; status: string }[];
  sync_health: { status: string | null; stale: boolean; publication_blocked: boolean };
  sms_delivery_counts: Record<string, number>;
  pending_deadline_count: number;
  uncovered_count: number;
  late_count: number;
  dispatch_enabled: boolean;
};

export function OperationalDashboard({demo, canWrite, workspaceSignupEnabled = false}: {demo: boolean; canWrite: boolean; workspaceSignupEnabled?: boolean}) {
  const [filter, setFilter] = useState("open");
  const [checkoutNotice, setCheckoutNotice] = useState("");
  const [search, setSearch] = useState("");
  const [serviceDate, setServiceDate] = useState("");
  const [refreshTick, setRefreshTick] = useState(0);
  const [snapshot, setSnapshot] = useState<{ date: string; data: Dashboard | null; error: string; checkedAt: string } | null>(null);

  useEffect(() => {
    let cancelled = false; let pending = false;
    async function refresh() {
      if(pending) return; pending = true;
      try {
        const body = await api<Dashboard>(serviceDate ? `/dashboard?service_date=${encodeURIComponent(serviceDate)}` : "/dashboard");
        if(!cancelled) setSnapshot({date: serviceDate, data: body, error: "", checkedAt: new Date().toISOString()});
      } catch(reason) {
        if(!cancelled) setSnapshot(previous => ({date: serviceDate, data: previous?.date === serviceDate ? previous.data : null,
          checkedAt: previous?.date === serviceDate ? previous.checkedAt : "",
          error: "Dashboard refresh failed; displayed information may be out of date. " + ((reason as ApiError).body?.error?.message ?? "Try refreshing again.")}));
      } finally {pending = false;}
    }
    void refresh(); const timer = setInterval(() => void refresh(), 30000);
    return () => {cancelled = true; clearInterval(timer);};
  }, [serviceDate, refreshTick]);

  const loading = snapshot === null || snapshot.date !== serviceDate;
  const data = snapshot?.date === serviceDate ? snapshot.data : null;
  const error = snapshot?.date === serviceDate ? snapshot.error : "";

  const isToday = !!data && data.service_date === data.business_date;
  const rows = [...new Map([...(data?.today ?? []), ...(isToday ? data?.overdue ?? [] : [])].map(task => [task.id, task])).values()]
    .sort((left, right) => Number(left.status === "completed") - Number(right.status === "completed") || Number(!!right.overdue) - Number(!!left.overdue) || (left.deadline_at ?? "z").localeCompare(right.deadline_at ?? "z") || left.property_name.localeCompare(right.property_name) || left.id.localeCompare(right.id));
  const open = rows.filter(task => task.status !== "completed" && task.status !== "canceled");
  const unassigned = open.filter(task => task.needs_assignment ?? !task.cleaner_name);
  const cleaned = rows.filter(task => task.status === "completed");
  const query = search.trim().toLocaleLowerCase();
  const visible = rows.filter(task => (filter === "all" || (filter === "unassigned" ? (task.needs_assignment ?? !task.cleaner_name) && task.status !== "completed" : filter === "completed" ? task.status === "completed" : task.status !== "completed")) && `${task.property_name} ${task.cleaner_name ?? "unassigned"}`.toLocaleLowerCase().includes(query));
  function moveDate(offset: number) {
    if(!snapshot?.data) return;
    const fallback = snapshot.data.business_date;
    setServiceDate(previous => {
      const date = new Date(`${previous || fallback}T12:00:00Z`);
      date.setUTCDate(date.getUTCDate() + offset);
      return date.toISOString().slice(0, 10);
    });
  }
  return <main className="cleaning-dashboard">
    <header className="cleaning-dashboard-heading"><div><p className="eyebrow">DAILY OPERATIONS</p><h1>{!serviceDate || isToday ? "Today’s cleaning" : "Cleaning schedule"}</h1><p>{data ? new Intl.DateTimeFormat("en-US", {timeZone: "America/New_York", weekday: "long", month: "long", day: "numeric"}).format(new Date(`${data.service_date}T12:00:00Z`)) : "Loading your day"} · Eastern time</p></div><div className="daily-date-control"><button type="button" className="daily-date-arrow" aria-label="Previous day" disabled={!snapshot?.data} onClick={() => moveDate(-1)}>←</button><label>View date<input aria-label="Service date" type="date" value={serviceDate || data?.service_date || ""} onChange={event => setServiceDate(event.target.value)} /></label><button type="button" className="daily-date-arrow" aria-label="Next day" disabled={!snapshot?.data} onClick={() => moveDate(1)}>→</button><button type="button" onClick={() => setServiceDate("")}>Today</button></div></header>
    {workspaceSignupEnabled ? <OnboardingEmailNotice /> : null}
    {error ? <p role="alert">{error}</p> : null}
    {checkoutNotice ? <p role="status">{checkoutNotice}</p> : null}
    <div className="cleaning-summary" aria-label="Cleaning summary">{[
      ["To clean", data ? open.length : "—"], ["Assigned", data ? open.length - unassigned.length : "—"], ["Unassigned", data ? unassigned.length : "—"], ["Cleaned", data ? cleaned.length : "—"],
    ].map(([label, count]) => <div className="cleaning-summary-card" key={label}><span>{label}</span><strong>{count}</strong></div>)}</div>
    <section className="today-cleaning-board" aria-label={isToday ? "Today’s houses" : "Selected day’s houses"}>
      <div className="cleaning-board-toolbar"><div className="cleaning-board-filters" role="group" aria-label="Show cleaning work">{[["open", "To clean"], ["unassigned", "Unassigned"], ["completed", "Cleaned"], ["all", "All"]].map(([value, label]) => <button type="button" aria-pressed={filter === value} className={filter === value ? "is-selected" : ""} key={value} onClick={() => setFilter(value)}>{label}</button>)}</div><label className="cleaning-board-search"><span className="visually-hidden">Find a house or cleaner</span><input type="search" placeholder="Find a house or cleaner" value={search} onChange={event => setSearch(event.target.value)} /></label></div>
      {loading ? <p className="cleaning-board-empty">Loading cleaning work…</p> : data && visible.length ? <div className="today-cleaning-table-wrap"><table className="today-cleaning-table"><thead><tr><th scope="col">House</th><th scope="col">Assigned cleaner</th><th scope="col">Clean by</th><th scope="col">Status</th></tr></thead><tbody>{visible.map(task => {
        const done = task.status === "completed";
        const status = done ? "Cleaned" : task.assignment_status === "blocked" ? "Blocked" : task.deadline_conflict ? "Deadline conflict" : task.overdue ? "Overdue" : !task.cleaner_name ? "Unassigned" : task.acknowledged ? "Confirmed" : "Assigned";
        return <tr key={task.id} className={task.overdue ? "is-overdue" : ""}>
          <th scope="row"><strong>{task.property_name}</strong><small>Ready after {datedTime(task.release_at)}{task.duration_minutes != null ? ` · ${task.duration_minutes} min` : ""}</small>{task.confirmed_checkout_at ? <small>Guest departure confirmed</small> : task.can_confirm_early_checkout && task.version ? <EarlyCheckout taskId={task.id} version={task.version} onConfirmed={message => {setCheckoutNotice(message); setRefreshTick(value => value + 1);}} /> : null}</th>
          <td><div className={`cleaner-assignment${!task.cleaner_name ? " is-unassigned" : ""}`}><span className="cleaner-initials" aria-hidden="true">{task.cleaner_name ? task.cleaner_name.split(/\s+/).filter(Boolean).map(part => part[0]).slice(0, 2).join("") : "—"}</span><div><strong>{task.cleaner_name ?? "Unassigned"}</strong><small>{task.planned_start ? `${datedTime(task.planned_start)} – ${time(task.planned_end)}` : "Not scheduled"}</small>{task.acknowledged && !done && status !== "Confirmed" ? <small>Cleaner confirmed</small> : null}</div></div></td>
          <td><span className={task.overdue ? "cleaning-deadline-overdue" : ""}>{task.deadline_at ? datedTime(task.deadline_at) : "Needs deadline"}</span>{task.overdue && status !== "Overdue" ? <small className="cleaning-deadline-overdue">Overdue</small> : null}{task.deadline_conflict && status !== "Deadline conflict" ? <small className="cleaning-deadline-overdue">Planned finish misses deadline</small> : null}</td>
          <td><span className={`cleaning-task-status${done ? " is-cleaned" : task.overdue || task.deadline_conflict || task.assignment_status === "blocked" ? " needs-review" : !task.cleaner_name ? " is-unassigned" : ""}${status === "Overdue" ? " is-overdue" : ""}`}>{status}</span>{task.completed_at ? <small>{datedTime(task.completed_at)}</small> : null}</td>
        </tr>;
      })}</tbody></table></div> : data ? <EmptyState variant="dashboard" title={rows.length && filter === "open" && !query && !open.length ? "All cleaning is done" : rows.length ? "No matching cleans" : "No cleaning work on this date"} description={rows.length && filter === "open" && !query && !open.length ? "Open Cleaned to review finished work." : rows.length ? "Try another filter or search." : "Imported cleaning work will appear here."} /> : null}
      <div className="cleaning-board-footer"><span>{data ? `${visible.length} cleans${isToday && data.overdue?.length ? " · includes overdue work" : ""}` : ""}</span>{data?.mode === "hospitable_read_only" ? <Link href="/cleaners">Manage cleaners →</Link> : canWrite ? <Link href="/schedules">Review assignments →</Link> : <Link href="/setup">Setup →</Link>}</div>
    </section>
    <div className="cleaning-dashboard-status"><span>{snapshot?.checkedAt ? `Updated ${time(snapshot.checkedAt)} ET · Auto-refresh 30s` : "Waiting for an update"} <button type="button" onClick={() => setRefreshTick(value => value + 1)}>Refresh</button></span><div>{data?.mode === "hospitable_read_only" && !data.email_automation?.enabled ? <span>Automation paused</span> : null}{data?.sync_health.publication_blocked ? <Link href="/exceptions">Imports need review</Link> : null}</div></div>
    {data ? <details className="cleaning-operations-details"><summary>Operations details <span className="badge">{data.exceptions.length} exceptions</span></summary><div className="cleaning-details-grid"><div><h2>Availability · today &amp; tomorrow</h2><Counts values={data.availability} /></div><div><h2>Assignments</h2><Counts values={data.assignment_counts} /><p>{data.ack_counts.acknowledged} confirmed · {data.ack_counts.awaiting} awaiting</p></div><div><h2>Source &amp; messages</h2><p>{data.sync_health.stale ? "Source is stale" : "Latest source read is fresh"}</p><Counts values={data.mode === "hospitable_read_only" ? data.email_automation?.delivery_counts ?? {} : data.sms_delivery_counts} /></div></div><p><Link href="/exceptions">Review exceptions</Link>{data.mode !== "hospitable_read_only" ? <> · <Link href="/tomorrow">Tomorrow’s schedule</Link></> : null}</p><DemoAutomation demo={demo} canWrite={canWrite} /></details> : null}
  </main>;
}
