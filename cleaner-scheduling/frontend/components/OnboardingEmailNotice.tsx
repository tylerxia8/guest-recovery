"use client";

import { useEffect, useState } from "react";
import { api, type ApiError } from "../lib/api";

type EmailStatus = {
  counts: {total: number; queued: number; sending: number; accepted: number; uncertain: number; failed: number; stale: number};
  enabled: boolean;
};

export function OnboardingEmailNotice() {
  const [status, setStatus] = useState<EmailStatus | null>(null);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    async function refresh() {
      try {
        const result = await api<EmailStatus>("/workspace/onboarding-emails", {signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20000)])});
        if (controller.signal.aborted) return;
        setStatus(result);
        setError(false);
        failures = 0;
        if (result.counts.queued + result.counts.sending > 0) timer = setTimeout(() => void refresh(), 5000);
      } catch (failure) {
        if (controller.signal.aborted) return;
        const code = (failure as ApiError).status;
        failures += 1;
        if (failures < 3 && (!code || code === 429 || code >= 500)) {
          timer = setTimeout(() => void refresh(), 1000 * 2 ** (failures - 1));
        } else {
          setError(true);
        }
      }
    }
    void refresh();
    return () => {controller.abort(); clearTimeout(timer);};
  }, [retry]);

  if (error) return <section className="onboarding-email-notice" role="alert"><strong>Onboarding email status unavailable</strong><button type="button" onClick={() => {setError(false); setStatus(null); setRetry(value => value + 1);}}>Retry</button></section>;
  if (!status || !status.counts.total) return null;
  const {queued, sending, uncertain, failed, stale} = status.counts;
  const pending = queued + sending;
  const attention = failed + stale;
  if (!pending && !attention && !uncertain) return null;
  const title = pending ? status.enabled ? `Sending onboarding links to ${pending} cleaners…` : `${pending} onboarding invitations queued` : "Onboarding invitations need attention";
  const detail = [
    pending && !status.enabled ? "Email sending is paused." : "",
    uncertain ? `Check delivery for ${uncertain} invitations before resending.` : "",
    attention ? `${attention} invitations need attention. Review cleaner emails and email settings.` : "",
  ].filter(Boolean).join(" ") || "Invitations are queued for email delivery.";
  return <section className="onboarding-email-notice" role={uncertain || attention ? "alert" : "status"}><strong>{title}</strong><small>{detail}</small></section>;
}
