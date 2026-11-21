"use client";
import type { WorkspaceMode } from "../lib/session";

import Image from "next/image";
import { useEffect, useState, type FormEvent } from "react";
import { api } from "../lib/api";
import { ReadingLanguages } from "./ReadingLanguages";
import { useCleanerLanguage } from "../lib/cleaner-language";

type Day = {weekday: number; start_time: string; end_time: string; break_start?: string; break_end?: string; origin_label?: string};
type Week = {version: number; cleaner_version: number; reading_languages: string[]; preferred_language: "en" | "es" | "pt" | null; days: Day[]; starting_address?: string; route_status?: string | null; cleaner_name?: string; routing_mode?: string};
const translations: Record<string, {es: string; pt: string}> = {
  "Monday": {
    "es": "lunes",
    "pt": "segunda-feira"
  },
  "Tuesday": {
    "es": "martes",
    "pt": "terça-feira"
  },
  "Wednesday": {
    "es": "miércoles",
    "pt": "quarta-feira"
  },
  "Thursday": {
    "es": "jueves",
    "pt": "quinta-feira"
  },
  "Friday": {
    "es": "viernes",
    "pt": "sexta-feira"
  },
  "Saturday": {
    "es": "sábado",
    "pt": "sábado"
  },
  "Sunday": {
    "es": "domingo",
    "pt": "domingo"
  },
  "Weekly working hours": {
    "es": "Horario semanal de trabajo",
    "pt": "Horário semanal de trabalho"
  },
  "Welcome,": {
    "es": "Bienvenido/a,",
    "pt": "Boas-vindas,"
  },
  "When can you work?": {
    "es": "¿Cuándo puedes trabajar?",
    "pt": "Quando você pode trabalhar?"
  },
  "Regular weekly hours": {
    "es": "Horario semanal habitual",
    "pt": "Horário semanal habitual"
  },
  "Choose your days and usual hours. All times are Eastern.": {
    "es": "Elige tus días y horario habitual. Todas las horas son del este de EE. UU.",
    "pt": "Escolha seus dias e horários habituais. Todos os horários são do leste dos EUA."
  },
  "You’re all set": {
    "es": "Todo listo",
    "pt": "Tudo pronto"
  },
  "Edit my details": {
    "es": "Editar mis datos",
    "pt": "Editar meus dados"
  },
  "Loading weekly hours": {
    "es": "Cargando horario semanal",
    "pt": "Carregando horário semanal"
  },
  "Languages you can read": {
    "es": "Idiomas que puedes leer",
    "pt": "Idiomas que você consegue ler"
  },
  "Languages this cleaner can read": {
    "es": "Idiomas que puede leer este profesional",
    "pt": "Idiomas que este profissional consegue ler"
  },
  "Where do you start your day?": {
    "es": "¿Dónde empiezas tu jornada?",
    "pt": "Onde você começa seu dia?"
  },
  "Street address, city, state and ZIP": {
    "es": "Dirección, ciudad, estado y código postal",
    "pt": "Endereço, cidade, estado e CEP"
  },
  "Your address is encrypted. A free US Census address lookup locates your starting point; driving estimates are calculated locally without live traffic.": {
    "es": "Tu dirección está cifrada. Una consulta gratuita al Censo de EE. UU. ubica tu punto de partida; los tiempos de viaje se calculan localmente sin tráfico en tiempo real.",
    "pt": "Seu endereço é criptografado. Uma consulta gratuita ao Censo dos EUA localiza seu ponto de partida; os tempos de viagem são calculados localmente sem trânsito em tempo real."
  },
  "Enter where you usually start the working day. Your address is encrypted and only available for planning your work.": {
    "es": "Indica dónde sueles empezar tu jornada. Tu dirección está cifrada y solo se usa para planificar tu trabajo.",
    "pt": "Informe onde você costuma começar seu dia de trabalho. Seu endereço é criptografado e usado apenas para planejar seu trabalho."
  },
  "Saving address and hours…": {
    "es": "Guardando dirección y horario…",
    "pt": "Salvando endereço e horários…"
  },
  "Save my hours": {
    "es": "Guardar mis horarios",
    "pt": "Salvar meus horários"
  },
  "Save weekly hours": {
    "es": "Guardar horario semanal",
    "pt": "Salvar horário semanal"
  },
  "Your manager can see these details once saved. You can use this link again until it expires or is replaced.": {
    "es": "Tu responsable puede ver estos datos una vez guardados. Puedes volver a usar este enlace hasta que caduque o sea reemplazado.",
    "pt": "Seu responsável pode ver estes dados após serem salvos. Você pode usar este link novamente até que ele expire ou seja substituído."
  },
  "Working days": {
    "es": "Días de trabajo",
    "pt": "Dias de trabalho"
  },
  "Leave days off unchecked.": {
    "es": "No marques los días de descanso.",
    "pt": "Deixe os dias de folga desmarcados."
  },
  "Usual workday": {
    "es": "Jornada habitual",
    "pt": "Jornada habitual"
  },
  "Start": {
    "es": "Inicio",
    "pt": "Início"
  },
  "Finish": {
    "es": "Fin",
    "pt": "Fim"
  },
  "Add a break (optional)": {
    "es": "Añadir un descanso (opcional)",
    "pt": "Adicionar uma pausa (opcional)"
  },
  "Break starts": {
    "es": "Inicio del descanso",
    "pt": "Início da pausa"
  },
  "Break ends": {
    "es": "Fin del descanso",
    "pt": "Fim da pausa"
  },
  "Different hours on some days": {
    "es": "Horario diferente algunos días",
    "pt": "Horários diferentes em alguns dias"
  },
  "Break (optional)": {
    "es": "Descanso (opcional)",
    "pt": "Pausa (opcional)"
  },
  "No working days selected. Saving will mark you unavailable all week.": {
    "es": "No has seleccionado días de trabajo. Al guardar, no estarás disponible en toda la semana.",
    "pt": "Nenhum dia de trabalho selecionado. Ao salvar, você ficará indisponível durante toda a semana."
  },
  "Weekly hours could not be loaded. Reopen this page to retry. If your link expired, ask your manager for a new one.": {
    "es": "No se pudo cargar el horario. Vuelve a abrir esta página. Si tu enlace caducó, pide uno nuevo a tu responsable.",
    "pt": "Não foi possível carregar os horários. Abra esta página novamente. Se seu link expirou, peça um novo ao seu responsável."
  },
  "Your hours, breaks and starting address are saved. Your manager can review them now.": {
    "es": "Tu horario, descansos y dirección de inicio están guardados. Tu responsable ya puede revisarlos.",
    "pt": "Seus horários, pausas e endereço de partida foram salvos. Seu responsável já pode consultá-los."
  },
  "We could not confirm your save. Check hours and breaks. If someone else edited them, reopen this page to reload. If your link expired, ask your manager for a new one.": {
    "es": "No pudimos confirmar que se guardaron los cambios. Revisa el horario y los descansos. Si alguien los editó, vuelve a abrir esta página. Si el enlace caducó, pide uno nuevo a tu responsable.",
    "pt": "Não foi possível confirmar o salvamento. Confira os horários e as pausas. Se alguém os alterou, abra esta página novamente. Se o link expirou, peça um novo ao seu responsável."
  }
};
const weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

