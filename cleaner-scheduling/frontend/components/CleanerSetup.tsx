"use client";
import type { WorkspaceMode } from "../lib/session";
import { useEffect, useState, type FormEvent } from "react";
import { GmailSetup } from "./GmailSetup";
import { CleanerOnboardingLink } from "./CleanerOnboardingLink";
import { WeeklyHours } from "./WeeklyHours";
import { ReadingLanguages, readingLanguages, PreferredLanguage } from "./ReadingLanguages";
import { api, type ApiError } from "../lib/api";
import { properties as loadProperties } from "../lib/properties";
import { EmptyState } from "./EmptyState";

type Cleaner = { id: string; version: number; display_name: string; sms_consent_status: string; phone_stored: boolean; email_stored: boolean; reading_languages: string[]; preferred_language: "en" | "es" | "pt" | null };
type House = { id: string; name: string };
type Workday = { version: number; origin_label: string | null; work_intervals: {start_at: string; end_at: string}[]; unavailable_intervals: {start_at: string; end_at: string}[] };
type Eligibility = {version: number; eligibility_policy: string; excluded_property_ids: string[]};
const localTime = (value: string | undefined) => value ? new Intl.DateTimeFormat("en-GB", {timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hourCycle: "h23"}).format(new Date(value)) : "";
export function CleanerSetup({ canWrite, sourceMode = "mock", configureEmail = false }: {canWrite: boolean; sourceMode?: WorkspaceMode; configureEmail?: boolean}) {
  const liveReadOnly = sourceMode === "hospitable_read_only";
  const [items, setItems] = useState<Cleaner[]>([]); const [houses, setHouses] = useState<House[]>([]);
  const [error, setError] = useState(""); const [notice, setNotice] = useState(""); const [busy, setBusy] = useState(false);
  const [houseSearch, setHouseSearch] = useState("");
  const [removing, setRemoving] = useState<Cleaner | null>(null);
  const [emailing, setEmailing] = useState<Cleaner | null>(null);
  const [editingLanguages, setEditingLanguages] = useState<Cleaner | null>(null);
  const [cleaner, setCleaner] = useState(""); const [day, setDay] = useState(""); const [workday, setWorkday] = useState<Workday | null>(null);
  const [eligibility, setEligibility] = useState<Eligibility | null>(null);
  useEffect(() => {let active = true; if(cleaner) void api<Eligibility>(`/cleaners/${cleaner}/eligibility`).then(row => {if(active) setEligibility(row);}).catch(() => {if(active) setError("House exceptions could not be loaded.");}); return () => {active = false;};}, [cleaner]);
  useEffect(() => {let active = true; Promise.all([api<{items: Cleaner[]}>("/cleaners"), loadProperties<House>(true), api<{business_date: string}>("/setup")]).then(([c, p, s]) => {if(active) {setItems(c.items); setHouses(p.items); setDay(s.business_date);}}).catch(() => {if(active) setError("Cleaner setup could not be loaded.");}); return () => {active = false;};}, []);
  async function act(work: () => Promise<void>) {setBusy(true); setError(""); setNotice(""); try {await work();} catch(reason) {const failure = reason as ApiError; setError(failure.body?.error?.code === "duplicate_phone" ? "This phone number is already assigned to a cleaner." : failure.status === 409 ? "Information changed. Refresh this page and try again." : failure.message);} finally {setBusy(false);}}
  function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
    void act(async () => {
      const row = await api<Cleaner>("/cleaners", {method: "POST", headers: {"Idempotency-Key": crypto.randomUUID()}, body: JSON.stringify({
        display_name: String(data.get("name")).trim(), phone: data.get("phone"), email: String(data.get("email") ?? "").trim() || null, excluded_property_ids: data.getAll("exclusions"),
        max_jobs: data.get("maxJobs") ? Number(data.get("maxJobs")) : null,
        reading_languages: data.getAll("reading_languages"), preferred_language: data.get("preferred_language") || null,
      })});
      setItems((await api<{items: Cleaner[]}>("/cleaners")).items); setCleaner(row.id); setEligibility(null); setWorkday(null); form.reset(); setHouseSearch("");
      setNotice(liveReadOnly ? "Cleaner saved locally. Now choose regular weekly hours. No message was sent." : "Cleaner added. Now choose regular weekly hours. Record consent before dispatch.");
    });
  }
  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data = new FormData(event.currentTarget);
    void act(async () => {
      await api(`/cleaners/${cleaner}/workday`, {method: "POST", headers: {"Idempotency-Key": crypto.randomUUID()}, body: JSON.stringify({
        service_date: day, expected_version: workday!.version, start_time: data.get("start"), end_time: data.get("end"),
        break_start: data.get("breakStart") || null, break_end: data.get("breakEnd") || null, origin_label: data.get("origin"),
      })});
      setWorkday(await api<Workday>(`/cleaners/${cleaner}/availability?service_date=${day}`));
      setNotice("Date-specific hours saved in Eastern time. They override weekly hours for this date.");
    });
  }
  return <main><p className="eyebrow">SET UP YOUR TEAM</p><h1>Cleaners</h1>
    {error ? <p role="alert">{error}</p> : null}{notice ? <p role="status">{notice}</p> : null}
    {canWrite ? <section className="cleaner-create" aria-label="Add a cleaner">
      <div className="cleaner-create-heading"><div><h2>Add a cleaner</h2><p>Enter their details to get started.</p></div></div>
      <form className="cleaner-create-form" method="post" onSubmit={create}>
        <fieldset className="cleaner-contact" aria-label="Contact details"><div className="cleaner-contact-grid">
          <label>Cleaner name<input name="name" required maxLength={80} placeholder="Full name" disabled={busy} /></label>
          <label>Phone number<input name="phone" type="tel" required maxLength={64} placeholder="(704) 555-0123" autoComplete="tel" aria-label="Phone number" aria-describedby="cleaner-phone-hint" disabled={busy} /><small id="cleaner-phone-hint">Use a country code outside the US.</small></label>
          <label>Email <span className="field-optional">Optional</span><input name="email" type="email" maxLength={254} placeholder="cleaner@example.com" autoComplete="email" disabled={busy} /></label>
        </div></fieldset>
        <ReadingLanguages selected={[]} disabled={busy} legend="Languages they can read" /><PreferredLanguage disabled={busy} />
        <details className="cleaner-options"><summary>Options <span>Daily limit & house exceptions</span></summary><div className="cleaner-capacity"><label>Maximum houses per day <span className="field-optional">Optional</span><input name="maxJobs" type="number" min="1" max="20" step="1" placeholder="Default: 3" aria-label="Maximum houses per day (optional)" aria-describedby="cleaner-capacity-hint" disabled={busy} /></label><p id="cleaner-capacity-hint">Default: 3 houses, or up to 5 in one name group, such as HB 14 and HB 20. A lower limit always wins.</p></div>
        <details className="cleaner-house-exceptions"><summary>House exceptions <span className="field-optional">Optional</span></summary>
          <p>Select only houses they cannot clean.</p>
          <label className="house-search">Find a house<input type="search" placeholder="Search house names…" value={houseSearch} onChange={event => setHouseSearch(event.target.value)} onKeyDown={event => {if(event.key === "Enter") event.preventDefault();}} disabled={busy} /></label>
          <fieldset className="house-exclusion-list"><legend>Houses this cleaner cannot service</legend><div className="house-exclusion-options">
            {houses.map(house => <label className="house-exclusion-option" key={house.id} hidden={!house.name.toLocaleLowerCase().includes(houseSearch.trim().toLocaleLowerCase())}><input type="checkbox" name="exclusions" value={house.id} disabled={busy} /><span>{house.name}</span></label>)}
          </div></fieldset>
          {houses.length > 0 && !houses.some(house => house.name.toLocaleLowerCase().includes(houseSearch.trim().toLocaleLowerCase())) ? <p role="status">No houses match your search.</p> : null}
          {!houses.length ? <p>No houses available yet. New houses will be included automatically.</p> : null}

        </details>
        </details><p className="cleaner-included-note">All houses included unless excluded.</p>
        <div className="cleaner-create-actions"><button disabled={busy}>Add cleaner</button><p>{liveReadOnly ? "Saved locally. No text is sent." : "No texts are sent."}</p></div>
      </form>

    </section> : null}
    {canWrite ? <section aria-label="Working day"><h2>Cleaner onboarding and hours</h2>
      <label>Cleaner<select value={cleaner} disabled={busy} onChange={event => {setCleaner(event.target.value); setEligibility(null); setWorkday(null);}}><option value="">Choose a cleaner</option>{items.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>
      {cleaner && eligibility ? <details><summary>House exceptions</summary><p>{eligibility.eligibility_policy === "allowlist" ? "This cleaner has a previously saved house list. Saving these exceptions switches them to all current and future houses except the exclusions below." : "All current and future houses are allowed except these exclusions."}</p><form key={`${cleaner}:${eligibility.version}`} method="post" onSubmit={event => {event.preventDefault(); const form = new FormData(event.currentTarget); void act(async () => {
        setEligibility(await api<Eligibility>(`/cleaners/${cleaner}/eligibility`, {method: "POST", headers: {"Idempotency-Key": crypto.randomUUID()}, body: JSON.stringify({expected_version: eligibility.version, excluded_property_ids: form.getAll("excluded")})}));
        setItems((await api<{items: Cleaner[]}>("/cleaners")).items); setNotice("House exceptions saved locally. New houses are automatically allowed.");
      });}}><label>Excluded houses<select name="excluded" multiple size={5} defaultValue={eligibility.excluded_property_ids}>{houses.map(house => <option key={house.id} value={house.id}>{house.name}</option>)}</select></label><button disabled={busy}>Save house exceptions</button></form></details> : null}
      {cleaner ? <CleanerOnboardingLink key={`onboarding:${cleaner}`} cleaner={cleaner} /> : null}
      {cleaner ? <details><summary>Manager: edit weekly hours</summary><WeeklyHours key={cleaner} cleaner={cleaner} sourceMode={sourceMode} /></details> : null}
      {!liveReadOnly ? <details><summary>Date-specific hours (optional exception)</summary><p>These hours override the weekly schedule on this date.</p>
      <label>Working date<input type="date" value={day} onChange={event => {setDay(event.target.value); setWorkday(null);}} /></label>
      <button disabled={busy || !cleaner || !day} onClick={() => void act(async () => {setWorkday(await api<Workday>(`/cleaners/${cleaner}/availability?service_date=${day}`));})}>Load working day</button>
      {workday && workday.work_intervals.length <= 1 && workday.unavailable_intervals.length <= 1 ? <form method="post" key={`${cleaner}:${day}:${workday.version}`} onSubmit={save}>
        <label>Start time (Eastern)<input name="start" type="time" required defaultValue={localTime(workday.work_intervals[0]?.start_at)} /></label>
        <label>End time (Eastern)<input name="end" type="time" required defaultValue={localTime(workday.work_intervals[0]?.end_at)} /></label>
        <label>Break starts (optional)<input name="breakStart" type="time" defaultValue={localTime(workday.unavailable_intervals[0]?.start_at)} /></label>
        <label>Break ends (optional)<input name="breakEnd" type="time" defaultValue={localTime(workday.unavailable_intervals[0]?.end_at)} /></label>
        <label>Synthetic starting point<input name="origin" required pattern="synthetic:[a-zA-Z0-9_-]+" defaultValue={workday.origin_label ?? ""} placeholder="synthetic:cleaner-home" /></label>
        <button disabled={busy}>Save working day</button>
      </form> : null}</details> : null}</section> : null}
    {canWrite && cleaner && !liveReadOnly ? <section aria-label="Cleaner consent"><h2>Record synthetic SMS consent</h2><p>Consent is separate from delivery and accepting an assignment. Record fictional evidence only in this workspace.</p>
      <form method="post" onSubmit={event => {event.preventDefault(); const data = new FormData(event.currentTarget); void act(async () => {
        await api(`/cleaners/${cleaner}/consent`, {method: "POST", headers: {"Idempotency-Key": crypto.randomUUID()}, body: JSON.stringify({status: data.get("consent"), evidence_ref: data.get("evidence"), occurred_at: new Date().toISOString(), reason: "Synthetic setup demonstration only."})});
        setItems((await api<{items: Cleaner[]}>("/cleaners")).items); setNotice("Synthetic consent recorded. This does not confirm an assignment or enable live SMS.");
      });}}>
        <label>Consent status<select name="consent"><option value="opted_in">Opted in</option><option value="opted_out">Opted out</option></select></label>
        <label>Fictional evidence reference<input name="evidence" required maxLength={200} placeholder="synthetic-fresh-consent:demo-example" /></label>
        <button disabled={busy}>Record synthetic consent</button>
      </form></section> : null}
    {workday && (workday.work_intervals.length > 1 || workday.unavailable_intervals.length > 1) ? <p role="alert">This day has multiple shifts or breaks. The simple editor cannot change it safely.</p> : null}
    {configureEmail && liveReadOnly ? <GmailSetup /> : null}
    <section className="cleaner-roster"><div className="cleaner-roster-heading"><h2>Your cleaners</h2><span>{items.length} on your team</span></div>{!items.length ? <EmptyState variant="cleaners" title="No cleaners" /> : null}<ul className="cleaner-roster-list">{items.map(item => <li key={item.id}>
      <div className="cleaner-roster-person"><span className="cleaner-initials" aria-hidden="true">{item.display_name.split(" ").slice(0, 2).map(name => name[0]).join("")}</span><div><strong>{item.display_name}</strong><small className="cleaner-roster-languages">Reads: {readingLanguages.filter(([code]) => item.reading_languages.includes(code)).map(([, name]) => name).join(", ") || "Not set"}</small></div></div>
      <div className="cleaner-roster-status"><span>{item.email_stored ? "Email saved" : "No email"}</span>{!liveReadOnly ? <span>{item.sms_consent_status === "pending" ? "Consent pending" : item.sms_consent_status === "opted_in" ? "Consent recorded" : "Opted out"}</span> : null}<span>{item.phone_stored ? "Phone saved" : "No phone"}</span></div>
      {canWrite ? <div className="cleaner-roster-actions"><button className="cleaner-roster-email" type="button" disabled={busy} aria-label={`Edit reading languages for ${item.display_name}`} aria-expanded={editingLanguages?.id === item.id} aria-controls={`cleaner-languages-${item.id}`} onClick={() => {setEmailing(null); setEditingLanguages(editingLanguages?.id === item.id ? null : item);}}>Languages</button><button className="cleaner-roster-email" type="button" disabled={busy} aria-label={`${item.email_stored ? "Change" : "Add"} email for ${item.display_name}`} aria-expanded={emailing?.id === item.id} aria-controls={`cleaner-email-${item.id}`} onClick={() => {setEditingLanguages(null); setEmailing(emailing?.id === item.id ? null : item);}}>{item.email_stored ? "Change email" : "Add email"}</button><button className="cleaner-roster-remove" type="button" disabled={busy} aria-label={`Remove ${item.display_name}`} onClick={() => setRemoving(item)}>Remove</button></div> : null}
      {canWrite && editingLanguages?.id === item.id ? <form className="cleaner-language-form" id={`cleaner-languages-${item.id}`} key={`${item.id}:${editingLanguages.version}`} method="post" onSubmit={event => {event.preventDefault(); const data = new FormData(event.currentTarget); void act(async () => {
        await api(`/cleaners/${item.id}/languages`, {method: "POST", headers: {"Idempotency-Key": crypto.randomUUID()}, body: JSON.stringify({expected_version: editingLanguages.version, reading_languages: data.getAll("reading_languages"), preferred_language: data.get("preferred_language") || null})});
        setItems((await api<{items: Cleaner[]}>("/cleaners")).items); setEditingLanguages(null); setNotice("Reading languages saved.");
      });}}><ReadingLanguages selected={editingLanguages.reading_languages} disabled={busy} legend={`Languages ${item.display_name} can read`} /><PreferredLanguage selected={editingLanguages.preferred_language} disabled={busy} /><div className="cleaner-email-actions"><button disabled={busy}>Save languages</button><button type="button" disabled={busy} onClick={() => setEditingLanguages(null)}>Cancel</button></div></form> : null}
      {canWrite && emailing?.id === item.id ? <form className="cleaner-email-form" id={`cleaner-email-${item.id}`} key={item.id} method="post" onSubmit={event => {event.preventDefault(); const data = new FormData(event.currentTarget); void act(async () => {
        const email = String(data.get("email") ?? "").trim();
        await api(`/cleaners/${item.id}/email`, {method: "POST", headers: {"Idempotency-Key": crypto.randomUUID()}, body: JSON.stringify({expected_version: emailing.version, email})});
        setItems((await api<{items: Cleaner[]}>("/cleaners")).items); setEmailing(null); setNotice(email ? "Email saved locally. No message was sent." : "Email removed. No message was sent.");
      });}}><label>Email for {item.display_name}<input name="email" type="email" maxLength={254} placeholder="cleaner@example.com" autoComplete="email" aria-describedby={`cleaner-email-hint-${item.id}`} disabled={busy} /></label><div className="cleaner-email-actions"><button disabled={busy}>Save email</button><button type="button" disabled={busy} onClick={() => setEmailing(null)}>Cancel</button></div><small id={`cleaner-email-hint-${item.id}`}>{item.email_stored ? "Enter a replacement address, or leave blank to remove it." : "Saved locally. No message is sent."}</small></form> : null}
    </li>)}</ul>
      {canWrite && removing ? <div role="group" aria-label="Remove cleaner"><p>Remove {removing.display_name} from your team? Past work stays in the history. Future unlocked jobs will need rescheduling; locked or already-started work will be flagged for review.</p>
        <button type="button" disabled={busy} onClick={() => setRemoving(null)}>Keep cleaner</button>
        <button type="button" disabled={busy} onClick={() => void act(async () => {
          const result = await api<{released: number; needs_review: number}>(`/cleaners/${removing.id}/remove`, {method: "POST", headers: {"Idempotency-Key": crypto.randomUUID()}, body: JSON.stringify({expected_version: removing.version})});
          setItems((await api<{items: Cleaner[]}>("/cleaners")).items);
          if(cleaner === removing.id) {setCleaner(""); setWorkday(null);}
          setRemoving(null);
          setNotice(`Cleaner removed. ${result.released} assignments need rescheduling; ${result.needs_review} need manual review. Past work is preserved.`);
        })}>Confirm removal</button>
      </div> : null}
      {!liveReadOnly ? <p className="cleaner-roster-note">Newly added cleaners need consent recorded before mock messages can be dispatched.</p> : null}
    </section></main>;
}
