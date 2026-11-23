"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { enterDemoRole } from "../lib/demo";

export default function DemoEntry() {
  const router = useRouter();
  const started = useRef(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const view = new URLSearchParams(window.location.search).get("view");
    void enterDemoRole(view === "manager" ? "property_manager" : "tenant")
      .then(destination => router.replace(destination))
      .catch(failure => setError(failure instanceof Error ? failure.message : "Please try again."));
  }, [router]);

  return error
    ? <><p role="alert">{error}</p><button type="button" onClick={() => window.location.reload()}>Try again</button></>
    : <p role="status">Opening repair chat…</p>;
}
