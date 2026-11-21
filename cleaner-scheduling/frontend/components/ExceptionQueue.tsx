"use client";

import { useEffect, useState } from "react";
import { api, type ApiError } from "../lib/api";
import { EmptyState } from "./EmptyState";

type ExceptionRow = {
  id: string;
  version: number;
  code: string;
  status: string;
  owner_subject: string | null;
  severity: string;
};

export function ExceptionQueue({ canWrite }: { canWrite: boolean }) {
  const [status, setStatus] = useState("open");
  const [snapshot, setSnapshot] = useState<{ status: string; items: ExceptionRow[]; error: string } | null>(null);
  const [revision, setRevision] = useState(0);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let cancelled = false;
    api<{ items: ExceptionRow[] }>(`/exceptions?status=${status}`)
      .then((body) => {
        if (!cancelled) {
          setSnapshot({ status, items: body.items, error: "" });
        }
      })
      .catch((reason: ApiError) => {
        if (!cancelled) {
          setSnapshot({
            status,
            items: [],
            error: reason.body?.error?.message ?? "Exceptions could not be loaded.",
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [status, revision]);

  const loading = snapshot === null || snapshot.status !== status;
  const items = snapshot?.status === status ? snapshot.items : [];
  const error = snapshot?.status === status ? snapshot.error : "";

  return (
    <main>
      <h1>Exception queue</h1>
      <label>
        Status
        <select aria-label="Exception status" value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="open">open</option>
          <option value="acknowledged">acknowledged</option>
          <option value="resolved">resolved</option>
        </select>
      </label>
      {loading ? <p>Loading exceptions</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      {notice ? <p role="status">{notice}</p> : null}
      {!loading && items.length === 0 ? <EmptyState variant="exceptions" title="No exceptions in this filter." /> : null}
      <ul>
        {items.map((item) => (
          <li key={item.id}>
            <span>
              {item.code} {item.severity} {item.status} owner {item.owner_subject ?? "unassigned"}
            </span>
            {canWrite ? (
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  const form = new FormData(event.currentTarget, (event.nativeEvent as SubmitEvent).submitter);
                  const owner = String(form.get("owner") ?? "");
                  const action = String(form.get("action") ?? "owner");
                  const reason = String(form.get("reason") ?? "");
                  const body =
                    action === "resolve"
                      ? { status: "resolved", resolution_reason: reason }
                      : { status: "acknowledged", owner_subject: owner };
                  setNotice("");
                  api(`/exceptions/${item.id}`, {
                    method: "PATCH",
                    headers: { "If-Match": `"${item.version}"` },
                    body: JSON.stringify(body),
                  })
                    .then(() => {
                      setNotice("Exception updated.");
                      setRevision((current) => current + 1);
                    })
                    .catch((reasonError: ApiError) => {
                      const message =
                        reasonError.status === 409
                          ? "This exception changed. Reload and try again."
                          : (reasonError.body?.error?.message ?? "Update failed.");
                      setSnapshot({ status, items, error: message });
                    });
                }}
              >
                <label>
                  Owner
                  <input name="owner" aria-label={`Owner for ${item.code}`} defaultValue={item.owner_subject ?? ""} />
                </label>
                <label>
                  Resolution reason
                  <input name="reason" aria-label={`Reason for ${item.code}`} />
                </label>
                <button type="submit" name="action" value="owner">
                  Assign owner
                </button>
                <button type="submit" name="action" value="resolve">
                  Resolve
                </button>
              </form>
            ) : null}
          </li>
        ))}
      </ul>
    </main>
  );
}
