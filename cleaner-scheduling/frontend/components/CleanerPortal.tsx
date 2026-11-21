"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { cleanerLocales, LanguageSelector, useCleanerLanguage, type CleanerLanguage } from "../lib/cleaner-language";
import { EmptyState } from "./EmptyState";

type Job = {assignment_id: string; version: number; property_name: string; address: string; service_date: string; planned_start: string; planned_end: string; deadline_at: string; duration_minutes: number; status: string; can_accept: boolean; can_complete: boolean; can_punt: boolean};
type Offer = {offer_id: string; version: number; property_name: string; address: string; service_date: string; release_at: string; deadline_at: string; duration_minutes: number; expires_at: string; can_claim: boolean};
type Availability = {service_date: string; status: "pending" | "available" | "unavailable"; version: number; can_change: boolean};
type AvailabilityChange = {action: "set_availability"; service_date: string; available: boolean; expected_version: number};
type Snapshot = {availability?: Availability; cleaner_name: string; jobs: Job[]; offers: Offer[]; preferred_language: CleanerLanguage | null; reading_languages: string[]; cleaner_version: number};
type PortalError = Error & {status?: number};
type Action = "accept" | "complete" | "punt" | "claim";
type Change = {action: Action; assignment_id?: string; offer_id?: string; expected_version: number};
const day = (value: string, language: CleanerLanguage) => new Intl.DateTimeFormat(cleanerLocales[language], {timeZone: "America/New_York", weekday: "short", month: "short", day: "numeric"}).format(new Date(`${value}T12:00:00Z`));
const time = (value: string, language: CleanerLanguage) => new Intl.DateTimeFormat(cleanerLocales[language], {timeZone: "America/New_York", hour: "numeric", minute: "2-digit"}).format(new Date(value));
const when = (value: string, language: CleanerLanguage) => new Intl.DateTimeFormat(cleanerLocales[language], {timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}).format(new Date(value));
const duration = (minutes: number) => [Math.floor(minutes / 60) ? `${Math.floor(minutes / 60)} h` : "", minutes % 60 ? `${minutes % 60} min` : ""].filter(Boolean).join(" ");