type WeeklyHoursProps = ({cleaner: string} | {onboardingToken: string}) & {sourceMode?: WorkspaceMode};

export function WeeklyHours(props: WeeklyHoursProps) {
  const {language, initializeLanguage} = useCleanerLanguage();
  const t = (text: string) => language === "en" ? text : translations[text]?.[language] ?? text;
  const selfService = "onboardingToken" in props;
  const liveReadOnly = selfService || props.sourceMode === "hospitable_read_only";
  const endpoint = "onboardingToken" in props ? "/cleaner-onboarding" : `/cleaners/${props.cleaner}/weekly-hours`;
  const bearer = "onboardingToken" in props ? props.onboardingToken : "";
  const [week, setWeek] = useState<Week | null>(null);
  const [error, setError] = useState(""); const [notice, setNotice] = useState(""); const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    api<Week>(endpoint, {headers: bearer ? {Authorization: `Bearer ${bearer}`} : {}}).then(row => {if(active) {
      setWeek(row);
      const preferred = row.preferred_language ?? (["en", "es", "pt"] as const).find(value => row.reading_languages.includes(value));
      if(selfService && preferred) initializeLanguage(preferred);
    }})
      .catch(() => {if(active) setError("load");});
    return () => {active = false;};
  }, [endpoint, bearer, selfService, initializeLanguage]);
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if(!week) return;
    if(!event.currentTarget.checkValidity()) {setError("save"); event.currentTarget.querySelector<HTMLElement>(":invalid")?.focus(); return;}
    const form = new FormData(event.currentTarget); setBusy(true); setError(""); setNotice("");
    const days = weekdays.flatMap((_, weekday) => form.has(`day-${weekday}`) ? [{
      weekday, start_time: String(form.get(form.has("custom-hours") ? `start-${weekday}` : "usual-start")), end_time: String(form.get(form.has("custom-hours") ? `end-${weekday}` : "usual-end")),
      break_start: form.get(form.has("custom-hours") ? `break-start-${weekday}` : "usual-break-start") || null, break_end: form.get(form.has("custom-hours") ? `break-end-${weekday}` : "usual-break-end") || null,
    }] : []);
    try {
      const result = await api<Week>(endpoint, {method: "POST", headers: {"Idempotency-Key": crypto.randomUUID(), ...(bearer ? {Authorization: `Bearer ${bearer}`} : {})}, body: JSON.stringify({expected_version: week.version, expected_cleaner_version: week.cleaner_version, reading_languages: form.getAll("reading_languages"), ...(selfService ? {preferred_language: language} : {}), days, starting_address: String(form.get("address")).trim()})});
      setWeek({...week, ...result, starting_address: String(form.get("address")).trim()});
      setNotice(selfService ? "Your hours, breaks and starting address are saved. Your manager can review them now." : result.route_status === "ready" ? "Weekly hours saved. Starting-address driving times are saved for future schedules." : "Weekly hours and address saved. Driving times need attention before work can be assigned from this address.");
    } catch {
      setError("save");
    } finally {setBusy(false);}
  }
  return <section className="weekly-hours" aria-label={t("Weekly working hours")}>{selfService && week?.cleaner_name ? <p className="eyebrow">{t("Welcome,")} {week.cleaner_name}</p> : null}<h2>{selfService ? t("When can you work?") : t("Regular weekly hours")}</h2>
    <p>{t("Choose your days and usual hours. All times are Eastern.")}</p>
    {error ? <p role="alert">{t(error === "load" ? "Weekly hours could not be loaded. Reopen this page to retry. If your link expired, ask your manager for a new one." : "We could not confirm your save. Check hours and breaks. If someone else edited them, reopen this page to reload. If your link expired, ask your manager for a new one.")}</p> : null}{notice ? <div className="weekly-saved" role="status">{selfService ? <><Image src="/illustrations/G12-success-light.svg" width={200} height={200} alt="" aria-hidden="true" className="weekly-saved-illustration" /><h3>{t("You’re all set")}</h3></> : null}<p>{t(notice)}</p>{selfService ? <button type="button" onClick={() => setNotice("")}>{t("Edit my details")}</button> : null}</div> : null}
    {!week && !error ? <p>{t("Loading weekly hours")}</p> : null}
    {week && (!selfService || !notice) ? <form noValidate method="post" onSubmit={event => void save(event)} key={week.version}>
      <ReadingLanguages selected={week.reading_languages} disabled={busy} language={language} legend={selfService ? t("Languages you can read") : t("Languages this cleaner can read")} />
      <WeeklyHoursFields week={week} busy={busy} />
      <label className="starting-address-field">{t("Where do you start your day?")}<input name="address" disabled={busy} required minLength={5} maxLength={300} autoComplete="street-address" defaultValue={week.starting_address ?? ""} placeholder={t("Street address, city, state and ZIP")} /></label>
      <p className="muted">{week.routing_mode === "local_osrm" ? t("Your address is encrypted. A free US Census address lookup locates your starting point; driving estimates are calculated locally without live traffic.") : selfService ? t("Enter where you usually start the working day. Your address is encrypted and only available for planning your work.") : liveReadOnly ? "Enter where this cleaner starts the day. The address is encrypted and stored only on the local server; no online lookup is made." : "Enter where this cleaner starts their day. In the local Charlotte demo, the address is looked up once to save driving estimates; unchanged addresses reuse saved times."}</p>
      {!selfService && week.route_status && week.route_status !== "ready" ? <p role="alert">{liveReadOnly ? "Address saved locally. Driving times await routing setup; no schedule can be previewed yet." : "Driving times are not ready. Check the full street address and save again. Addresses outside the Charlotte demo area and locations without verified routes cannot be scheduled."}</p> : null}
      <button className="weekly-save-button" disabled={busy}>{busy ? t("Saving address and hours…") : (selfService ? t("Save my hours") : "Save weekly hours")}</button>{!selfService ? <p><a href="/setup">{liveReadOnly ? "Review pilot setup" : "Continue to automation"}</a>{!liveReadOnly ? <> · <a href="/tomorrow">View tomorrow’s schedule</a></> : null}</p> : <p>{t("Your manager can see these details once saved. You can use this link again until it expires or is replaced.")}</p>}
    </form> : null}
  </section>;
}

