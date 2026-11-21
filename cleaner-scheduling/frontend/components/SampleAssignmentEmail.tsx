"use client";
import { useState } from "react";
import { api } from "../lib/api";

type Preview = {cleaner_id: string; cleaner_version: number; recipient: string; subject: string; body: string; fingerprint: string};
export function SampleAssignmentEmail() {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  const [finished, setFinished] = useState(false);
  async function loadPreview() {
    setBusy(true);
    setResult("");
    try {
      let loaded: Preview | null = null;
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          loaded = await api<Preview>("/email/sample-assignment");
          break;
        } catch (reason) {
          if (attempt === 2) throw reason;
          await new Promise(resolve => setTimeout(resolve, 500 * 2 ** attempt));
        }
      }
      setPreview(loaded);
      setFinished(false);
    } catch (reason) {
      setResult(reason instanceof Error ? reason.message : "The preview could not be loaded.");
    } finally {
      setBusy(false);
    }
  }
  async function send() {
    if (!preview) return;
    setBusy(true);
    setResult("");
    try {
      const response = await api<{status: string; message: string}>("/email/sample-assignment", {
        method: "POST",
        headers: {"Idempotency-Key": `sample-assignment-${preview.fingerprint}`},
        body: JSON.stringify({fingerprint: preview.fingerprint}),
        signal: AbortSignal.timeout(75000),
      });
      setResult(response.message);
      setFinished(true);
    } catch (reason) {
      setResult(`${reason instanceof Error ? reason.message : "Send result unavailable."} Check the inbox before trying again; retrying checks this same request.`);
    } finally {
      setBusy(false);
    }
  }
  return <div className="sample-assignment-email">
    <div className="sample-email-heading"><div><h3>Sample assignment</h3><p>A fictional house and cleaning window for Benito’s demo profile.</p></div><button type="button" disabled={busy} onClick={() => void loadPreview()}>{busy && !preview ? "Loading…" : preview ? "Refresh preview" : "Preview email"}</button></div>
    {preview ? <div className="sample-email-preview"><dl><div><dt>To</dt><dd>{preview.recipient}</dd></div><div><dt>Subject</dt><dd>{preview.subject}</dd></div></dl><pre>{preview.body}</pre><div className="sample-email-actions"><span>No real cleaning job is created.</span><button type="button" disabled={busy || finished} onClick={() => void send()}>{busy ? "Sending…" : finished ? "Send recorded" : "Send sample email"}</button></div></div> : null}
    {result ? <p className="sample-email-result" role="status">{result}</p> : null}
  </div>;
}
