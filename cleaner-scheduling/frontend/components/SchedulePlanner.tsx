"use client";
import { useEffect, useState, useSyncExternalStore } from "react";
import { LocalSchedulingPolicy } from "./LocalSchedulingPolicy";
import { api, type ApiError } from "../lib/api";
import type { WorkspaceMode } from "../lib/session";

type Run = { id: string; version: number; service_date: string; status: string; input_fingerprint: string; day_revision: number; solver_status: string; uncovered: { task_id: string; reason: string }[]; candidate_plan: { placed: { task_id: string; cleaner_id: string; start: string; end: string; travel_before_seconds: number }[] } };
type Schedule = { active_run: Run | null };
type ReplyRow = { id: string; task_id: string; cleaner: string; status: string; service_date: string };
type AssignmentRow = { id: string; property_id: string; assignment_id: string | null; assignment_version: number | null; assignment_status: string | null; property_name: string; cleaner_name: string | null; cleaner_id: string | null; is_locked: boolean; lock_reason: string | null; locked_by: string | null; planned_start: string | null; planned_end: string | null; status: string };
type Dashboard = { today: AssignmentRow[]; tomorrow: AssignmentRow[] };
type CleanerOption = { id: string; display_name: string; eligible_property_ids?: string[] };

const time = (value: string) => new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" }).format(new Date(value));

function toEasternDate(isoTimestamp: string): string {
  const date = new Date(isoTimestamp);
  return date.toLocaleDateString("en-CA", { timeZone: "America/New_York" });
}

function toEasternLocalInput(isoTimestamp: string): string {
  const date = new Date(isoTimestamp);
  const eastern = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(date);
  const parts: Record<string, string> = {};
  for (const p of eastern) parts[p.type] = p.value;
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

function fromLocalInputToEasternISO(localInput: string): string {
  const [datePart, timePart] = localInput.split("T");
  const [year, month, day] = datePart.split("-").map(Number);
  const [hour, minute] = timePart.split(":").map(Number);
  const probe = new Date(Date.UTC(year, month - 1, day, hour + 5, minute));
  const eastern = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
    timeZoneName: "shortOffset",
  }).formatToParts(probe);
  const parts: Record<string, string> = {};
  for (const p of eastern) parts[p.type] = p.value;
  const offsetMatch = parts.timeZoneName?.match(/GMT([+-]\d+)/);
  const offsetHours = offsetMatch ? parseInt(offsetMatch[1], 10) : -5;
  const offsetStr = offsetHours <= 0 ? `-${String(-offsetHours).padStart(2, "0")}:00` : `+${String(offsetHours).padStart(2, "0")}:00`;
  return `${datePart}T${timePart}:00${offsetStr}`;
}

const subscribeToReadiness = () => () => {};
const clientReady = () => true;
const serverReady = () => false;

