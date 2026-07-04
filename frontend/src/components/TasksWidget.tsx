// Widget léger : liste des tâches actives de la conversation courante.
//
// Reconstruit à partir des UIEvent `widget`/`widget_update` de type
// `task_progress` (déjà émis par l'orchestrateur). Compact, scrollable.

import { useMemo } from 'react'
import type { Progress, UIEvent } from '../lib/types'
import { Bar, Pill, STATUS_COLORS } from '../lib/ui'
import { useT } from '../lib/i18n'

type Row = {
  corr_id: string
  agent_type: string
  status: string
  pct: number
  instruction: string
}

function build(events: UIEvent[]): Row[] {
  const by: Record<string, Row> = {}
  for (const ev of events) {
    if (ev.type === 'widget' && ev.payload.widget_type === 'task_progress') {
      const d = ev.payload.data || {}
      by[ev.payload.widget_id] = {
        corr_id: d.corr_id, agent_type: d.agent_type, status: d.status,
        pct: d.pct || 0, instruction: d.instruction || '',
      }
    } else if (ev.type === 'widget_update' && by[ev.payload.widget_id]) {
      Object.assign(by[ev.payload.widget_id], ev.payload.patch)
    }
  }
  return Object.values(by)
}

export function TasksWidget({ events, progress }:
                              { events: UIEvent[]; progress: Record<string, Progress> }) {
  const t = useT()
  const rows = useMemo(() => build(events), [events])

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex shrink-0 items-center justify-end border-b border-navy-700/20 px-2 py-1">
        <span className="mono text-[10px] text-slate-400 dark:text-slate-500">{rows.length}</span>
      </div>
      <div className="flex-1 space-y-1.5 overflow-y-auto p-2 text-[11px]">
        {rows.length === 0 && (
          <p className="mt-6 text-center text-slate-400 dark:text-slate-500">{t('widgets.tasksWidget.empty')}</p>
        )}
        {rows.map((r) => {
          const live = progress[r.corr_id]
          const pct = live?.pct ?? r.pct
          return (
            <div key={r.corr_id} className="rounded border border-slate-200 dark:border-ink-600 bg-slate-50 dark:bg-ink-700 p-2">
              <div className="flex items-center gap-2">
                <span className="mono text-[11px] font-semibold text-slate-800 dark:text-slate-100">
                  {r.agent_type?.toUpperCase()}
                </span>
                <Pill label={r.status} cls={STATUS_COLORS[r.status]} />
                <span className="ml-auto mono text-[10px] text-slate-400 dark:text-slate-500">
                  {Math.round(pct * 100)}%
                </span>
              </div>
              <div className="mt-1"><Bar pct={pct} /></div>
              {r.instruction && (
                <div className="mt-1 line-clamp-2 text-[10px] italic text-slate-400 dark:text-slate-500">
                  {r.instruction}
                </div>
              )}
              {live?.note && (
                <div className="mono mt-0.5 text-[10px] text-slate-400 dark:text-slate-500">↳ {live.note}</div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
