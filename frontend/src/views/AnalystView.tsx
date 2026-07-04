// Vue Analyste — chat + canvas de widgets server-driven.
//
// L'analyste discute ; l'orchestrateur pilote ce qui s'affiche en publiant des
// événements UI (messages, widgets, statuts) ordonnés par `seq`. La progression
// live des tâches est superposée depuis le canal éphémère.

import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import { useConversation, usePolling } from '../lib/hooks'
import { useT } from '../lib/i18n'
import type { UIEvent } from '../lib/types'
import { FSM_COLORS, Pill } from '../lib/ui'
import { renderWidget } from '../widgets'

type TimelineItem =
  | { kind: 'message'; seq: number; role: string; text: string }
  | { kind: 'status'; seq: number; state: string }
  | { kind: 'widget'; seq: number; widget_id: string; widget_type: string; data: any }

// Rejoue les événements dans l'ordre du seq et applique les widget_update par widget_id.
function buildTimeline(events: UIEvent[]): TimelineItem[] {
  const items: TimelineItem[] = []
  const byId: Record<string, any> = {}
  for (const ev of events) {
    if (ev.type === 'message') {
      items.push({ kind: 'message', seq: ev.seq, role: ev.payload.role, text: ev.payload.text })
    } else if (ev.type === 'status') {
      items.push({ kind: 'status', seq: ev.seq, state: ev.payload.state })
    } else if (ev.type === 'widget') {
      const item: any = {
        kind: 'widget', seq: ev.seq, widget_id: ev.payload.widget_id,
        widget_type: ev.payload.widget_type, data: { ...ev.payload.data },
      }
      byId[ev.payload.widget_id] = item
      items.push(item)
    } else if (ev.type === 'widget_update') {
      const item = byId[ev.payload.widget_id]
      if (item) Object.assign(item.data, ev.payload.patch)
    }
  }
  return items
}

export function AnalystView() {
  const t = useT()
  const conversations = usePolling(api.conversations, 3000) || []
  const [convId, setConvId] = useState<string | null>(null)
  const { events, progress, connected, send } = useConversation(convId)
  const [draft, setDraft] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)

  const timeline = useMemo(() => buildTimeline(events), [events])
  const fsmState = useMemo(() => {
    const st = events.filter((e) => e.type === 'status')
    return st.length ? st[st.length - 1].payload.state : convId ? 'IDLE' : null
  }, [events, convId])

  // Auto-scroll vers le dernier élément à chaque nouvel événement.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [timeline.length])

  const submit = () => {
    if (draft.trim() && convId) {
      send(draft.trim())
      setDraft('')
    }
  }

  return (
    <div className="flex h-full gap-3">
      {/* Sélecteur de conversation */}
      <div className="flex w-56 shrink-0 flex-col gap-2 overflow-y-auto rounded-lg border border-slate-800 bg-slate-900/60 p-2">
        <button
          onClick={() => setConvId(crypto.randomUUID())}
          className="rounded border border-cyan-700 bg-cyan-950 px-2 py-1.5 text-xs font-medium text-cyan-300 hover:bg-cyan-900"
        >
          {t('monitoring.analyst.newInvestigation')}
        </button>
        {conversations.map((c) => (
          <button
            key={c.conv_id}
            onClick={() => setConvId(c.conv_id)}
            className={`rounded border px-2 py-1.5 text-left text-xs ${
              c.conv_id === convId
                ? 'border-slate-600 bg-slate-800'
                : 'border-slate-800 bg-slate-950/60 hover:bg-slate-800/60'
            }`}
          >
            <div className="truncate text-slate-200">{c.title}</div>
            <div className="mt-0.5 flex items-center gap-1">
              <Pill label={c.fsm_state} cls={FSM_COLORS[c.fsm_state]} />
              <span className="mono text-[10px] text-slate-600">v{c.version}</span>
            </div>
          </button>
        ))}
      </div>

      {/* Chat + canvas de widgets */}
      <div className="flex flex-1 flex-col overflow-hidden rounded-lg border border-slate-800 bg-slate-900/60">
        <div className="flex items-center justify-between border-b border-slate-800 px-3 py-2">
          <span className="mono text-xs text-slate-500">{convId || t('monitoring.analyst.noConversation')}</span>
          <div className="flex items-center gap-2">
            {fsmState && <Pill label={fsmState} cls={FSM_COLORS[fsmState]} />}
            <span
              className={`h-2 w-2 rounded-full ${connected ? 'bg-emerald-500' : 'bg-slate-600'}`}
              title={connected ? t('monitoring.analyst.connected') : t('monitoring.analyst.disconnected')}
            />
          </div>
        </div>

        <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto p-4">
          {!convId && (
            <p className="mt-20 text-center text-sm text-slate-600">
              {t('monitoring.analyst.emptyState')}
            </p>
          )}
          {timeline.map((item) => {
            if (item.kind === 'message') {
              const isUser = item.role === 'user'
              return (
                <div key={item.seq} className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
                  <div
                    className={`max-w-[78%] rounded-lg px-3 py-2 text-sm ${
                      isUser
                        ? 'bg-cyan-900/70 text-cyan-50'
                        : 'border border-slate-800 bg-slate-800/60 text-slate-200'
                    }`}
                  >
                    {item.text}
                  </div>
                </div>
              )
            }
            if (item.kind === 'status') {
              return (
                <div key={item.seq} className="flex items-center gap-2 py-0.5">
                  <div className="h-px flex-1 bg-slate-800" />
                  <Pill label={item.state} cls={FSM_COLORS[item.state]} />
                  <div className="h-px flex-1 bg-slate-800" />
                </div>
              )
            }
            return (
              <div key={item.seq}>
                {renderWidget(item.widget_type, item.data, progress[item.data?.corr_id])}
              </div>
            )
          })}
        </div>

        <div className="flex gap-2 border-t border-slate-800 p-3">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()}
            disabled={!convId}
            placeholder={convId ? t('monitoring.analyst.inputPlaceholder') : t('monitoring.analyst.inputPlaceholderDisabled')}
            className="flex-1 rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:border-cyan-700 focus:outline-none disabled:opacity-50"
          />
          <button
            onClick={submit}
            disabled={!convId || !draft.trim()}
            className="rounded border border-cyan-700 bg-cyan-950 px-4 py-2 text-sm font-medium text-cyan-300 hover:bg-cyan-900 disabled:opacity-40"
          >
            {t('monitoring.analyst.send')}
          </button>
        </div>
      </div>
    </div>
  )
}
