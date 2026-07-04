// Sélecteur de langue du header : un seul drapeau (langue courante), clic = bascule EN⇄FR.
// Drapeaux en SVG inline → rendu net et fiable cross-OS (vs emojis 🇬🇧/🇫🇷 cassés sous Windows).

import { useI18n } from '../lib/i18n'
import { useT } from '../lib/i18n'

// Drapeau britannique (Union Jack) simplifié — proportions 20×14, coins arrondis.
function FlagGB() {
  return (
    <svg viewBox="0 0 20 14" className="h-3.5 w-5 rounded-[2px] shadow-sm" aria-hidden="true">
      <clipPath id="gb-clip"><rect width="20" height="14" rx="2" /></clipPath>
      <g clipPath="url(#gb-clip)">
        <rect width="20" height="14" fill="#012169" />
        <path d="M0 0 L20 14 M20 0 L0 14" stroke="#fff" strokeWidth="2.4" />
        <path d="M0 0 L20 14 M20 0 L0 14" stroke="#C8102E" strokeWidth="1.2" />
        <path d="M10 0 V14 M0 7 H20" stroke="#fff" strokeWidth="3.6" />
        <path d="M10 0 V14 M0 7 H20" stroke="#C8102E" strokeWidth="2" />
      </g>
    </svg>
  )
}

// Drapeau français — tricolore vertical bleu/blanc/rouge.
function FlagFR() {
  return (
    <svg viewBox="0 0 20 14" className="h-3.5 w-5 rounded-[2px] shadow-sm" aria-hidden="true">
      <clipPath id="fr-clip"><rect width="20" height="14" rx="2" /></clipPath>
      <g clipPath="url(#fr-clip)">
        <rect width="20" height="14" fill="#fff" />
        <rect width="6.67" height="14" x="0" fill="#002395" />
        <rect width="6.67" height="14" x="13.33" fill="#ED2939" />
      </g>
    </svg>
  )
}

// Bouton de bascule de langue (placé dans le header global).
export function FlagToggle() {
  const { lang, setLang } = useI18n()
  const t = useT()
  const next = lang === 'en' ? 'fr' : 'en'
  const title = next === 'fr' ? t('header.switchToFrench') : t('header.switchToEnglish')
  return (
    <button
      type="button"
      onClick={() => setLang(next)}
      title={title}
      aria-label={title}
      className="flex items-center gap-1.5 rounded-md px-1.5 py-1 text-navy-200 transition hover:bg-white/10 hover:text-white"
    >
      {lang === 'en' ? <FlagGB /> : <FlagFR />}
      <span className="mono text-[10px] font-semibold uppercase">{lang}</span>
    </button>
  )
}
