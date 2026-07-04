// Liste des channels (salons de groupe) — corps de l'onglet « Channels » du
// panneau latéral. Distinct de l'historique de conversation : sélectionner une
// channel bascule le panneau principal en mode salon (cf. useChannelStore).
//
// Le salon système « hemicycle » (alertes de l'orchestrateur) est épinglé en tête
// dans une section « Alertes » ; les salons d'état-major G1..G10 suivent.

import { useMemo } from 'react'
import { api } from '../lib/api'
import { usePolling } from '../lib/hooks'
import { useT } from '../lib/i18n'
import { useChannelStore } from '../lib/store'
import type { Channel } from '../lib/types'

type Props = {
  onClose: () => void
}

export function ChannelsList({ onClose }: Props) {
  const t = useT()
  const { channelId, setChannelId } = useChannelStore()
  const channels = usePolling(api.channels, 5000) || []

  // Le backend renvoie déjà trié par position ; on sépare juste système / staff
  // pour afficher deux sections distinctes.
  const { system, staff } = useMemo(() => {
    const system: Channel[] = []
    const staff: Channel[] = []
    for (const c of channels) (c.kind === 'system' ? system : staff).push(c)
    return { system, staff }
  }, [channels])

  const open = (id: string) => {
    setChannelId(id)
    onClose()
  }

  const renderItem = (c: Channel) => {
    const isActive = c.id === channelId
    return (
      <button
        key={c.id}
        onClick={() => open(c.id)}
        className={`flex w-full items-center gap-2 rounded-lg border px-2 py-1.5 text-left transition ${
          isActive
            ? 'border-navy-400 bg-navy-50 dark:bg-navy-500/15'
            : 'border-slate-200 dark:border-ink-600 bg-white dark:bg-ink-800 hover:bg-slate-50 dark:hover:bg-ink-700'
        }`}
      >
        <span className="shrink-0 text-sm" aria-hidden>
          {c.kind === 'system' ? '📢' : '#'}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[11px] font-medium text-slate-800 dark:text-slate-100">
            {c.name}
          </span>
        </span>
        {c.readonly && (
          <span className="mono shrink-0 rounded bg-slate-100 dark:bg-ink-700 px-1 py-0.5 text-[8px] uppercase tracking-wide text-slate-400 dark:text-slate-500">
            {t('channels.readonly')}
          </span>
        )}
      </button>
    )
  }

  return (
    <div className="flex flex-col gap-2 overflow-y-auto p-2">
      {system.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <h3 className="px-1 text-[9px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
            {t('channels.alertsSection')}
          </h3>
          {system.map(renderItem)}
        </div>
      )}
      <div className="flex flex-col gap-1.5">
        <h3 className="px-1 text-[9px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
          {t('channels.staffSection')}
        </h3>
        {staff.map(renderItem)}
      </div>
    </div>
  )
}