const copy = {
  "en": {
    "Your response could not be saved. Check your working hours or contact your manager.": "Your response could not be saved. Check your working hours or contact your manager.",
    "Do you want to work tomorrow?": "Do you want to work tomorrow?",
    "Choose yes each day to be considered for assignments. Respond before 8pm Eastern for the initial schedule. Your saved hours do not opt you in.": "Choose yes each day to be considered for assignments. Respond before 8pm Eastern for the initial schedule. Your saved hours do not opt you in.",
    "You haven’t opted in for tomorrow.": "You haven’t opted in for tomorrow.",
    "You’re opted in for tomorrow.": "You’re opted in for tomorrow.",
    "You’re unavailable tomorrow.": "You’re unavailable tomorrow.",
    "I want to work tomorrow": "I want to work tomorrow",
    "Not available": "Not available",
    "Your choice has been saved.": "Your choice has been saved.",
    "Your choice changed. Refresh and try again. If you need help, contact your manager.": "Your choice changed. Refresh and try again. If you need help, contact your manager.",
    "Your response is not confirmed. Retry the same response below.": "Your response is not confirmed. Retry the same response below.",
    "Retry the same response": "Retry the same response",
    "You already have assignments for this date. Contact your manager if you cannot work.": "You already have assignments for this date. Contact your manager if you cannot work.",
    "Your schedule": "Your schedule",
    "All times Eastern": "All times Eastern",
    "Refresh": "Refresh",
    "Retry": "Retry",
    "Saving your update…": "Saving your update…",
    "Loading your houses…": "Loading your houses…",
    "If your link has expired or was replaced, ask your manager for a new one.": "If your link has expired or was replaced, ask your manager for a new one.",
    "Your houses": "Your houses",
    "to clean": "to clean",
    "You’re all set for now": "You’re all set for now",
    "New assignments will appear here.": "New assignments will appear here.",
    "Done": "Done",
    "Accepted": "Accepted",
    "Please confirm": "Please confirm",
    "cleaning": "cleaning",
    "Ready by": "Ready by",
    "Accept assignment": "Accept assignment",
    "Mark done": "Mark done",
    "Cannot clean this house": "Cannot clean this house",
    "Release this house?": "Release this house?",
    "Other cleaners have two hours to take it before your manager is alerted.": "Other cleaners have two hours to take it before your manager is alerted.",
    "Release house": "Release house",
    "Keep assignment": "Keep assignment",
    "Available houses": "Available houses",
    "available": "available",
    "No houses need cover right now.": "No houses need cover right now.",
    "Needs cover": "Needs cover",
    "Available after": "Available after",
    "Respond by": "Respond by",
    "Take this house": "Take this house",
    "Assignment accepted.": "Assignment accepted.",
    "House marked done. Thank you!": "House marked done. Thank you!",
    "House released. Other cleaners can now take it.": "House released. Other cleaners can now take it.",
    "House added to your schedule.": "House added to your schedule.",
    "Your houses could not be loaded. Try refreshing.": "Your houses could not be loaded. Try refreshing.",
    "The result could not be confirmed. Refresh before trying again.": "The result could not be confirmed. Refresh before trying again.",
    "This link is unavailable. Ask your manager for a new one.": "This link is unavailable. Ask your manager for a new one.",
    "This house has changed. Refresh and try again. If it still fails, contact your manager.": "This house has changed. Refresh and try again. If it still fails, contact your manager.",
    "Your language could not be saved for emails. Try again.": "Your language could not be saved for emails. Try again."
  },
  "es": {
    "Your response could not be saved. Check your working hours or contact your manager.": "No se pudo guardar tu respuesta. Revisa tus horarios de trabajo o contacta a tu responsable.",
    "Do you want to work tomorrow?": "¿Quieres trabajar mañana?",
    "Choose yes each day to be considered for assignments. Respond before 8pm Eastern for the initial schedule. Your saved hours do not opt you in.": "Elige sí cada día para que se te puedan asignar tareas. Responde antes de las 8 p. m., hora del Este, para el horario inicial. Tus horarios guardados no confirman tu disponibilidad.",
    "You haven’t opted in for tomorrow.": "Aún no has confirmado que quieres trabajar mañana.",
    "You’re opted in for tomorrow.": "Has confirmado que quieres trabajar mañana.",
    "You’re unavailable tomorrow.": "No estás disponible mañana.",
    "I want to work tomorrow": "Quiero trabajar mañana",
    "Not available": "No estoy disponible",
    "Your choice has been saved.": "Tu respuesta se ha guardado.",
    "Your choice changed. Refresh and try again. If you need help, contact your manager.": "Tu respuesta ha cambiado. Actualiza e inténtalo de nuevo. Si necesitas ayuda, contacta a tu responsable.",
    "Your response is not confirmed. Retry the same response below.": "No se ha confirmado tu respuesta. Vuelve a enviar la misma respuesta abajo.",
    "Retry the same response": "Reenviar la misma respuesta",
    "You already have assignments for this date. Contact your manager if you cannot work.": "Ya tienes tareas para esta fecha. Contacta a tu responsable si no puedes trabajar.",
    "Your schedule": "Tu horario",
    "All times Eastern": "Todas las horas: hora del Este de EE. UU.",
    "Refresh": "Actualizar",
    "Retry": "Reintentar",
    "Saving your update…": "Guardando tus cambios…",
    "Loading your houses…": "Cargando tus casas…",
    "If your link has expired or was replaced, ask your manager for a new one.": "Si tu enlace ha caducado o fue reemplazado, pide uno nuevo a tu responsable.",
    "Your houses": "Tus casas",
    "to clean": "por limpiar",
    "You’re all set for now": "Todo listo por ahora",
    "New assignments will appear here.": "Las nuevas tareas aparecerán aquí.",
    "Done": "Terminada",
    "Accepted": "Aceptada",
    "Please confirm": "Confirma, por favor",
    "cleaning": "de limpieza",
    "Ready by": "Lista antes de",
    "Accept assignment": "Aceptar tarea",
    "Mark done": "Marcar como terminada",
    "Cannot clean this house": "No puedo limpiar esta casa",
    "Release this house?": "¿Dejar esta casa?",
    "Other cleaners have two hours to take it before your manager is alerted.": "Los demás tienen dos horas para aceptar la casa antes de que se avise a tu responsable.",
    "Release house": "Dejar la casa",
    "Keep assignment": "Mantener tarea",
    "Available houses": "Casas disponibles",
    "available": "disponibles",
    "No houses need cover right now.": "No hay casas que necesiten reemplazo ahora.",
    "Needs cover": "Necesita reemplazo",
    "Available after": "Disponible a partir de",
    "Respond by": "Responde antes de",
    "Take this house": "Aceptar esta casa",
    "Assignment accepted.": "Tarea aceptada.",
    "House marked done. Thank you!": "Casa marcada como terminada. ¡Gracias!",
    "House released. Other cleaners can now take it.": "Casa liberada. Los demás ya pueden aceptarla.",
    "House added to your schedule.": "Casa añadida a tu horario.",
    "Your houses could not be loaded. Try refreshing.": "No se pudieron cargar tus casas. Actualiza la página.",
    "The result could not be confirmed. Refresh before trying again.": "No se pudo confirmar el resultado. Actualiza antes de volver a intentarlo.",
    "This link is unavailable. Ask your manager for a new one.": "Este enlace no está disponible. Pide uno nuevo a tu responsable.",
    "This house has changed. Refresh and try again. If it still fails, contact your manager.": "Esta casa ha cambiado. Actualiza e inténtalo de nuevo. Si el problema continúa, contacta a tu responsable.",
    "Your language could not be saved for emails. Try again.": "No se pudo guardar tu idioma para los correos. Inténtalo de nuevo."
  },
  "pt": {
    "Your response could not be saved. Check your working hours or contact your manager.": "Não foi possível salvar sua resposta. Confira seus horários de trabalho ou fale com seu gerente.",
    "Do you want to work tomorrow?": "Você quer trabalhar amanhã?",
    "Choose yes each day to be considered for assignments. Respond before 8pm Eastern for the initial schedule. Your saved hours do not opt you in.": "Escolha sim a cada dia para poder receber tarefas. Responda antes das 20h, horário do leste dos EUA, para a agenda inicial. Seus horários salvos não confirmam sua disponibilidade.",
    "You haven’t opted in for tomorrow.": "Você ainda não confirmou que quer trabalhar amanhã.",
    "You’re opted in for tomorrow.": "Você confirmou que quer trabalhar amanhã.",
    "You’re unavailable tomorrow.": "Você não está disponível amanhã.",
    "I want to work tomorrow": "Quero trabalhar amanhã",
    "Not available": "Não estou disponível",
    "Your choice has been saved.": "Sua resposta foi salva.",
    "Your choice changed. Refresh and try again. If you need help, contact your manager.": "Sua resposta mudou. Atualize e tente novamente. Se precisar de ajuda, fale com seu gerente.",
    "Your response is not confirmed. Retry the same response below.": "Sua resposta não foi confirmada. Envie a mesma resposta novamente abaixo.",
    "Retry the same response": "Reenviar a mesma resposta",
    "You already have assignments for this date. Contact your manager if you cannot work.": "Você já tem tarefas para esta data. Fale com seu gerente se não puder trabalhar.",
    "Your schedule": "Sua agenda",
    "All times Eastern": "Todos os horários: horário do leste dos EUA",
    "Refresh": "Atualizar",
    "Retry": "Tentar novamente",
    "Saving your update…": "Salvando suas alterações…",
    "Loading your houses…": "Carregando suas casas…",
    "If your link has expired or was replaced, ask your manager for a new one.": "Se seu link expirou ou foi substituído, peça um novo ao seu gerente.",
    "Your houses": "Suas casas",
    "to clean": "para limpar",
    "You’re all set for now": "Tudo em dia por enquanto",
    "New assignments will appear here.": "Novas tarefas aparecerão aqui.",
    "Done": "Concluída",
    "Accepted": "Aceita",
    "Please confirm": "Confirme, por favor",
    "cleaning": "de limpeza",
    "Ready by": "Pronta até",
    "Accept assignment": "Aceitar tarefa",
    "Mark done": "Marcar como concluída",
    "Cannot clean this house": "Não posso limpar esta casa",
    "Release this house?": "Liberar esta casa?",
    "Other cleaners have two hours to take it before your manager is alerted.": "Os outros profissionais têm duas horas para aceitar a casa antes de seu gerente ser avisado.",
    "Release house": "Liberar casa",
    "Keep assignment": "Manter tarefa",
    "Available houses": "Casas disponíveis",
    "available": "disponíveis",
    "No houses need cover right now.": "Nenhuma casa precisa de substituto agora.",
    "Needs cover": "Precisa de substituto",
    "Available after": "Disponível a partir de",
    "Respond by": "Responda até",
    "Take this house": "Aceitar esta casa",
    "Assignment accepted.": "Tarefa aceita.",
    "House marked done. Thank you!": "Casa marcada como concluída. Obrigado!",
    "House released. Other cleaners can now take it.": "Casa liberada. Outros profissionais já podem aceitá-la.",
    "House added to your schedule.": "Casa adicionada à sua agenda.",
    "Your houses could not be loaded. Try refreshing.": "Não foi possível carregar suas casas. Atualize a página.",
    "The result could not be confirmed. Refresh before trying again.": "Não foi possível confirmar o resultado. Atualize antes de tentar novamente.",
    "This link is unavailable. Ask your manager for a new one.": "Este link não está disponível. Peça um novo ao seu gerente.",
    "This house has changed. Refresh and try again. If it still fails, contact your manager.": "Esta casa foi alterada. Atualize e tente novamente. Se o problema continuar, fale com seu gerente.",
    "Your language could not be saved for emails. Try again.": "Não foi possível salvar seu idioma para os e-mails. Tente novamente."
  }
} as const;

