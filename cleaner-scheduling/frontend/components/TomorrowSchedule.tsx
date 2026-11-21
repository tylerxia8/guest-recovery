"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { TaskList, type TaskRow } from "./OperationalDashboard";

type Snapshot = {tomorrow_date: string; tomorrow: TaskRow[]};
export function TomorrowSchedule() {
  const [data, setData] = useState<Snapshot | null>(null); const [error, setError] = useState("");
  const [cleaner, setCleaner] = useState("");
  useEffect(() => {
    let active = true; let pending = false;
    async function refresh() {
      if(pending) return; pending = true;
      try {const row = await api<Snapshot>("/dashboard"); if(active) {setData(row); setError("");}}
      catch {if(active) setError("Could not refresh tomorrow’s schedule. Displayed assignments may be out of date.");}
      finally {pending = false;}
    }
    void refresh(); const timer = setInterval(() => void refresh(), 30000);
    return () => {active = false; clearInterval(timer);};
  }, []);
  const cleaners = Array.from(new Set(data?.tomorrow.flatMap(row => row.cleaner_name ? [row.cleaner_name] : []) ?? [])).sort();
  const tasks = data?.tomorrow.filter(row => !cleaner || row.cleaner_name === cleaner) ?? [];
  return <main><p className="eyebrow">AUTOMATED DAILY PLAN</p><h1>Tomorrow’s schedule</h1>
    <p>{data?.tomorrow_date} · All times Eastern. Refreshes every 30 seconds using the server business date.</p>
    <p><Link href="/dashboard">Today and exceptions</Link> · <Link href="/setup">Set up automation</Link></p>
    {error ? <p role="alert">{error}</p> : null}
    {!data && !error ? <p>Loading tomorrow’s schedule</p> : null}
    {data ? <><label>Filter by cleaner<select value={cleaner} onChange={event => setCleaner(event.target.value)}><option value="">Everyone</option>{cleaners.map(name => <option key={name}>{name}</option>)}</select></label>
      <p>{data.tomorrow.length} houses · {data.tomorrow.filter(row => !row.cleaner_name && row.status !== "completed").length} awaiting assignment</p>
      <p>Unassigned houses need manager attention. An allocation appears only after a feasible schedule is published.</p>
      {tasks.length ? <TaskList tasks={[...tasks].sort((left, right) => (left.planned_start ?? "z").localeCompare(right.planned_start ?? "z"))} /> : <p>No cleaning work to show.</p>}
    </> : null}
  </main>;
}
