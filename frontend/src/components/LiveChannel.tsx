// Channel temps réel : flux déroulant de TOUS les échanges de la conv courante
// (UIEvent durables + traces) — affichage compact horodaté, filtrable.

import { useMemo, useState } from 'react'
import type { Progress, UIEvent } from '../lib/types'
import { Pill, timeAgo } from '../lib/ui'
import { useT } from '../lib/i18n'

type Entry = {
  ts: string
  kind: string
  label: string
  detail: string
}

type Filter = 'all' | 'message' | 'widget' | 'trace' | 'status'

function eventToEntry(ev: UIEvent): Entry {
  if (ev.type === 'message') {
    return { ts: ev.ts, kind: 'message', label: ev.payload.role,
             detail: ev.payload.text }
  }
  if (ev.type === 'status') {
    return { ts: ev.ts, kind: 'status', label: 'FSM',
             detail: ev.payload.state }
  }
  if (ev.type === 'widget') {
    return { ts: ev.ts, kind: 'widget', label: ev.payload.widget_type,
             detail: ev.payload.widget_id }
  }
  if (ev.type === 'widget_update') {
    return { ts: ev.ts, kind: 'widget', label: 'patch',
             detail: `${ev.payload.widget_id} ← ${JSON.stringify(ev.payload.patch)}` }
  }
  return { ts: ev.ts, kind: ev.type, label: ev.type, detail: JSON.stringify(ev.payload).slice(0, 80) }
}

function traceToEntry(t: Progress): Entry {
  const td = t.trace_data || {}
  let detail = ''
  if (t.kind === 'thinking') detail = td.text || ''
  else if (t.kind === 'tool_call') detail = `→ ${td.tool}(${Object.keys(td.params || {}).join(', ')})`
  else if (t.kind === 'tool_result') detail = `← ${td.tool} ${td.skipped ? '(SKIPPED)' : ''}`
  return { ts: t.ts, kind: 'trace', label: `${t.agent_id}/${t.kind}`, detail }
}

const KIND_CLS: Record<string, string> = {
  message: 'text-navy-700 dark:text-navy-300 bg-navy-50 dark:bg-navy-500/15 border-navy-200 dark:border-navy-500/30',
  widget: 'text-violet-700 dark:text-violet-300 bg-violet-50 dark:bg-violet-500/15 border-violet-200 dark:border-violet-500/30',
  status: 'text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-500/15 border-amber-200 dark:border-amber-500/30',
  trace: 'text-emerald-700 dark:text-emerald-300 bg-emerald-50 dark:bg-emerald-500/15 border-emerald-200 dark:border-emerald-500/30',
}

type Props = { events: UIEvent[]; traces: Progress[] }

export function LiveChannel({ events, traces }: Props) {
  const t = useT()
  const [filter, setFilter] = useState<Filter>('all')
  const entries = useMemo(() => {
    const combined: Entry[] = [
      ...events.map(eventToEntry),
      ...traces.map(traceToEntry),
    ]
    combined.sort((a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime())
    return combined.reverse()                      // plus récent en haut
  }, [events, traces])
  const filtered = filter === 'all' ? entries : entries.filter((e) => e.kind === filter)

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex shrink-0 items-center justify-end border-b border-navy-700/20 px-2 py-1">
        <div className="flex gap-1">
          {(['all', 'message', 'widget', 'trace', 'status'] as Filter[]).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`rounded border px-1.5 py-0.5 text-[10px] ${
                filter === f
                  ? 'border-navy-700 bg-navy-700 text-white'
                  : 'border-slate-200 dark:border-ink-600 bg-white dark:bg-ink-800 text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-100'
              }`}
            >
              {t(`widgets.liveChannel.filters.${f}`)}
            </button>
          ))}
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-2 text-[11px]">
        {filtered.length === 0 && (
          <p className="mt-6 text-center text-slate-400 dark:text-slate-500">{t('widgets.liveChannel.empty')}</p>
        )}
        {filtered.map((e, i) => (
          <div key={i} className="mb-1.5 rounded border border-slate-100 dark:border-ink-600 bg-slate-50 dark:bg-ink-700 p-1.5">
            <div className="mb-0.5 flex items-center justify-between">
              <Pill label={`${e.kind}/${e.label}`} cls={KIND_CLS[e.kind]} />
              <span className="mono text-[9px] text-slate-400 dark:text-slate-500">{timeAgo(e.ts)}</span>
            </div>
            {e.detail && <div className="break-words text-slate-800 dark:text-slate-100">{e.detail}</div>}
          </div>
        ))}
      </div>
    </div>
  )
}