async function request<T>(token: string, init: RequestInit = {}, path = ""): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  if (init.body) headers.set("Content-Type", "application/json");
  const response = await fetch(`/api/cleaner-portal${path}`, {...init, headers, credentials: "omit", cache: "no-store", signal: AbortSignal.timeout(init.method === "POST" ? 75000 : 15000)});
  let body: T;
  try {body = await response.json();}
  catch {throw Object.assign(new Error("unavailable"), {status: response.status});}
  if (!response.ok) throw Object.assign(new Error("request_failed"), {status: response.status});
  return body;
}

export function CleanerPortal({token}: {token: string}) {
  const {language, initializeLanguage} = useCleanerLanguage();
  const text = copy[language];
  const [savingLanguage, setSavingLanguage] = useState(false);
  const [languageError, setLanguageError] = useState(false);
  const [data, setData] = useState<Snapshot | null>(null);
  const [error, setError] = useState<number | null>(null);
  const [notice, setNotice] = useState<Action | "">("");
  const [changeError, setChangeError] = useState<number | null>(null);
  const [availabilityPending, setAvailabilityPending] = useState<{key: string; payload: AvailabilityChange} | null>(null);
  const [availabilityError, setAvailabilityError] = useState<number | null>(null);
  const [availabilitySaved, setAvailabilitySaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [confirming, setConfirming] = useState<string | null>(null);
  const sequence = useRef(0);
  const active = useRef(true);
  const writing = useRef(false);
  const keys = useRef(new Map<string, string>());
  const load = useCallback(async () => {
    if (writing.current) return;
    const current = ++sequence.current;
    try {
      let snapshot: Snapshot | null = null;
      for (let attempt = 0; attempt < 3; attempt++) {
        try {snapshot = await request<Snapshot>(token); break;}
        catch (reason) {
          const failure = reason as PortalError;
          if ((failure.status && failure.status < 500 && failure.status !== 429) || attempt === 2) throw reason;
          await new Promise(resolve => setTimeout(resolve, 500 * 2 ** attempt));
          if (!active.current || sequence.current !== current) return;
        }
      }
      if (active.current && sequence.current === current && snapshot) {setData(snapshot); setError(null);
        const preferred = snapshot.preferred_language ?? (["en", "es", "pt"].find(value => snapshot.reading_languages.includes(value)) as CleanerLanguage | undefined);
        if (preferred) initializeLanguage(preferred);
      }
    } catch (reason) {
      if (active.current && sequence.current === current) {
        const failure = reason as PortalError;
        if (failure.status === 401 || failure.status === 403) setData(null);
        setError(failure.status ?? 0);
      }
    } finally {
      if (active.current && sequence.current === current) setLoading(false);
    }
  }, [token, initializeLanguage]);
  useEffect(() => {
    active.current = true;
    const initial = setTimeout(() => {void load();}, 0);
    const timer = setInterval(() => {void load();}, 60000);
    return () => {active.current = false; clearTimeout(initial); clearInterval(timer);};
  }, [load]);
  async function change(payload: Change) {
    if (writing.current) return;
    writing.current = true;
    sequence.current++;
    setBusy(true);
    setNotice("");
    setChangeError(null);
    const identity = `${payload.action}:${payload.assignment_id ?? payload.offer_id}:${payload.expected_version}`;
    let key = keys.current.get(identity);
    if (!key) {key = crypto.randomUUID(); keys.current.set(identity, key);}
    try {
      await request(token, {method: "POST", headers: {"Idempotency-Key": key}, body: JSON.stringify(payload)});
      if (active.current) {
        setConfirming(null);
        setNotice(payload.action);
      }
    } catch (reason) {
      if (active.current) {
        const failure = reason as PortalError;
        if (failure.status === 401 || failure.status === 403) setData(null);
        setChangeError(failure.status ?? 0);
      }
    } finally {
      writing.current = false;
      if (active.current) {setBusy(false); await load();}
    }
  }
  async function saveAvailability(available: boolean) {
    if (!data?.availability || writing.current) return;
    const change = availabilityPending ?? {key: crypto.randomUUID(), payload: {action: "set_availability" as const, service_date: data.availability.service_date, available, expected_version: data.availability.version}};
    writing.current = true;
    sequence.current++;
    setAvailabilityPending(change);
    setBusy(true);
    setAvailabilityError(null);
    setAvailabilitySaved(false);
    try {
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          await request(token, {method: "POST", headers: {"Idempotency-Key": change.key}, body: JSON.stringify(change.payload)});
          if (active.current) {setAvailabilityPending(null); setAvailabilitySaved(true);}
          break;
        } catch (reason) {
          const failure = reason as PortalError;
          if ((failure.status && failure.status < 500 && failure.status !== 429 && failure.status !== 408) || attempt === 2) throw reason;
          await new Promise(resolve => setTimeout(resolve, 500 * 2 ** attempt));
          if (!active.current) return;
        }
      }
    } catch (reason) {
      if (active.current) {
        const failure = reason as PortalError;
        if (failure.status && failure.status < 500 && ![408, 429].includes(failure.status)) setAvailabilityPending(null);
        if (failure.status === 401 || failure.status === 403) setData(null);
        setAvailabilityError(failure.status ?? 0);
      }
    } finally {
      writing.current = false;
      if (active.current) {setBusy(false); await load();}
    }
  }
  async function saveLanguage(next: CleanerLanguage) {
    if (!data || writing.current) return;
    writing.current = true;
    sequence.current++;
    setSavingLanguage(true);
    setLanguageError(false);
    try {
      const identity = `language:${next}:${data.cleaner_version}`;
      let key = keys.current.get(identity);
      if (!key) {key = crypto.randomUUID(); keys.current.set(identity, key);}
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          const updated = await request<Pick<Snapshot, "preferred_language" | "reading_languages" | "cleaner_version">>(token, {method: "POST", headers: {"Idempotency-Key": key}, body: JSON.stringify({preferred_language: next, expected_cleaner_version: data.cleaner_version})}, "/language");
          if (active.current) setData(current => current ? {...current, ...updated} : current);
          break;
        } catch (reason) {
          const failure = reason as PortalError;
          if ((failure.status && failure.status < 500 && failure.status !== 429) || attempt === 2) throw reason;
          await new Promise(resolve => setTimeout(resolve, 500 * 2 ** attempt));
          if (!active.current) return;
        }
      }
    } catch {
      if (active.current) setLanguageError(true);
    } finally {
      writing.current = false;
      if (active.current) {setSavingLanguage(false); await load();}
    }
  }
  const failureText = (status: number, fallback: keyof typeof copy.en) => text[status === 401 || status === 403 ? "This link is unavailable. Ask your manager for a new one." : status === 409 ? "This house has changed. Refresh and try again. If it still fails, contact your manager." : fallback];
  return <>
    <LanguageSelector disabled={busy || savingLanguage} onChange={next => void saveLanguage(next)} />
    {languageError ? <p role="alert" className="cleaner-portal-alert">{text["Your language could not be saved for emails. Try again."]} <button type="button" disabled={busy || savingLanguage} onClick={() => void saveLanguage(language)}>{text["Retry"]}</button></p> : null}
    <div className="cleaner-portal-toolbar"><div><strong>{data?.cleaner_name ?? text["Your schedule"]}</strong><span>{text["All times Eastern"]}</span></div><button type="button" disabled={busy || savingLanguage || loading} onClick={() => void load()}>{text["Refresh"]}</button></div>
    {busy ? <p role="status" className="cleaner-portal-notice">{text["Saving your update\u2026"]}</p> : null}
    {loading ? <p role="status" className="cleaner-portal-message">{text["Loading your houses\u2026"]}</p> : null}
    {error !== null ? <p role="alert" className="cleaner-portal-alert">{failureText(error, "Your houses could not be loaded. Try refreshing.")}</p> : null}
    {changeError !== null ? <p role="alert" className="cleaner-portal-alert">{failureText(changeError, "The result could not be confirmed. Refresh before trying again.")}</p> : null}
    {notice ? <p role="status" className="cleaner-portal-notice">{text[{accept: "Assignment accepted.", complete: "House marked done. Thank you!", punt: "House released. Other cleaners can now take it.", claim: "House added to your schedule."}[notice] as keyof typeof copy.en]}</p> : null}
    {!loading && !data ? <p className="cleaner-portal-recovery">{text["If your link has expired or was replaced, ask your manager for a new one."]}</p> : null}
    {data?.availability ? <section className="cleaner-job-card" aria-labelledby="tomorrow-availability-title">
      <div className="cleaner-job-top"><span>{day(data.availability.service_date, language)}</span></div>
      <h2 id="tomorrow-availability-title">{text["Do you want to work tomorrow?"]}</h2>
      <p>{text["Choose yes each day to be considered for assignments. Respond before 8pm Eastern for the initial schedule. Your saved hours do not opt you in."]}</p>
      <p role="status">{text[data.availability.status === "available" ? "You’re opted in for tomorrow." : data.availability.status === "unavailable" ? "You’re unavailable tomorrow." : "You haven’t opted in for tomorrow."]}</p>
      {availabilitySaved ? <p role="status" className="cleaner-portal-notice">{text["Your choice has been saved."]}</p> : null}
      {availabilityError !== null ? <p role="alert" className="cleaner-portal-alert">{text[availabilityError === 401 || availabilityError === 403 ? "This link is unavailable. Ask your manager for a new one." : availabilityError === 409 ? "Your choice changed. Refresh and try again. If you need help, contact your manager." : availabilityPending ? "Your response is not confirmed. Retry the same response below." : "Your response could not be saved. Check your working hours or contact your manager."]}</p> : null}
      {availabilityPending ? <button type="button" disabled={busy || savingLanguage} onClick={() => void saveAvailability(availabilityPending.payload.available)}>{text["Retry the same response"]} · {day(availabilityPending.payload.service_date, language)}</button> : data.availability.can_change ? <div className="cleaner-job-actions">
        <button type="button" disabled={busy || savingLanguage || data.availability.status === "available"} onClick={() => void saveAvailability(true)}>{text["I want to work tomorrow"]}</button>
        <button type="button" className="cleaner-portal-secondary" disabled={busy || savingLanguage || data.availability.status === "unavailable"} onClick={() => void saveAvailability(false)}>{text["Not available"]}</button>
      </div> : <p>{text["You already have assignments for this date. Contact your manager if you cannot work."]}</p>}
    </section> : null}
    {data ? <><div className="cleaner-portal-section-heading"><h2>{text["Your houses"]}</h2><span>{data.jobs.filter(job => job.status !== "completed").length} {text["to clean"]}</span></div>
      {!data.jobs.length ? <EmptyState variant="portal-no-jobs" title={text["You\u2019re all set for now"]} description={text["New assignments will appear here."]} /> : null}
      <div className="cleaner-portal-jobs">{data.jobs.map(job => <section className={`cleaner-job-card${job.status === "completed" ? " is-completed" : ""}`} key={job.assignment_id}>
        <div className="cleaner-job-top"><span>{day(job.service_date, language)}</span><span className="badge">{job.status === "completed" ? text["Done"] : job.status === "acknowledged" ? text["Accepted"] : text["Please confirm"]}</span></div><h3>{job.property_name}</h3><p className="cleaner-job-address">{job.address}</p>
        <div className="cleaner-job-window"><strong>{time(job.planned_start, language)}–{time(job.planned_end, language)}</strong><span>{duration(job.duration_minutes)} {text["cleaning"]}</span></div><p className="cleaner-job-deadline">{text["Ready by"]} {when(job.deadline_at, language)}</p>
        <div className="cleaner-job-actions">{job.can_accept ? <button type="button" disabled={busy || savingLanguage} onClick={() => void change({action: "accept", assignment_id: job.assignment_id, expected_version: job.version})}>{text["Accept assignment"]}</button> : null}{job.status !== "completed" ? <button className={job.can_accept ? "cleaner-portal-secondary" : ""} type="button" disabled={busy || savingLanguage || !job.can_complete} onClick={() => void change({action: "complete", assignment_id: job.assignment_id, expected_version: job.version})}>{text["Mark done"]}</button> : null}{job.can_punt ? <button className="cleaner-portal-text-button" type="button" disabled={busy || savingLanguage} aria-expanded={confirming === job.assignment_id} onClick={() => setConfirming(confirming === job.assignment_id ? null : job.assignment_id)}>{text["Cannot clean this house"]}</button> : null}</div>
        {confirming === job.assignment_id ? <div className="cleaner-punt-confirmation" role="group" aria-label={`${text["Release house"]}: ${job.property_name}`}><strong>{text["Release this house?"]}</strong><p>{text["Other cleaners have two hours to take it before your manager is alerted."]}</p><div><button type="button" disabled={busy || savingLanguage} onClick={() => void change({action: "punt", assignment_id: job.assignment_id, expected_version: job.version})}>{text["Release house"]}</button><button type="button" className="cleaner-portal-secondary" disabled={busy || savingLanguage} onClick={() => setConfirming(null)}>{text["Keep assignment"]}</button></div></div> : null}
      </section>)}</div>
      <div className="cleaner-portal-section-heading"><h2>{text["Available houses"]}</h2><span>{data.offers.length} {text["available"]}</span></div>
      {!data.offers.length ? <p className="cleaner-portal-quiet">{text["No houses need cover right now."]}</p> : null}
      <div className="cleaner-portal-jobs">{data.offers.map(offer => <section className="cleaner-job-card cleaner-offer-card" key={offer.offer_id}><div className="cleaner-job-top"><span>{day(offer.service_date, language)}</span><span className="badge">{text["Needs cover"]}</span></div><h3>{offer.property_name}</h3><p className="cleaner-job-address">{offer.address}</p><div className="cleaner-job-window"><strong>{duration(offer.duration_minutes)} {text["cleaning"]}</strong><span>{text["Ready by"]} {when(offer.deadline_at, language)}</span></div><p className="cleaner-job-deadline">{text["Available after"]} {when(offer.release_at, language)}</p><div className="cleaner-offer-footer"><small>{text["Respond by"]} {when(offer.expires_at, language)}</small><button type="button" disabled={busy || savingLanguage || !offer.can_claim} onClick={() => void change({action: "claim", offer_id: offer.offer_id, expected_version: offer.version})}>{text["Take this house"]}</button></div></section>)}</div>
    </> : null}
  </>;
}
