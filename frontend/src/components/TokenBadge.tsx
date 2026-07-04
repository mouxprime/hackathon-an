// Badge de tokens de contexte — fond interpolé slate→ambre selon la charge,
// icône recycle à gauche du nombre. Clic → popup ancré au-dessus avec une barre
// de progression (jauge) + bouton Compacter. Fermeture au clic extérieur / Esc.

import { useEffect, useRef, useState } from 'react'
import { useT } from '../lib/i18n'
import { useThemeStore } from '../lib/store'
import { ContextPanel, RecycleIcon } from './ContextPanel'

type Props = {
  tokens: number
  max: number
  onCompact: () => void
}

// Formate un nombre de tokens : 12400 → "12.4k", 1234567 → "1.23M".
function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 10_000)    return `${(n / 1_000).toFixed(1)}k`
  if (n >= 1_000)     return `${(n / 1_000).toFixed(2)}k`
  return String(n)
}

// Interpolation linéaire entre deux valeurs.
function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * Math.min(1, Math.max(0, t))
}

// Couleur de fond (hsl). Clair : BLANC à vide → ambre pâle à pleine charge.
// Nuit : surface ink sombre à vide → ambre profond chaud à pleine charge.
function tokenBgColor(ratio: number, dark: boolean): string {
  if (dark) {
    const h = lerp(222, 42, ratio)
    const s = lerp(47, 55, ratio)
    const l = lerp(16, 24, ratio)
    return `hsl(${h.toFixed(0)}, ${s.toFixed(0)}%, ${l.toFixed(0)}%)`
  }
  const s = lerp(0, 95, ratio)
  const l = lerp(100, 82, ratio)
  return `hsl(42, ${s.toFixed(0)}%, ${l.toFixed(0)}%)`
}

// Bordure : gris/navy discret à vide → ambre à pleine charge.
function tokenBorderColor(ratio: number, dark: boolean): string {
  const h = lerp(dark ? 222 : 215, 42, ratio)
  const s = lerp(dark ? 39 : 16, 90, ratio)
  const l = lerp(dark ? 30 : 80, dark ? 45 : 70, ratio)
  return `hsl(${h.toFixed(0)}, ${s.toFixed(0)}%, ${l.toFixed(0)}%)`
}

// Couleur de texte. Clair : slate-600 → amber-800. Nuit : slate-300 → amber-300.
function tokenTextColor(ratio: number, dark: boolean): string {
  const h = lerp(dark ? 213 : 215, dark ? 42 : 38, ratio)
  const s = lerp(dark ? 27 : 25, dark ? 85 : 92, ratio)
  const l = lerp(dark ? 84 : 40, dark ? 70 : 30, ratio)
  return `hsl(${h.toFixed(0)}, ${s.toFixed(0)}%, ${l.toFixed(0)}%)`
}

export function TokenBadge({ tokens, max, onCompact }: Props) {
  const t = useT()
  const dark = useThemeStore((s) => s.theme === 'dark')
  const [open, setOpen] = useState(false)
  const btnRef = useRef<HTMLButtonElement>(null)
  const popRef = useRef<HTMLDivElement>(null)
  const ratio = Math.min(1, Math.max(0, tokens / max))

  // Ferme le popup au clic extérieur ou à Esc.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    const onOutside = (e: MouseEvent) => {
      if (!popRef.current?.contains(e.target as Node) && !btnRef.current?.contains(e.target as Node))
        setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onOutside)
    return () => {
      window.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onOutside)
    }
  }, [open])

  const bg = tokenBgColor(ratio, dark)
  const color = tokenTextColor(ratio, dark)
  const tooltip = t('chat.tokenBadgeTooltip', { tokens, max })

  return (
    <span className="relative inline-flex items-center">
      <button
        ref={btnRef}
        onClick={() => setOpen((v) => !v)}
        title={tooltip}
        style={{ background: bg, color, borderColor: tokenBorderColor(ratio, dark) }}
        className="mono inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-[11px] font-semibold transition hover:opacity-80"
        aria-haspopup="true"
        aria-expanded={open}
      >
        <RecycleIcon className="h-2.5 w-2.5 opacity-80" />
        {fmt(tokens)}
      </button>

      {open && (
        // Conteneur de POSITION (statique) ↔ enfant ANIMÉ : on ne mélange pas
        // `translate` de positionnement et le `transform` de l'animation popIn,
        // sinon le popup « saute » à l'ouverture. Ancré à droite pour ne pas
        // déborder hors du chat.
        <div ref={popRef} className="absolute bottom-full right-0 z-50 mb-1.5">
          <div className="card shadow-pop w-56 animate-popIn p-3">
            <ContextPanel
              tokens={tokens}
              max={max}
              onCompact={() => { setOpen(false); onCompact() }}
            />
          </div>
        </div>
      )}
    </span>
  )
}
