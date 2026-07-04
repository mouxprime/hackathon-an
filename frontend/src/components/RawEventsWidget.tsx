// Widget debug : derniers UIEvent bruts en JSON (lecture seule).
//
// Pratique pour vérifier ce qui sort réellement du gateway sans changer d'onglet
// vers /operations → Bus.

import type { UIEvent } from '../lib/types'
import { useT } from '../lib/i18n'

export function RawEventsWidget({ events }: { events: UIEvent[] }) {
  const t = useT()
  const last = events.slice(-30).reverse()
  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex shrink-0 items-center justify-end border-b border-navy-700/20 px-2 py-1">
        <span className="mono text-[10px] text-slate-400 dark:text-slate-500">{t('widgets.rawEvents.received', { count: events.length })}</span>
      </div>
      <div className="flex-1 overflow-y-auto p-2">
        {last.length === 0 && (
          <p className="mt-6 text-center text-[11px] text-slate-400 dark:text-slate-500">{t('widgets.rawEvents.waiting')}</p>
        )}
        {last.map((e) => (
          <div key={e.seq}
                className="mono mb-1 rounded border border-slate-200 dark:border-ink-600 bg-slate-50 dark:bg-ink-700 p-1.5 text-[10px] text-slate-800 dark:text-slate-100">
            <div className="text-navy-700 dark:text-navy-200">#{e.seq} · {e.type}</div>
            <div className="break-all text-slate-400 dark:text-slate-500">{JSON.stringify(e.payload).slice(0, 320)}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
