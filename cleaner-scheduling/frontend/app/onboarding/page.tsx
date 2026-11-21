"use client";

import Image from "next/image";
import { useSyncExternalStore } from "react";
import { WeeklyHours } from "../../components/WeeklyHours";
import { CleanerLanguageProvider, LanguageSelector, useCleanerLanguage } from "../../lib/cleaner-language";

const subscribe = (onChange: () => void) => {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
};
const getToken = () => window.location.hash.slice(1);
const serverToken = () => "";

const copy = {
  en: {eyebrow: "CLEANER SETUP", title: "Let’s get you set up", intro: "Just your availability and starting address.", expired: "Open the original onboarding link your manager shared. If it has expired, ask for a new link.", privacy: "Your personal setup link · Keep this link private"},
  es: {eyebrow: "CONFIGURACIÓN DEL PERSONAL DE LIMPIEZA", title: "Vamos a configurarlo todo", intro: "Solo necesitamos tu disponibilidad y dirección de inicio.", expired: "Abre el enlace original que te envió tu responsable. Si ha caducado, pide uno nuevo.", privacy: "Tu enlace personal de configuración · No compartas este enlace"},
  pt: {eyebrow: "CONFIGURAÇÃO DO PROFISSIONAL DE LIMPEZA", title: "Vamos preparar tudo", intro: "Só precisamos da sua disponibilidade e do endereço de partida.", expired: "Abra o link original enviado pelo seu responsável. Se ele expirou, peça um novo.", privacy: "Seu link pessoal de configuração · Mantenha este link privado"},
};

export default function CleanerOnboarding() {
  return <CleanerLanguageProvider><OnboardingContent /></CleanerLanguageProvider>;
}

function OnboardingContent() {
  const token = useSyncExternalStore(subscribe, getToken, serverToken);
  const {language} = useCleanerLanguage();
  const text = copy[language];
  return <main className="cleaner-onboarding" lang={language}><LanguageSelector /><header><div className="onboarding-wordmark"><Image src="/brand/turnover-mark.svg" alt="" width={32} height={32} aria-hidden="true" />Turnover</div><p className="eyebrow">{text.eyebrow}</p><h1>{text.title}</h1><p>{text.intro}</p><Image src="/illustrations/G10-onboarding-welcome-light.svg" width={280} height={180} alt="" aria-hidden="true" className="onboarding-welcome-illustration" /></header>
    {/^(?:[0-9a-f]{32}\.)?[0-9a-f]{32}\.[0-9a-f]{64}$/.test(token) ? <WeeklyHours key={token} onboardingToken={token} /> : <p role="status">{text.expired}</p>}
    <p className="onboarding-privacy">{text.privacy}</p>
  </main>;
}
