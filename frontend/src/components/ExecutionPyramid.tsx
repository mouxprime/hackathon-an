// Pyramide d'exécution : visualisation hiérarchique de la chaîne en cours.
//
// 5 niveaux, construits en mémoire depuis les UIEvent + traces de la conv :
//   0. prompt utilisateur (dernier message user)
//   1. orchestrateur (état FSM courant + plan)
//   2. agents dispatchés (par task_progress widget)
//   3. tools appelés (depuis trace events tool_call / tool_result)
//   4. pensées (thinking events, dépliables)
//
// L'arbre se reconstruit à chaque tick d'event, sans clignoter — chaque card
// porte une key stable (corr_id, tool name).

import { useMemo, useState } from 'react'
import type { Progress, UIEvent } from '../lib/types'
import { Bar, FSM_COLORS, Pill, STATUS_COLORS, agentLabel, fsmLabel } from '../lib/ui'
import { useT } from '../lib/i18n'

type TaskNode = {
  corr_id: string
  agent_type: string
  status: string
  pct: number
  instruction: string
  toolCalls: { name: string; ts: string; params?: any; result?: any; skipped?: string }[]
  thoughts: { ts: string; text: string }[]
}

type Pyramid = {
  prompt: string | null
  fsmState: string | null
  planSize: number
  tasks: TaskNode[]
}

function build(events: UIEvent[], traces: Progress[]): Pyramid {
  const userMsgs = events.filter((e) => e.type === 'message' && e.payload.role === 'user')
  const status = events.filter((e) => e.type === 'status')
  const taskWidgets: Record<string, any> = {}
  for (const ev of events) {
    if (ev.type === 'widget' && ev.payload.widget_type === 'task_progress') {
      taskWidgets[ev.payload.widget_id] = { ...ev.payload.data }
    } else if (ev.type === 'widget_update' && taskWidgets[ev.payload.widget_id]) {
      Object.assign(taskWidgets[ev.payload.widget_id], ev.payload.patch)
    }
  }
  const tasks: TaskNode[] = Object.values(taskWidgets).map((t: any) => ({
    corr_id: t.corr_id, agent_type: t.agent_type, status: t.status,
    pct: t.pct || 0, instruction: t.instruction || '', toolCalls: [], thoughts: [],
  }))
  // Indexe par corr_id pour rattacher les traces.
  const byCorr = new Map(tasks.map((t) => [t.corr_id, t]))
  for (const tr of traces) {
    const node = byCorr.get(tr.corr_id)
    if (!node) continue
    const td = tr.trace_data || {}
    if (tr.kind === 'thinking') {
      node.thoughts.push({ ts: tr.ts, text: td.text || '' })
    } else if (tr.kind === 'tool_call') {
      node.toolCalls.push({ name: td.tool || '?', ts: tr.ts, params: td.params })
    } else if (tr.kind === 'tool_result') {
      // Cherche le call correspondant (dernier non-clos), sinon push standalone.
      const open = [...node.toolCalls].reverse().find((c) => c.name === td.tool && c.result === undefined)
      if (open) {
        open.result = td
        open.skipped = td.skipped
      } else {
        node.toolCalls.push({ name: td.tool || '?', ts: tr.ts, result: td, skipped: td.skipped })
      }
    }
  }
  return {
    prompt: userMsgs.length ? userMsgs[userMsgs.length - 1].payload.text : null,
    fsmState: status.length ? status[status.length - 1].payload.state : null,
    planSize: tasks.length,
    tasks,
  }
}

