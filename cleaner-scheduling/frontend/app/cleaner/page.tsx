"use client";

import Image from "next/image";
import { useSyncExternalStore } from "react";
import { CleanerPortal } from "../../components/CleanerPortal";
import { CleanerLanguageProvider, LanguageSelector, useCleanerLanguage } from "../../lib/cleaner-language";

const subscribe = (changed: () => void) => {
  window.addEventListener("hashchange", changed);
  return () => window.removeEventListener("hashchange", changed);
};
const tokenSnapshot = () => window.location.hash.slice(1);
const serverSnapshot = () => "";
const copy = {
  en: ["YOUR CLEANING DAY", "Your houses, in one place.", "Confirm your work, mark it done, or find someone to cover.", "Open your personal link", "Use the link in your assignment email. If it has expired, ask your manager for a new one.", "Your personal cleaner link · Keep it private"],
  es: ["TU DÍA DE LIMPIEZA", "Tus casas, en un solo lugar.", "Confirma tus tareas, márcalas como terminadas o busca un reemplazo.", "Abre tu enlace personal", "Usa el enlace del correo con tus tareas. Si ha caducado, pide uno nuevo a tu responsable.", "Tu enlace personal de limpieza · Mantenlo privado"],
  pt: ["SEU DIA DE LIMPEZA", "Suas casas, em um só lugar.", "Confirme suas tarefas, marque como concluídas ou encontre um substituto.", "Abra seu link pessoal", "Use o link do e-mail com suas tarefas. Se ele expirou, peça um novo ao seu gerente.", "Seu link pessoal de limpeza · Mantenha-o privado"]
};
function CleanerPageContent() {
  const token = useSyncExternalStore(subscribe, tokenSnapshot, serverSnapshot);
  const {language} = useCleanerLanguage();
  const text = copy[language];
  const valid = /^(?:[0-9a-f]{32}\.)?[0-9a-f]{32}\.[0-9a-f]{64}$/.test(token);
  return <main className="cleaner-portal"><header className="cleaner-portal-heading"><div className="onboarding-wordmark"><Image src="/brand/turnover-mark.svg" alt="" width={32} height={32} aria-hidden="true" />Turnover</div><p className="eyebrow">{text[0]}</p><h1>{text[1]}</h1><p>{text[2]}</p></header>
    {valid ? <CleanerPortal key={token} token={token} /> : <><LanguageSelector /><section className="cleaner-portal-message"><h2>{text[3]}</h2><p>{text[4]}</p></section></>}
    <p className="cleaner-portal-privacy">{text[5]}</p>
  </main>;
}
export default function CleanerPage() {
  return <CleanerLanguageProvider><CleanerPageContent /></CleanerLanguageProvider>;
}
