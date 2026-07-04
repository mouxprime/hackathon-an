// Zone de chips pour les convs référencées via @ — affichée entre la barre
// d'actions et l'input, uniquement si au moins une conv est référencée.

import { useT } from '../lib/i18n'
import type { ConvRef } from '../lib/types'

type Props = {
  refs: ConvRef[]
  onRemove: (conv_id: string) => void
}

export function ContextRefChips({ refs, onRemove }: Props) {
  const t = useT()
  if (refs.length === 0) return null
  return (
    <div className="flex flex-wrap items-center gap-1.5 border-t border-slate-200 px-2 py-1.5 dark:border-ink-600">
      <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
        {t('chat.contextLabel')}
      </span>
      {refs.map((ref) => (
        <span
          key={ref.conv_id}
          className="mono flex items-center gap-1 rounded-full border border-navy-200 bg-navy-50 px-2 py-0.5 text-[10px] text-navy-700 dark:border-navy-500/30 dark:bg-navy-500/15 dark:text-navy-300"
        >
          🔗 {ref.title || ref.conv_id.slice(0, 8)}
          <button
            onClick={() => onRemove(ref.conv_id)}
            className="text-navy-400 transition hover:text-navy-700 dark:hover:text-navy-200"
            title={`Retirer « ${ref.title} » du contexte`}
          >
            ✕
          </button>
        </span>
      ))}
    </div>
  )
}