function TaskCard({ task, overrideProgress }: { task: TaskNode; overrideProgress?: Progress }) {
  const t = useT()
  const [open, setOpen] = useState(true)
  const pct = overrideProgress?.pct ?? task.pct
  const note = overrideProgress?.note || ''
  return (
    <div className="rounded-lg border border-slate-200 dark:border-ink-600 bg-white dark:bg-ink-800">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left"
      >
        <span className="mono text-[10px] text-slate-400 dark:text-slate-500">{open ? '▾' : '▸'}</span>
        <span className="mono text-[11px] font-semibold text-slate-800 dark:text-slate-100">
          {agentLabel(task.agent_type)}
        </span>
        <Pill label={task.status} cls={STATUS_COLORS[task.status]} />
        <span className="ml-auto mono text-[10px] text-slate-400 dark:text-slate-500">
          {Math.round(pct * 100)}%
        </span>
      </button>
      <div className="px-2">
        <Bar pct={pct} />
      </div>
      {note && <div className="px-2 pt-1 text-[10px] italic text-slate-400 dark:text-slate-500">{note}</div>}
      {open && (
        <div className="space-y-1.5 px-2 py-2">
          {task.toolCalls.length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-wider text-slate-400 dark:text-slate-500">{t('widgets.pyramidWidget.tools')}</div>
              {task.toolCalls.map((c, i) => (
                <div key={i} className="mt-1 rounded border border-slate-200 dark:border-ink-600 bg-slate-50 dark:bg-ink-700 p-1.5">
                  <div className="flex items-center gap-1.5">
                    <span className="mono text-[11px] text-navy-700 dark:text-navy-200">{c.name}</span>
                    {c.skipped && (
                      <span className="rounded bg-amber-50 dark:bg-amber-500/15 px-1.5 text-[9px] text-amber-700 dark:text-amber-300">
                        SKIPPED ({c.skipped})
                      </span>
                    )}
                    {c.result !== undefined && !c.skipped && (
                      <span className="rounded bg-emerald-50 dark:bg-emerald-500/15 px-1.5 text-[9px] text-emerald-700 dark:text-emerald-300">
                        OK
                      </span>
                    )}
                  </div>
                  {c.params && (
                    <div className="mono mt-0.5 text-[10px] text-slate-400 dark:text-slate-500">
                      params: {JSON.stringify(c.params)}
                    </div>
                  )}
                  {c.result && !c.skipped && (
                    <div className="mono mt-0.5 text-[10px] text-slate-400 dark:text-slate-500">
                      {Object.entries(c.result)
                        .filter(([k]) => k !== 'tool')
                        .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`)
                        .join(' · ')
                        .slice(0, 200)}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
          {task.thoughts.length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-wider text-slate-400 dark:text-slate-500">
                {t('widgets.pyramidWidget.thoughts')}
              </div>
              {task.thoughts.map((t, i) => (
                <div key={i} className="mt-0.5 italic text-[11px] text-slate-400 dark:text-slate-500">
                  « {t.text} »
                </div>
              ))}
            </div>
          )}
          {task.toolCalls.length === 0 && task.thoughts.length === 0 && (
            <p className="text-[10px] text-slate-400 dark:text-slate-500">{t('widgets.pyramidWidget.waiting')}</p>
          )}
        </div>
      )}
    </div>
  )
}

type Props = {
  events: UIEvent[]
  traces: Progress[]
  progress: Record<string, Progress>
}

export function ExecutionPyramid({ events, traces, progress }: Props) {
  const t = useT()
  const pyramid = useMemo(() => build(events, traces), [events, traces])

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex shrink-0 items-center justify-end border-b border-navy-700/20 px-2 py-1">
        {pyramid.fsmState && (
          /* Libellé citoyen (« Je prépare ma réponse »…) — état technique en title. */
          <span title={pyramid.fsmState}>
            <Pill label={fsmLabel(pyramid.fsmState)} cls={FSM_COLORS[pyramid.fsmState]} />
          </span>
        )}
      </div>
      <div className="flex-1 overflow-y-auto p-3">
        {!pyramid.prompt && (
          <p className="mt-12 text-center text-sm text-slate-400 dark:text-slate-500">
            {t('widgets.pyramidWidget.sendPrompt')}
          </p>
        )}

        {/* Niveau 0 : prompt */}
        {pyramid.prompt && (
          <div className="mb-3">
            <div className="text-[10px] uppercase tracking-wider text-slate-400 dark:text-slate-500">
              {t('widgets.pyramidWidget.userPrompt')}
            </div>
            <div className="mt-1 rounded-lg border border-navy-200 dark:border-navy-500/30 bg-navy-50 dark:bg-navy-500/15 px-3 py-2 text-sm text-navy-700 dark:text-navy-200">
              {pyramid.prompt}
            </div>
          </div>
        )}

        {/* Niveau 1 : orchestrateur */}
        {pyramid.prompt && (
          <div className="mb-3 flex justify-center">
            <div className="w-2/3 rounded-lg border border-violet-200 dark:border-violet-500/30 bg-violet-50 dark:bg-violet-500/15 px-3 py-2">
              <div className="text-center text-[11px] font-semibold text-violet-700 dark:text-violet-300">
                {t('widgets.pyramidWidget.orchestrator')}
              </div>
              <div className="mt-0.5 text-center text-[10px] text-violet-600/70 dark:text-violet-400">
                {t('widgets.pyramidWidget.planSteps', { state: fsmLabel(pyramid.fsmState) || '?', count: pyramid.planSize })}
              </div>
            </div>
          </div>
        )}

        {/* Niveau 2-4 : agents avec tools + pensées */}
        {pyramid.tasks.length > 0 && (
          <div className="mb-3">
            <div className="mb-1 text-[10px] uppercase tracking-wider text-slate-400 dark:text-slate-500">
              {t('widgets.pyramidWidget.agentsToolsThoughts')}
            </div>
            <div className="grid gap-2"
                  style={{ gridTemplateColumns: `repeat(${Math.min(pyramid.tasks.length, 3)}, minmax(0, 1fr))` }}>
              {pyramid.tasks.map((t) => (
                <TaskCard key={t.corr_id} task={t}
                          overrideProgress={progress[t.corr_id]} />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
