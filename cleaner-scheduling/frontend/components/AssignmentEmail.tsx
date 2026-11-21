"use client";
import { useEffect, useState } from "react";
import { api } from "../lib/api";

type CleanerSchedule = {cleaner_id: string; cleaner_name: string; email_stored: boolean; assignment_count: number};
type Schedules = {service_date: string; business_date: string; items: CleanerSchedule[]};
type Preview = {cleaner_id: string; recipient: string; subject: string; body: string; fingerprint: string};
export function AssignmentEmail() {
  const [date, setDate] = useState("");
  const [data, setData] = useState<Schedules | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [finished, setFinished] = useState(false);
  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const result = await api<Schedules>(`/email/assignments${date ? `?service_date=${encodeURIComponent(date)}` : ""}`);
        if (active) {setData(result); setMessage("");}
      } catch (reason) {
        if (active) setMessage(reason instanceof Error ? reason.message : "Schedules could not be loaded.");
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => {active = false;};
  }, [date]);
  function changeDate(value: string) {
    setPreview(null);
    setData(null);
    setLoading(true);
    setDate(value);
  }
  async function openPreview(cleaner: string) {
    if (!data) return;
    setBusy(true);
    setPreview(null);
    setMessage("");
    try {
      setPreview(await api<Preview>(`/email/assignments/${cleaner}?service_date=${encodeURIComponent(data.service_date)}`));
      setFinished(false);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "The preview could not be loaded.");
    } finally {setBusy(false);}
  }
  async function send() {
    if (!preview || !data) return;
    setBusy(true);
    setMessage("");
    try {
      const result = await api<{status: string; message: string}>(`/email/assignments/${preview.cleaner_id}`, {
        method: "POST", headers: {"Idempotency-Key": `assignment-email-${preview.fingerprint}`},
        body: JSON.stringify({service_date: data.service_date, fingerprint: preview.fingerprint}),
        signal: AbortSignal.timeout(75000),
      });
      setMessage(result.message);
      setFinished(true);
    } catch (reason) {
      setMessage(`${reason instanceof Error ? reason.message : "Send result unavailable."} Check the inbox before trying again; retrying checks the same request.`);
    } finally {setBusy(false);}
  }
  return <div className="sample-assignment-email"><div className="sample-email-heading"><div><h3>Assignment emails</h3><p>Preview each cleaner’s published schedule before sending.</p></div><div className="assignment-email-date"><label>Cleaning date<input type="date" value={date || data?.service_date || ""} disabled={busy} onChange={event => changeDate(event.target.value)} /></label><button type="button" disabled={busy || !date} onClick={() => changeDate("")}>Today</button></div></div>
    {loading ? <p role="status">Loading schedules…</p> : data && !data.items.length ? <p className="assignment-email-empty">No published assignments for this date.</p> : null}
    {data?.items.length ? <ul className="assignment-email-list">{data.items.map(item => <li key={item.cleaner_id}><div><strong>{item.cleaner_name}</strong><span>{item.assignment_count} {item.assignment_count === 1 ? "house" : "houses"}{item.email_stored ? "" : " · Add an email below"}</span></div><button type="button" disabled={busy || !item.email_stored} onClick={() => void openPreview(item.cleaner_id)}>Preview email</button></li>)}</ul> : null}
    {preview ? <div className="sample-email-preview"><dl><div><dt>To</dt><dd>{preview.recipient}</dd></div><div><dt>Subject</dt><dd>{preview.subject}</dd></div></dl><pre>{preview.body}</pre><div className="sample-email-actions"><span>Only this cleaner receives this schedule.</span><button type="button" disabled={busy || finished} onClick={() => void send()}>{busy ? "Sending…" : finished ? "Send recorded" : "Send assignment email"}</button></div></div> : null}
    {message ? <p className="sample-email-result" role="status">{message}</p> : null}
  </div>;
}
