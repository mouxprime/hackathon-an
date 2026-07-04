// Vue Bus — tail live des événements UI publiés par l'orchestrateur sur le bus NATS.

import { api } from '../lib/api'
import { usePolling } from '../lib/hooks'
import { useT } from '../lib/i18n'
import type { UIEvent } from '../lib/types'
import { Panel, Pill, timeAgo } from '../lib/ui'

const TYPE_COLORS: Record<string, string> = {
  message: 'text-slate-600 dark:text-slate-300 bg-slate-100 dark:bg-ink-700 border-slate-300 dark:border-ink-600',
  widget: 'text-navy-700 dark:text-navy-200 bg-navy-50 dark:bg-navy-500/15 border-navy-200 dark:border-navy-500/30',
  widget_update: 'text-violet-700 dark:text-violet-300 bg-violet-50 dark:bg-violet-500/15 border-violet-200 dark:border-violet-500/30',
  status: 'text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-500/15 border-amber-200 dark:border-amber-500/30',
  task_update: 'text-emerald-700 dark:text-emerald-300 bg-emerald-50 dark:bg-emerald-500/15 border-emerald-200 dark:border-emerald-500/30',
}

// Résumé compact du payload selon le type d'événement.
function summarize(ev: UIEvent): string {
  const p = ev.payload || {}
  if (ev.type === 'message') return `${p.role}: ${p.text}`
  if (ev.type === 'status') return `→ ${p.state}`
  if (ev.type === 'widget') return `${p.widget_type} (${p.widget_id?.slice(0, 8)})`
  if (ev.type === 'widget_update') return `${p.widget_id?.slice(0, 8)} ${JSON.stringify(p.patch)}`
  return JSON.stringify(p)
}

export function BusView() {
  const t = useT()
  const events = (usePolling(api.bus, 1000) || []) as UIEvent[]
  const ordered = [...events].reverse() // plus récents en haut

  return (
    <Panel title={t('monitoring.bus.title', { count: events.length })}>
      <div className="space-y-1">
        {ordered.map((ev, i) => (
          <div
            key={`${ev.conv_id}-${ev.seq}-${i}`}
            className="flex items-center gap-2 rounded border border-slate-200 dark:border-ink-600 bg-slate-50 dark:bg-ink-700 px-2 py-1 text-xs"
          >
            <Pill label={ev.type} cls={TYPE_COLORS[ev.type]} />
            <span className="mono text-[10px] text-slate-400 dark:text-slate-500">{ev.conv_id.slice(0, 8)}#{ev.seq}</span>
            <span className="flex-1 truncate text-slate-800 dark:text-slate-100">{summarize(ev)}</span>
            <span className="mono text-[10px] text-slate-400 dark:text-slate-500">{timeAgo(ev.ts)}</span>
          </div>
        ))}
        {events.length === 0 && (
          <p className="py-6 text-center text-xs text-slate-400 dark:text-slate-500">{t('monitoring.bus.empty')}</p>
        )}
      </div>
    </Panel>
  )
}
