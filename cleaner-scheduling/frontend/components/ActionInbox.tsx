"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";

const labels = {cleaner: "Cleaner", "guest-replies": "Guest Messages", supplies: "Supplies", maintenance: "Maintenance"};
type Source = keyof typeof labels;
type Item = {id: string; source: Source; title: string; property: string | null; next_action: string; due_at: string | null; urgent: boolean; href: string};
type Claim = {source: Source; item_id: string; assignee_subject: string | null; version: number};
type Inbox = {
  items: Item[];
  claims: Claim[];
  managers: {name: string; subject: string}[];
  current_manager: string;
  unavailable: string[];
};

const dueDate = new Intl.DateTimeFormat("en-US", {timeZone: "America/New_York", dateStyle: "medium", timeStyle: "short"});

export function ActionInbox() {
  const [data, setData] = useState<Inbox>();
  const [filter, setFilter] = useState<Source | "all">("all");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const response = await api<Inbox>("/action-inbox", {cache: "no-store", signal: signal ?? AbortSignal.timeout(25000)});
      if (!signal?.aborted) setData(response);
    } catch {
      if (!signal?.aborted) setError("Could not load the inbox. Refresh to try again.");
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  async function assign(item: Item, subject: string | null, version: number) {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await api<{version: number; assignee_subject: string | null}>("/workspace/action-inbox/claim", {
        method: "PATCH", body: JSON.stringify({source: item.source, item_id: item.id, assignee_subject: subject, version}),
      });
      setData(current => current && ({
        ...current, claims: [
          ...current.claims.filter(claim => claim.source !== item.source || claim.item_id !== item.id),
          {source: item.source, item_id: item.id, ...result},
        ],
      }));
      setNotice(subject === null ? "Assignment released." : "Assignment saved.");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not save the assignment.");
      await load();
    } finally {
      setBusy(false);
    }
  }

  const items = (data?.items ?? []).filter(item => filter === "all" || filter === item.source).sort((a, b) =>
    Number(b.urgent) - Number(a.urgent) || (a.due_at ?? "~").localeCompare(b.due_at ?? "~"),
  );
  return <main className="action-inbox">
    <header><div><h1>Action inbox</h1><p>Review work that needs a manager.</p></div>
      <button type="button" disabled={busy} onClick={() => {setError(""); void load();}}>Refresh</button>
    </header>
    {error ? <p role="alert">{error}</p> : null}
    {notice ? <p role="status">{notice}</p> : null}
    {data?.unavailable.length ? <p role="status">Unavailable queue: {data.unavailable.join(", ")}.</p> : null}
    {!data && !error ? <p role="status">Loading inbox…</p> : null}
    {data ? <>
      <label>Tool <select value={filter} onChange={event => setFilter(event.target.value as Source | "all")}>
        <option value="all">All tools</option>
        {Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
      {items.length ? <ul className="action-inbox-items">{items.map(item => {
        const claim = data.claims.find(row => row.source === item.source && row.item_id === item.id);
        const assigned = claim?.assignee_subject ?? "";
        const version = claim?.version ?? 0;
        return <li key={item.source + item.id}>
          <div>
            <p>{labels[item.source]}{item.urgent ? " · Urgent" : ""}</p>
            <h2>{item.title}</h2>
            {item.property ? <p>{item.property}</p> : null}
            {item.due_at ? <p>Due <time dateTime={item.due_at}>{dueDate.format(new Date(item.due_at))} Eastern</time></p> : null}
            <p>{item.next_action}</p>
          </div>
          <div className="action-inbox-controls">
            <label>Assigned to <select value={assigned} disabled={busy} onChange={event => void assign(item, event.target.value || null, version)}>
              <option value="">Unassigned</option>
              {data.managers.map(manager => <option key={manager.subject} value={manager.subject}>{manager.name}</option>)}
            </select></label>
            {!assigned ? <button type="button" disabled={busy} onClick={() => void assign(item, data.current_manager, version)}>Claim</button> : null}
            <a href={item.href}>Open {labels[item.source]}</a>
          </div>
        </li>;
      })}</ul> : <p>No items in the available queues.</p>}
    </> : null}
  </main>;
}
