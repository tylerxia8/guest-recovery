"use client";
import { useEffect, useState } from "react";
import { api } from "../lib/api";

type Review = {approved: boolean; fingerprint: string; settings: Record<string, string | number | boolean | Record<string, number>>};
export function LocalSchedulingPolicy({canApprove, onApproved}: {canApprove: boolean; onApproved: () => void}) {
  const [review, setReview] = useState<Review | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    api<Review>("/local-scheduling/policy").then(result => {if (active) setReview(result);}).catch(reason => {if (active) setError(reason instanceof Error ? reason.message : "Scheduling rules could not be loaded.");});
    return () => {active = false;};
  }, []);
  async function approve() {
    if (!review) return;
    setBusy(true);
    setError("");
    try {
      setReview(await api<Review>("/local-scheduling/policy", {method: "POST", headers: {"Idempotency-Key": `local-policy-${review.fingerprint}`}, body: JSON.stringify({fingerprint: review.fingerprint}), signal: AbortSignal.timeout(75000)}));
      onApproved();
    } catch (reason) {setError(reason instanceof Error ? reason.message : "Approval could not be saved.");}
    finally {setBusy(false);}
  }
  if (review?.approved) return null;
  return <section className="local-scheduling-policy"><h2>Review scheduling rules</h2>{review ? <><p>Working hours, breaks and house exceptions set each cleaner’s capacity. Cleaning times follow bedroom defaults unless overridden.</p><p>Finish at least <strong>{String(review.settings.readiness_buffer_minutes)} minutes</strong> before the next arrival. One cleaner per house. Existing locked assignments stay protected.</p><p>Publication and emails require separate actions. Missing acknowledgment never triggers automatic reassignment.</p><details><summary>All rule values</summary><dl>{Object.entries(review.settings).map(([name, value]) => <div key={name}><dt>{name.replaceAll("_", " ")}</dt><dd>{typeof value === "object" ? Object.entries(value).map(([bedrooms, minutes]) => `${bedrooms} bedrooms: ${minutes} min`).join(" · ") : String(value)}</dd></div>)}</dl></details>{canApprove ? <button type="button" disabled={busy} onClick={() => void approve()}>{busy ? "Saving…" : "Approve scheduling rules"}</button> : <p>An owner must approve these rules before generating a preview.</p>}</> : !error ? <p role="status">Loading rules…</p> : null}{error ? <p role="alert">{error}</p> : null}</section>;
}