export function SchedulePlanner({ demo, canWrite, canApprove = false, sourceMode = "mock" }: { demo: boolean; canWrite: boolean; canApprove?: boolean; sourceMode?: WorkspaceMode }) {
  const local = sourceMode === "hospitable_read_only";
  const hydrated = useSyncExternalStore(subscribeToReadiness, clientReady, serverReady);
  const [day, setDay] = useState(local ? "" : "2026-11-10");
  const [today, setToday] = useState(local ? "" : "2026-11-10");
  const [policyApproved, setPolicyApproved] = useState<boolean | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [names, setNames] = useState<Record<string, string>>({});
  const [tasks, setTasks] = useState<Record<string, string>>({});
  const [replies, setReplies] = useState<ReplyRow[]>([]);
  const [assignments, setAssignments] = useState<AssignmentRow[]>([]);
  const [cleaners, setCleaners] = useState<CleanerOption[]>([]);
  const [reassignTarget, setReassignTarget] = useState<{id: string; version: number; cleanerId: string; start: string; propertyId: string} | null>(null);

  useEffect(() => {
    if (!local) return;
    let active = true;
    async function loadSetup() {
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          const setup = await api<{business_date: string; tomorrow_date: string; policy_approved: boolean}>("/setup", {signal: AbortSignal.timeout(20000)});
          if (active) {setDay(setup.tomorrow_date); setToday(setup.business_date); setPolicyApproved(setup.policy_approved);}
          return;
        } catch (reason) {
          const failure = reason as ApiError;
          if (failure.status && failure.status < 500 && failure.status !== 429) {
            if (active) setError(failure.message);
            return;
          }
          if (attempt === 2 && active) setError("Schedule setup could not be loaded. Refresh to try again.");
        }
        await new Promise(resolve => setTimeout(resolve, 500 * 2 ** attempt));
        if (!active) return;
      }
    }
    void loadSetup();
    return () => {active = false;};
  }, [local]);
  function chooseDay(value: string) {setDay(value); setRun(null); setLoaded(false); setReplies([]); setAssignments([]); setReassignTarget(null); setError(""); setNotice("");}
  function moveDay(offset: number) {if (day) chooseDay(new Date(Date.parse(`${day}T00:00:00Z`) + offset * 86400000).toISOString().slice(0, 10));}
  async function refreshReplies() { if (demo && canWrite) setReplies((await api<{items: ReplyRow[]}>("/demo/assignments")).items); }

  async function act(work: () => Promise<void>, surfaceDetails = false) {
    setBusy(true); setError(""); setNotice("");
    try {
      await work();
    } catch (reason) {
      const failure = reason as ApiError;
      const detail = failure.body?.error;
      setError(surfaceDetails && detail?.code ? `${detail.code}: ${detail.message ?? failure.message}` : failure.message);
    } finally {
      setBusy(false);
    }
  }

  async function dictionaries() {
    const [cleanerList, snapshot] = await Promise.all([
      api<{items: CleanerOption[]}>("/cleaners"),
      api<Dashboard>(`/dashboard?service_date=${day}`),
    ]);
    setCleaners(cleanerList.items);
    setNames(Object.fromEntries(cleanerList.items.map(row => [row.id, row.display_name])));
    setTasks(Object.fromEntries([...snapshot.today, ...snapshot.tomorrow].map(row => [row.id, row.property_name])));
    setAssignments([...snapshot.today, ...snapshot.tomorrow].filter(row => row.assignment_id !== null));
  }

  async function toggleLock(assignmentId: string, version: number, currentlyLocked: boolean, reason: string) {
    await act(async () => {
      await api(`/assignments/${assignmentId}/lock`, {
        method: "POST",
        headers: {"Idempotency-Key": crypto.randomUUID(), "If-Match": `"${version}"`},
        body: JSON.stringify({locked: !currentlyLocked, reason}),
      });
      await dictionaries();
      setNotice(currentlyLocked ? "Assignment unlocked." : "Assignment locked.");
    }, true);
  }

  async function reassignAssignment(assignmentId: string, version: number, newCleanerId: string, newStart: string, reason: string) {
    await act(async () => {
      const isoStart = fromLocalInputToEasternISO(newStart);
      await api(`/assignments/${assignmentId}/override`, {
        method: "POST",
        headers: {"Idempotency-Key": crypto.randomUUID(), "If-Match": `"${version}"`},
        body: JSON.stringify({cleaner_id: newCleanerId, planned_start: isoStart, is_locked: false, reason}),
      });
      setReassignTarget(null);
      await dictionaries();
      setNotice("Assignment reassigned.");
    }, true);
  }

  const filteredAssignments = assignments.filter(row => {
    if (!row.assignment_id) return false;
    if (!row.planned_start) return false;
    const status = row.assignment_status ?? row.status;
    if (status === "completed" || status === "canceled" || status === "superseded") return false;
    const easternDate = toEasternDate(row.planned_start);
    return easternDate === day;
  });

  const canManage = (row: AssignmentRow) => {
    const status = row.assignment_status ?? row.status;
    return !local && status !== "completed" && status !== "canceled" && status !== "superseded";
  };

  return <main><h1>Schedules</h1><p className="muted">Review assignments before publishing. All times Eastern.</p>{local ? <LocalSchedulingPolicy canApprove={canApprove} onApproved={() => setPolicyApproved(true)} /> : null}
    <form className="toolbar" onSubmit={event => { event.preventDefault(); void act(async () => { await dictionaries(); const data = await api<Schedule>(`/schedules?service_date=${day}`); setRun(data.active_run); setLoaded(true); await refreshReplies(); }); }}>
      <button type="button" aria-label="Previous day" disabled={busy || !hydrated || !day} onClick={() => moveDay(-1)}>←</button><label>Cleaning date<input aria-label="Schedule date" disabled={busy || !hydrated} type="date" value={day} onChange={event => chooseDay(event.target.value)} /></label><button type="button" aria-label="Next day" disabled={busy || !hydrated || !day} onClick={() => moveDay(1)}>→</button><button type="button" disabled={busy || !hydrated || !today || day === today} onClick={() => chooseDay(today)}>Today</button>
      <button disabled={busy || !hydrated || !day} type="submit">Load schedule</button>
      {canWrite ? <button disabled={busy || !hydrated || !day || (local && policyApproved !== true)} type="button" onClick={() => void act(async () => { await dictionaries(); setRun(await api<Run>("/schedules/previews", {method: "POST", signal: AbortSignal.timeout(75000), headers: {"Idempotency-Key": crypto.randomUUID()}, body: JSON.stringify({service_date: day})})); setLoaded(true); })}>Generate preview</button> : null}
    </form>
    {busy ? <p role="status">Working on the schedule…</p> : null}{error ? <p role="alert">{error}</p> : null}{notice ? <p role="status">{notice}</p> : null}
    {loaded && !run ? <section><h2>No active run for this date</h2><p>Generate a preview to see what can be covered.</p></section> : null}
    {run ? <section aria-label="Schedule plan"><div className="toolbar"><h2>{run.status === "preview" ? "Preview" : "Schedule plan"}</h2><span className="badge">{run.status}</span></div><p>{run.candidate_plan.placed.length} assignments · {run.uncovered.length} uncovered</p><p className="muted">{local ? "Saved OSRM driving times" : "Mock travel estimates"} · {run.solver_status.replaceAll("_", " ")}</p>
      <table><thead><tr><th>Cleaner</th><th>Property</th><th>Time (ET)</th><th>Travel before</th></tr></thead><tbody>{run.candidate_plan.placed.map(row => <tr key={row.task_id}><td>{names[row.cleaner_id] ?? "Cleaner"}</td><td>{tasks[row.task_id] ?? row.task_id}</td><td>{time(row.start)}–{time(row.end)}</td><td>{Math.round(row.travel_before_seconds / 60)} min</td></tr>)}</tbody></table>
      {run.uncovered.length ? <div role="alert"><strong>Uncovered work needs manager attention</strong><ul>{run.uncovered.map((row, i) => <li key={i}>{tasks[row.task_id] ?? "Task"}: {row.reason.replaceAll("_", " ")}</li>)}</ul></div> : null}
      {canWrite && run.status === "preview" ? <button disabled={busy || !hydrated || run.service_date !== day} onClick={() => void act(async () => { await api(`/schedules/runs/${run.id}/publish`, {method: "POST", signal: AbortSignal.timeout(75000), headers: {"Idempotency-Key": crypto.randomUUID(), "If-Match": `"${run.version}"`}, body: JSON.stringify({expected_fingerprint: run.input_fingerprint, expected_day_revision: run.day_revision})}); setRun((await api<Schedule>(`/schedules?service_date=${day}`)).active_run); await refreshReplies(); setNotice(local ? "Schedule published. Review assignment emails on Cleaners when ready to send." : "Schedule published. No carrier SMS sent."); })}>Publish validated schedule</button> : null}
    </section> : null}
    {canWrite && !local && filteredAssignments.length > 0 ? <section aria-label="Assignment management" role="region"><h2>Assignment Management</h2><p className="muted">Lock assignments to protect them from automatic replanning. Unlock before reassigning.</p>
      <table className="cleaning-table"><thead><tr><th>House</th><th>Cleaner</th><th>Time</th><th>Status</th><th>Lock</th><th>Actions</th></tr></thead>
      <tbody>{filteredAssignments.map(row => <tr key={row.assignment_id} className={row.is_locked ? "locked-row" : ""}>
        <td>{row.property_name}</td>
        <td>{row.cleaner_name ?? "Unassigned"}</td>
        <td>{row.planned_start ? `${time(row.planned_start)}–${time(row.planned_end!)}` : "Not scheduled"}</td>
        <td><span className={`badge ${(row.assignment_status ?? row.status) === "completed" ? "" : (row.assignment_status ?? row.status) === "blocked" ? "attention" : ""}`}>{row.assignment_status ?? row.status}</span></td>
        <td>{row.is_locked ? <span className="badge attention" title={row.lock_reason ?? ""}>Locked</span> : <span className="badge">Unlocked</span>}</td>
        <td>
          <button data-testid={`lock-${row.assignment_id}`} disabled={busy || !hydrated || !canManage(row)} onClick={() => {
            const reason = row.is_locked ? "Manager unlocked assignment" : (window.prompt("Lock reason:") ?? "");
            if (reason) void toggleLock(row.assignment_id!, row.assignment_version!, row.is_locked, reason);
          }}>{row.is_locked ? "Unlock" : "Lock"}</button>
          {!row.is_locked && canManage(row) ? <button data-testid={`reassign-${row.assignment_id}`} disabled={busy || !hydrated} onClick={() => setReassignTarget({id: row.assignment_id!, version: row.assignment_version!, cleanerId: row.cleaner_id ?? "", start: row.planned_start ?? new Date().toISOString(), propertyId: row.property_id})}>Reassign</button> : null}
        </td>
      </tr>)}</tbody></table>
    </section> : null}
    {canWrite && !local && reassignTarget ? <dialog open aria-label="Reassign assignment"><form onSubmit={event => {
      event.preventDefault();
      const form = event.currentTarget;
      const cleanerId = (form.elements.namedItem("cleaner") as HTMLSelectElement).value;
      const start = (form.elements.namedItem("start") as HTMLInputElement).value;
      const reason = window.prompt("Reassignment reason:") ?? "";
      if (cleanerId && start && reason) void reassignAssignment(reassignTarget.id, reassignTarget.version, cleanerId, start, reason);
    }}><h3>Reassign Assignment</h3>
      <label>New cleaner<select name="cleaner" defaultValue={reassignTarget.cleanerId} required>{cleaners.filter(c => c.eligible_property_ids?.includes(reassignTarget.propertyId)).map(c => <option key={c.id} value={c.id}>{c.display_name}</option>)}</select></label>
      <label>Start time<input name="start" type="datetime-local" defaultValue={toEasternLocalInput(reassignTarget.start)} required /></label>
      <div><button type="submit" disabled={busy}>Reassign</button><button type="button" onClick={() => setReassignTarget(null)}>Cancel</button></div>
    </form></dialog> : null}
    {demo && canWrite ? <section aria-label="Cleaner simulator"><h2>Cleaner message simulator</h2><p className="muted">These actions run the existing SMS command handler as a fictional cleaner. Completion uses the fixed demo time of November 10 at 1 PM ET; future work cannot be completed early.</p><button disabled={busy || !hydrated} onClick={() => void act(async () => {const result = await api<{recorded: number}>("/demo/record-sms", {method: "POST"}); await refreshReplies(); setNotice(`Recorded ${result.recorded} mock messages. No external send.`);})}>Record mock schedule messages</button>
      <ul className="task-list">{replies.filter(row => row.service_date === day).map(row => <li key={row.id}><div><strong>{row.cleaner}</strong><small>{tasks[row.task_id] ?? row.task_id} · {row.status}</small></div><div>{(["CONFIRM", "DONE", "UNAVAILABLE"] as const).map(command => <button disabled={busy || !hydrated || row.status === "completed"} key={command} onClick={() => void act(async () => { await api("/demo/reply", {method: "POST", body: JSON.stringify({assignment_id: row.id, command, event_id: crypto.randomUUID()})}); await refreshReplies(); setNotice(`${command} processed. Review assignment status and dashboard for the result.`); })}>{command}</button>)}</div></li>)}</ul>
    </section> : null}
  </main>;
}
