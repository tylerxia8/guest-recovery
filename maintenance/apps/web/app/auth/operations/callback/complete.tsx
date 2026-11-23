"use client";

import { useEffect, useRef, useState } from "react";
import { setToken } from "../../../../lib/api";

export function CompleteOperationsLogin() {
  const attempted = useRef(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (attempted.current) return;
    attempted.current = true;
    const query = window.location.search;
    window.history.replaceState(null, "", window.location.pathname);
    fetch("/auth/operations/exchange" + query, {method: "POST", signal: AbortSignal.timeout(14000)})
      .then(async response => {
        if (!response.ok) throw new Error("Sign-in failed");
        const {token} = await response.json();
        if (typeof token !== "string" || !token) throw new Error("Invalid session");
        setToken(token);
        window.location.replace("/maintenance/review");
      })
      .catch(() => setFailed(true));
  }, []);

  return <main><h1>Maintenance</h1>{failed
    ? <p role="alert">Shared sign-in could not be completed. <a href="/auth/operations/start">Try again</a></p>
    : <p role="status">Opening your repair workspace…</p>}</main>;
}
