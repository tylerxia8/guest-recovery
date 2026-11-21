export const readingLanguages = [
  ["en", "English"],
  ["es", "Español"],
  ["pt", "Português"],
] as const;

export function ReadingLanguages({selected, disabled, legend, language = "en"}: {selected: readonly string[]; disabled: boolean; legend: string; language?: "en" | "es" | "pt"}) {
  const labels = {
    en: {optional: "Optional", hint: "Select all that apply. Leave blank if not known."},
    es: {optional: "Opcional", hint: "Selecciona todos los que correspondan. Déjalo en blanco si no lo sabes."},
    pt: {optional: "Opcional", hint: "Selecione todos os que se aplicam. Deixe em branco se não souber."},
  }[language];
  return <fieldset className="reading-languages" disabled={disabled}><legend>{legend} <span className="field-optional">{labels.optional}</span></legend><div className="reading-language-options">{readingLanguages.map(([code, name]) => <label key={code}><input type="checkbox" name="reading_languages" value={code} defaultChecked={selected.includes(code)} /><span lang={code}>{name}</span></label>)}</div><small>{labels.hint}</small></fieldset>;
}

export function PreferredLanguage({selected, disabled}: {selected?: string | null; disabled: boolean}) {
  return <label>Preferred email language<select name="preferred_language" defaultValue={selected ?? ""} disabled={disabled}><option value="">Automatic from reading languages</option>{readingLanguages.map(([code, name]) => <option key={code} value={code} lang={code}>{name}</option>)}</select></label>;
}
