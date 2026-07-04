// Jauge de contexte (tokens / max) + action Compacter.
// Partagée par le badge de tokens et la commande /context → « le même truc ».

import { useT } from '../lib/i18n'

// Icône recycle (= tokens / compaction). `fill=currentColor` pour suivre la
// couleur du texte du conteneur (le badge vire à l'ambre avec la charge).
export function RecycleIcon({ className = 'h-3 w-3' }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={className} fill="currentColor" aria-hidden="true">
      <path d="M5.68623 0H10.3138L12.178 3.27835L13.9282 2.26789L14.4282 3.13392L13.3301 7.232L9.23203 6.13392L8.73203 5.26789L10.4459 4.27837L9.15033 2L6.84966 2L6.29552 2.97447L4.56343 1.97445L5.68623 0Z" />
      <path d="M13.1649 9.05964L13.7039 10.0076L12.6055 12H9.99998L9.99998 9.99995H8.99998L5.99998 12.9999L8.99998 15.9999H9.99998L9.99998 14H13.7868L15.996 9.99242L14.8969 8.05962L13.1649 9.05964Z" />
      <path d="M3.39445 12H4.49998V14H2.21325L0.00390625 9.99242L1.8446 6.75554L0.0717772 5.732L0.571776 4.86598L4.66986 3.7679L5.76793 7.86598L5.26793 8.732L3.57669 7.75556L2.29605 10.0076L3.39445 12Z" />
    </svg>
  )
}

// Formate un nombre de tokens : 12400 → "12.4k", 1234567 → "1.23M".
function fmtN(n: number): string {
  if (n >= 1_000_000) return `${(n / 1e6).toFixed(2)}M`
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

// Barre seule : libellés + jauge colorée (navy → ambre > 70% → rouge > 90%).
export function ContextBar({ tokens, max }: { tokens: number; max: number }) {
  const t = useT()
  const ratio = Math.min(1, Math.max(0, tokens / max))
  const pct = Math.round(ratio * 100)
  const barCls = ratio > 0.9
    ? 'bg-red-500 dark:bg-red-400'
    : ratio > 0.7
      ? 'bg-amber-500 dark:bg-amber-400'
      : 'bg-navy-600 dark:bg-navy-500'
  return (
    <div className="space-y-2">
      <div className="flex justify-between text-[11px]">
        <span className="text-slate-600 dark:text-slate-300">{fmtN(tokens)} {t('chat.tokens')}</span>
        <span className="text-slate-400 dark:text-slate-500">/ {fmtN(max)} · {pct}%</span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-ink-700">
        <div
          className={`h-full rounded-full transition-all duration-300 ${barCls}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

// Panneau complet : barre + bouton Compacter. Affiché au clic sur le badge
// et par la commande /context (contenu identique).
export function ContextPanel({ tokens, max, onCompact }: {
  tokens: number
  max: number
  onCompact: () => void
}) {
  const t = useT()
  return (
    <div className="space-y-2.5">
      <ContextBar tokens={tokens} max={max} />
      <button
        onClick={onCompact}
        className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-slate-200 dark:border-ink-600 bg-white dark:bg-ink-800 px-2 py-1.5 text-[11px] font-semibold text-navy-700 dark:text-navy-200 transition hover:border-navy-300 hover:bg-slate-50 dark:hover:bg-ink-700"
      >
        <RecycleIcon className="h-3.5 w-3.5" /> {t('chat.compact')}
      </button>
    </div>
  )
}
