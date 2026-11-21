"use client";
import { useEffect, useState } from "react";
import { api, type ApiError } from "../lib/api";

type Status = {configured: boolean; automatic_assignments_enabled: boolean};
export function GmailSetup() {
  const [status, setStatus] = useState<Status | null>(null);
  const [message, setMessage] = useState("");
  useEffect(() => {
    let active = true;
    async function load() {
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          const result = await api<Status>("/email/status", {signal: AbortSignal.timeout(20000)});
          if (active) {setStatus(result); setMessage("");}
          return;
        } catch (reason) {
          const failure = reason as ApiError;
          if (failure.status && failure.status < 500 && failure.status !== 429) {
            if (active) setMessage(failure.status === 401 ? "Your session expired. Use Reconnect above to continue." : failure.message);
            return;
          }
          if (attempt === 2 && active) setMessage("Email status could not be loaded. Refresh to try again.");
        }
        await new Promise(resolve => setTimeout(resolve, 500 * 2 ** attempt));
        if (!active) return;
      }
    }
    void load();
    return () => {active = false;};
  }, []);
  const enabled = !!status?.configured && status.automatic_assignments_enabled;
  return <section className="gmail-setup"><div><h2>Assignment emails</h2><p>{enabled ? "Daily schedules email automatically at 8 PM Eastern. Schedule changes are emailed promptly." : status?.configured ? "Gmail connected. Automatic assignment emails are paused." : status ? "Connect Gmail to email cleaner assignments." : "Loading email status…"}</p></div><span className="badge">{status ? enabled ? "Automatic" : "Paused" : "Loading"}</span>{message ? <p className="gmail-message" role="status">{message}</p> : null}</section>;
}
