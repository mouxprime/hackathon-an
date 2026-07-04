// Widget compact : état FSM courant + résumé de plan (lecture seule).
//
// Lit les UIEvent `status` (transitions FSM) et l'enchaînement de tâches pour
// projeter une vision "macro" qui complète la pyramide sans la dupliquer.

import { useMemo } from 'react'
import type { UIEvent } from '../lib/types'
import { FSM_COLORS, Pill, fsmLabel, timeAgo } from '../lib/ui'
import { useT } from '../lib/i18n'

export function FsmWidget({ events }: { events: UIEvent[] }) {
  const t = useT()
  const { state, transitions, planAgents } = useMemo(() => {
    const status = events.filter((e) => e.type === 'status')
    const widgets = events.filter((e) => e.type === 'widget' && e.payload.widget_type === 'task_progress')
    const planAgents = Array.from(new Set(widgets.map((w) => w.payload.data?.agent_type).filter(Boolean)))
    return {
      state: status.length ? status[status.length - 1].payload.state : 'IDLE',
      transitions: status.slice(-6).reverse(),
      planAgents,
    }
  }, [events])

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex shrink-0 items-center justify-end border-b border-navy-700/20 px-2 py-1">
        {/* Libellé citoyen — état technique dans le title (info-bulle). */}
        <span title={state}>
          <Pill label={fsmLabel(state)} cls={FSM_COLORS[state]} />
        </span>
      </div>
      <div className="flex-1 space-y-3 overflow-y-auto p-3 text-[11px]">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-slate-400 dark:text-slate-500">{t('widgets.fsmWidget.currentPlan')}</div>
          {planAgents.length === 0 && <p className="mt-1 text-slate-400 dark:text-slate-500">{t('widgets.fsmWidget.empty')}</p>}
          {planAgents.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1">
              {planAgents.map((a) => (
                <span key={a}
                      className="mono rounded border border-slate-200 dark:border-ink-600 bg-slate-50 dark:bg-ink-700 px-1.5 py-0.5 text-[10px] text-slate-800 dark:text-slate-100">
                  {a}
                </span>
              ))}
            </div>
          )}
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-slate-400 dark:text-slate-500">
            {t('widgets.fsmWidget.latestTransitions')}
          </div>
          {transitions.length === 0 && <p className="mt-1 text-slate-400 dark:text-slate-500">{t('widgets.fsmWidget.none')}</p>}
          <div className="mt-1 space-y-1">
            {transitions.map((t) => (
              <div key={t.seq} className="flex items-center justify-between">
                <span title={t.payload.state}>
                  <Pill label={fsmLabel(t.payload.state)} cls={FSM_COLORS[t.payload.state]} />
                </span>
                <span className="mono text-[10px] text-slate-400 dark:text-slate-500">{timeAgo(t.ts)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
