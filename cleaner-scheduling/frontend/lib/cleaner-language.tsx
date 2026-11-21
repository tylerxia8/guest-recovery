"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

export type CleanerLanguage = "en" | "es" | "pt";
export const cleanerLocales = {en: "en-US", es: "es", pt: "pt-BR"};
const Context = createContext<{language: CleanerLanguage; setLanguage: (language: CleanerLanguage) => void; initializeLanguage: (language: CleanerLanguage) => void}>({language: "en", setLanguage: () => {}, initializeLanguage: () => {}});
const supported = (value: string | null): value is CleanerLanguage => value === "en" || value === "es" || value === "pt";

export function CleanerLanguageProvider({children}: {children: ReactNode}) {
  const [language, updateLanguage] = useState<CleanerLanguage>("en");
  const chosen = useRef(false);
  const revision = useRef(0);
  useEffect(() => {
    const query = new URLSearchParams(window.location.search).get("lang");
    let stored: string | null = null;
    try {stored = localStorage.getItem("cleaner-language");} catch {}
    const browser = navigator.language.split("-")[0];
    const initial = supported(query) ? query : supported(stored) ? stored : supported(browser) ? browser : "en";
    if (supported(query) || supported(stored)) chosen.current = true;
    const current = revision.current;
    const timer = setTimeout(() => {if (revision.current === current) updateLanguage(initial);}, 0);
    return () => clearTimeout(timer);
  }, []);
  useEffect(() => {
    const previous = document.documentElement.lang;
    document.documentElement.lang = language;
    return () => {document.documentElement.lang = previous;};
  }, [language]);
  const setLanguage = useCallback((next: CleanerLanguage) => {
    chosen.current = true;
    revision.current++;
    updateLanguage(next);
    try {localStorage.setItem("cleaner-language", next);} catch {}
  }, []);
  const initializeLanguage = useCallback((next: CleanerLanguage) => {
    if (chosen.current) return;
    chosen.current = true;
    revision.current++;
    updateLanguage(next);
  }, []);
  return <Context.Provider value={{language, setLanguage, initializeLanguage}}><div lang={language}>{children}</div></Context.Provider>;
}

export const useCleanerLanguage = () => useContext(Context);

export function LanguageSelector({onChange, disabled}: {onChange?: (language: CleanerLanguage) => void; disabled?: boolean}) {
  const {language, setLanguage} = useCleanerLanguage();
  return <label className="cleaner-language-picker"><span><span lang="en">Language</span> / <span lang="es">Idioma</span> / <span lang="pt">Idioma</span></span><select value={language} disabled={disabled} onChange={event => {const next = event.target.value as CleanerLanguage; setLanguage(next); onChange?.(next);}}><option value="en" lang="en">English</option><option value="es" lang="es">Español</option><option value="pt" lang="pt">Português</option></select></label>;
}
