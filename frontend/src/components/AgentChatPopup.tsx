// Pop-up de test : discute en direct avec un agent A2A via POST /api/agents/{id}/invoke.
//
// AUCUNE mémoire ni persistance : chaque message est une tâche A2A NEUVE (pas de
// contextId), rien n'est écrit en Postgres et l'orchestrateur n'est pas sollicité.
// Sert à tester un agent isolément. L'historique visible est purement local (état
// React) et disparaît à la fermeture.

import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { useT } from '../lib/i18n'
import { Markdown } from './Markdown'

type Msg = { role: 'user' | 'agent' | 'error'; text: string }

export function AgentChatPopup({ agentId, onClose }: { agentId: string; onClose: () => void }) {
  const t = useT()
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [msgs.length, busy])

  // Échap ferme le pop-up.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const send = async () => {
    const text = draft.trim()
    if (!text || busy) return
    setDraft('')
    setMsgs((m) => [...m, { role: 'user', text }])
    setBusy(true)
    try {
      const res = await api.invokeAgent(agentId, text)
      setMsgs((m) => [...m, { role: 'agent', text: res.reply || t('agentChat.emptyReply') }])
    } catch (e: any) {
      setMsgs((m) => [...m, { role: 'error', text: t('agentChat.callFailed', { error: String(e?.message || e) }) }])
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-navy-900/40 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="card flex h-[80vh] w-full max-w-2xl animate-popIn flex-col overflow-hidden shadow-pop"
        onClick={(e) => e.stopPropagation()}
      >
        {/* En-tête */}
        <div className="flex items-center justify-between gap-2 border-b border-slate-200 px-4 py-2.5 dark:border-ink-600">
          <div className="flex items-center gap-2.5">
            <span className="text-lg">💬</span>
            <div>
              <div className="text-sm font-bold text-navy-700 dark:text-navy-200">{agentId}</div>
              <div className="text-[10px] uppercase tracking-wide text-slate-400 dark:text-slate-500">
                {t('agentChat.subtitle')}
              </div>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 transition hover:text-navy-700 dark:text-slate-500 dark:hover:text-navy-200"
            title={t('agentChat.closeTitle')}
          >
            ✕
          </button>
        </div>

        {/* Fil */}
        <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto p-4">
          {msgs.length === 0 && (
            <p className="mt-8 text-center text-sm text-slate-400 dark:text-slate-500">
              {t('agentChat.emptyLine1')}<br />
              {t('agentChat.emptyLine2')}
            </p>
          )}
          {msgs.map((m, i) => (
            <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div
                className={`max-w-[85%] break-words rounded-2xl px-3.5 py-2 text-sm leading-relaxed ${
                  m.role === 'user'
                    ? 'rounded-br-md bg-navy-700 text-white'
                    : m.role === 'error'
                      ? 'rounded-bl-md border border-red-200 bg-red-50 text-red-700 dark:border-red-500/30 dark:bg-red-500/15 dark:text-red-300'
                      : 'rounded-bl-md border border-slate-200 bg-white text-slate-800 dark:border-ink-600 dark:bg-ink-800 dark:text-slate-100'
                }`}
              >
                {m.role === 'agent' ? <Markdown>{m.text}</Markdown> : m.text}
              </div>
            </div>
          ))}
          {busy && (
            <div className="flex justify-start">
              <div className="flex items-center gap-2 rounded-2xl rounded-bl-md border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-400 dark:border-ink-600 dark:bg-ink-800">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-navy-400" />
                {t('agentChat.thinking')}
              </div>
            </div>
          )}
        </div>

        {/* Saisie */}
        <div className="flex gap-2 border-t border-slate-200 p-2 dark:border-ink-600">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') send() }}
            placeholder={t('agentChat.placeholder')}
            className="field flex-1"
            autoFocus
          />
          <button onClick={send} disabled={!draft.trim() || busy} className="btn-primary px-3">
            {t('agentChat.send')}
          </button>
        </div>
      </div>
    </div>
  )
}
