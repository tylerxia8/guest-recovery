"use client";

import { useState } from "react";
import { api, type ApiError } from "../lib/api";

export function EarlyCheckout({taskId, version, onConfirmed}: {taskId: string; version: number; onConfirmed: (message: string) => void}) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState<{key: string; version: number} | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function confirm() {
    if (busy) return;
    const change = pending ?? {key: crypto.randomUUID(), version};
    setPending(change); setBusy(true); setError("");
    try {
      let result: {checkout_result?: string} | undefined;
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          result = await api<{checkout_result?: string}>(`/tasks/${taskId}/target`, {method: "POST", headers: {"Idempotency-Key": change.key, "If-Match": String(change.version)}, body: JSON.stringify({action: "confirm_checkout", confirmed: true, reason: "Manager confirmed that the guest has left and the unit is available."})});
          break;
        } catch (failure) {
          const status = (failure as ApiError).status;
          if ((status && status < 500 && ![408, 429].includes(status)) || attempt === 2) throw failure;
          await new Promise(resolve => setTimeout(resolve, 500 * 2 ** attempt));
        }
      }
      if (!result?.checkout_result) throw new Error("Departure confirmation could not be verified.");
      setPending(null); setOpen(false);
      onConfirmed(result.checkout_result === "same_cleaner_moved_earlier" ? "Guest departure confirmed. The same cleaner’s job was moved earlier; an updated schedule will be emailed." : result.checkout_result === "ready_for_earlier_allocation" ? "Guest departure confirmed. This house can now be allocated earlier." : "Guest departure confirmed. The existing cleaning time was kept because an earlier move was not available or the assignment is protected.");
    } catch (failure) {
      const status = (failure as ApiError).status;
      if (status && status < 500 && ![408, 429].includes(status)) {
        setPending(null);
        setError((failure as Error).message);
      } else {
        setError("The result is not confirmed. Retry the same confirmation below.");
      }
    } finally {
      setBusy(false);
    }
  }

  if (!open) return <button type="button" className="workspace-secondary" onClick={() => setOpen(true)}>Guest has left</button>;
  return <div>
    <p>Are you sure the guest has left and the unit is available now? Confirm this directly with the guest or your team. This only updates Lucena; it does not change Hospitable.</p>
    <p>We will try an earlier time with the same cleaner while keeping other jobs fixed. Locked or started work stays unchanged.</p>
    {error ? <p role="alert">{error}</p> : null}
    <button type="button" disabled={busy} onClick={() => void confirm()}>{busy ? "Confirming…" : pending ? "Retry the same confirmation" : "Yes, the guest has left"}</button>
    <button type="button" className="workspace-secondary" disabled={busy || Boolean(pending)} onClick={() => {setOpen(false); setError("");}}>Cancel</button>
  </div>;
}
