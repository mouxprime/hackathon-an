// i18n léger maison — calqué sur le pattern Zustand du projet (cf. lib/toast.tsx).
// Aucune dépendance externe : un store pour la langue, des dictionnaires typés,
// une fonction `t(key, params)` avec interpolation `{x}` et pluriel via Intl.PluralRules.
//
// Usage dans un composant (réactif au changement de langue) :
//   const t = useT()
//   <button>{t('common.save')}</button>
//   <span>{t('chat.sources', { count: n })}</span>
//
// Usage hors composant (fonctions utilitaires, ex. timeAgo) :
//   translate('time.seconds', { n: 12 })

import { create } from 'zustand'
import { en } from './en'
import { fr } from './fr'

export type Lang = 'en' | 'fr'

// Une entrée de dictionnaire : chaîne simple, ou objet de pluriel { one, other }.
type Plural = { one: string; other: string; zero?: string }
type Entry = string | Plural
export type Dict = { [k: string]: Entry | Dict }

const DICTS: Record<Lang, Dict> = { en, fr }
const STORAGE_KEY = 'hemicycle.lang'

// Langue initiale : préférence persistée si valide, sinon FRANÇAIS (défaut
// produit — l'assistant s'adresse aux citoyens, même en navigation privée).
function initialLang(): Lang {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'fr' || saved === 'en') return saved
  } catch {
    /* localStorage indisponible (SSR / mode privé) — repli français */
  }
  return 'fr'
}

type I18nStore = {
  lang: Lang
  setLang: (l: Lang) => void
}

// Store global de la langue. `setLang` persiste le choix et met à jour `<html lang>`.
export const useI18n = create<I18nStore>((set) => ({
  lang: initialLang(),
  setLang: (lang) => {
    try {
      localStorage.setItem(STORAGE_KEY, lang)
    } catch {
      /* ignore — la langue reste effective le temps de la session */
    }
    if (typeof document !== 'undefined') document.documentElement.lang = lang
    set({ lang })
  },
}))

// Descend une clé pointée (« a.b.c ») dans un dictionnaire imbriqué.
function resolve(dict: Dict, key: string): Entry | Dict | undefined {
  return key.split('.').reduce<Entry | Dict | undefined>(
    (o, k) => (o && typeof o === 'object' ? (o as Dict)[k] : undefined),
    dict,
  )
}

// Applique pluriel (si `count` fourni et entrée pluriel) puis interpolation `{x}`.
function format(value: Entry | Dict | undefined, lang: Lang, params?: Record<string, unknown>): string {
  if (value == null) return ''
  let raw: string
  if (typeof value === 'object') {
    // Entrée de pluriel : on choisit la catégorie via Intl.PluralRules.
    if (params && typeof params.count === 'number') {
      const cat = new Intl.PluralRules(lang).select(params.count) as keyof Plural
      const p = value as Plural
      raw = (cat === 'zero' && p.zero != null ? p.zero : p[cat]) ?? p.other ?? p.one ?? ''
    } else {
      raw = (value as Plural).other ?? (value as Plural).one ?? ''
    }
  } else {
    raw = value
  }
  if (params) {
    raw = raw.replace(/\{(\w+)\}/g, (_, k) =>
      params[k] != null ? String(params[k]) : `{${k}}`)
  }
  return raw
}

// Traduction « brute » lisant la langue courante du store (pour le code hors React).
export function translate(key: string, params?: Record<string, unknown>): string {
  const lang = useI18n.getState().lang
  const value = resolve(DICTS[lang], key) ?? resolve(DICTS.en, key) ?? key
  return format(value === key ? key : value, lang, params)
}

// Langue courante (lecture ponctuelle hors React).
export function currentLang(): Lang {
  return useI18n.getState().lang
}

// Hook de traduction : se ré-abonne à la langue → re-render au changement de drapeau.
export function useT(): (key: string, params?: Record<string, unknown>) => string {
  const lang = useI18n((s) => s.lang)
  return (key, params) => {
    const value = resolve(DICTS[lang], key) ?? resolve(DICTS.en, key) ?? key
    return format(value === key ? key : value, lang, params)
  }
}