function WeeklyHoursFields({week, busy}: {week: Week; busy: boolean}) {
  const {language} = useCleanerLanguage();
  const t = (text: string) => language === "en" ? text : translations[text]?.[language] ?? text;
  const first = week.days[0];
  const [selected, setSelected] = useState(week.days.map(day => day.weekday));
  const [custom, setCustom] = useState(week.days.some(day => day.start_time !== first?.start_time || day.end_time !== first?.end_time || (day.break_start ?? "") !== (first?.break_start ?? "") || (day.break_end ?? "") !== (first?.break_end ?? "")));
  return <div className="weekly-availability-fields">
    <fieldset className="weekly-days" disabled={busy}><legend>{t("Working days")}</legend><div>{weekdays.map((name, weekday) => <label key={name} title={t(name)} className={selected.includes(weekday) ? "is-selected" : ""}><input type="checkbox" name={`day-${weekday}`} aria-label={t(name)} checked={selected.includes(weekday)} onChange={event => setSelected(previous => event.target.checked ? [...previous, weekday] : previous.filter(day => day !== weekday))} /><span>{t(name).slice(0, 3)}</span></label>)}</div><p className="muted">{t("Leave days off unchecked.")}</p></fieldset>
    <fieldset className="weekly-usual-hours" disabled={busy || custom || !selected.length} hidden={custom} onChange={event => {
      if(!(event.target instanceof HTMLInputElement)) return;
      const input = event.target;
      for(let weekday = 0; weekday < 7; weekday++) {
        const target = input.form?.elements.namedItem(`${input.name.replace("usual-", "")}-${weekday}`);
        if(target instanceof HTMLInputElement) target.value = input.value;
      }
    }}><legend>{t("Usual workday")}</legend><div className="weekly-time-pair">
      <label>{t("Start")}<input type="time" name="usual-start" required defaultValue={first?.start_time} /></label>
      <label>{t("Finish")}<input type="time" name="usual-end" required defaultValue={first?.end_time} /></label>
    </div><details open={!!first?.break_start || !!first?.break_end}><summary>{t("Add a break (optional)")}</summary><div className="weekly-time-pair"><label>{t("Break starts")}<input type="time" name="usual-break-start" defaultValue={first?.break_start} /></label><label>{t("Break ends")}<input type="time" name="usual-break-end" defaultValue={first?.break_end} /></label></div></details></fieldset>
    <label className="weekly-custom-toggle"><input type="checkbox" name="custom-hours" checked={custom} disabled={busy} onChange={event => setCustom(event.target.checked)} />{t("Different hours on some days")}</label>
    <div className="weekly-custom-hours" hidden={!custom}>{weekdays.map((name, weekday) => {
      const day = week.days.find(day => day.weekday === weekday);
      return <fieldset key={name} disabled={busy || !custom || !selected.includes(weekday)} hidden={!selected.includes(weekday)}><legend>{t(name)}</legend><div className="weekly-time-pair">
        <label>{t("Start")}<input type="time" name={`start-${weekday}`} required defaultValue={day?.start_time ?? first?.start_time} /></label>
        <label>{t("Finish")}<input type="time" name={`end-${weekday}`} required defaultValue={day?.end_time ?? first?.end_time} /></label>
      </div><details open={!!day?.break_start || !!day?.break_end}><summary>{t("Break (optional)")}</summary><div className="weekly-time-pair"><label>{t("Break starts")}<input type="time" name={`break-start-${weekday}`} defaultValue={day ? day.break_start : first?.break_start} /></label><label>{t("Break ends")}<input type="time" name={`break-end-${weekday}`} defaultValue={day ? day.break_end : first?.break_end} /></label></div></details></fieldset>;
    })}</div>
    {!selected.length ? <p className="muted">{t("No working days selected. Saving will mark you unavailable all week.")}</p> : null}
  </div>;
}
