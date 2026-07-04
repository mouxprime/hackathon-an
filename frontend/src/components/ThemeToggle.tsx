// Bascule clair / nuit — vit dans le header navy (texte clair).
// Affiche une lune en mode clair (→ passer en nuit) et un soleil en mode nuit
// (→ revenir au clair). L'état est porté par useThemeStore (persisté).

import { useThemeStore } from '../lib/store'
import { useT } from '../lib/i18n'

// Icône lune (mode clair actif : clic = passer en nuit).
function MoonIcon({ className = '' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.7">
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" strokeLinejoin="round" />
    </svg>
  )
}

// Icône soleil (mode nuit actif : clic = revenir au clair).
function SunIcon({ className = '' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  )
}

// Bouton de bascule du thème (placé à gauche du compteur d'agents).
export function ThemeToggle() {
  const t = useT()
  const theme = useThemeStore((s) => s.theme)
  const toggle = useThemeStore((s) => s.toggle)
  const isDark = theme === 'dark'
  const label = isDark ? t('header.themeLight') : t('header.themeDark')

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={label}
      title={label}
      className="flex items-center justify-center rounded-lg p-1.5 text-navy-200 transition hover:bg-white/10 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-white/40"
    >
      {isDark ? <SunIcon className="h-4 w-4" /> : <MoonIcon className="h-4 w-4" />}
    </button>
  )
}
