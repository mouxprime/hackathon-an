// Discussion directe avec un agent A2A, RENDUE DANS le panneau de chat (pas une
// modale). Sélectionnée via /agents → on « étend » le chat avec le nom de l'agent.
//
// Ne passe PAS par l'orchestrateur : chaque tour est un POST /api/agents/{id}/invoke.
// Un `context_id` est généré par session et renvoyé à chaque tour → l'agent peut
// enchaîner les échanges (mémoire portée par l'agent, pas par Postgres). Le fil
// visible est purement local et disparaît quand on quitte le mode direct.

import type React from 'react'
import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { useT } from '../lib/i18n'
import { agentEmoji } from '../lib/ui'
import { Markdown } from './Markdown'

type Msg = { role: 'user' | 'agent' | 'error'; text: string }

export function DirectAgentChat({ agentId, discipline, onExit }: {
  agentId: string
  discipline?: string
  onExit: () => void
}) {
  const t = useT()
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  // contextId de session : enchaîne les tours côté agent.
  const ctxId = useRef<string>(crypto.randomUUID())
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { inputRef.current?.focus() }, [])
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [msgs.length, busy])

  // Envoie un tour à l'agent et empile la réponse (ou l'erreur) localement.
  const send = async () => {
    const text = draft.trim()
    if (!text || busy) return
    setDraft('')
    setMsgs((m) => [...m, { role: 'user', text }])
    setBusy(true)
    try {
      const res = await api.invokeAgent(agentId, text, ctxId.current)
      setMsgs((m) => [...m, { role: 'agent', text: res.reply || t('agentChat.emptyReply') }])
    } catch (e: any) {
      setMsgs((m) => [...m, { role: 'error', text: t('agentChat.callFailed', { error: String(e?.message || e) }) }])
    } finally {
      setBusy(false)
    }
  }

  // Entrée envoie ; Échap (input vide) quitte le mode direct → retour au chat.
  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') { e.preventDefault(); send() }
    else if (e.key === 'Escape' && !draft) { e.preventDefault(); onExit() }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-navy-300 dark:border-navy-500/40 bg-cream dark:bg-ink-800 shadow-card">
      {/* En-tête : retour + nom de l'agent + badge A2A. */}
      <div className="flex items-center justify-between gap-2 border-b border-navy-200 dark:border-navy-500/30 bg-navy-50/70 dark:bg-navy-500/10 px-3 py-2">
        <button
          onClick={onExit}
          className="text-[11px] font-medium text-navy-600 dark:text-navy-300 transition hover:text-navy-800 dark:hover:text-navy-100"
          title={t('chat.directExit')}
        >
          ◀ {t('chat.directExit')}
        </button>
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="text-sm">{agentEmoji(discipline || '')}</span>
          <span className="mono truncate text-[12px] font-bold text-navy-700 dark:text-navy-200">{agentId}</span>
        </div>
        <span className="mono shrink-0 rounded border border-navy-200 dark:border-navy-500/30 bg-white/70 dark:bg-ink-800 px-1 py-0.5 text-[9px] uppercase tracking-wide text-navy-500 dark:text-navy-300">
          A2A
        </span>
      </div>

      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto p-3">
        {msgs.length === 0 && (
          <div className="mt-10 space-y-1.5 text-center">
            <p className="text-sm font-medium text-slate-600 dark:text-slate-300">{t('agentChat.emptyLine1')}</p>
            <p className="text-[11px] text-slate-400 dark:text-slate-500">{t('chat.directHint')}</p>
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`flex animate-fadeInUp ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div
              className={`max-w-[85%] break-words rounded-2xl px-3.5 py-2 text-sm leading-relaxed ${
                m.role === 'user'
                  ? 'rounded-br-md bg-navy-700 text-white shadow-sm'
                  : m.role === 'error'
                    ? 'rounded-bl-md border border-red-200 bg-red-50 text-red-700 dark:border-red-500/30 dark:bg-red-500/15 dark:text-red-300'
                    : 'rounded-bl-md border border-slate-200 bg-white text-slate-800 shadow-xs dark:border-ink-600 dark:bg-ink-800 dark:text-slate-100'
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

      <div className="flex gap-2 border-t border-navy-200 p-2 dark:border-navy-500/30">
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={t('agentChat.placeholder')}
          className="field flex-1"
        />
        <button
          onClick={send}
          disabled={!draft.trim() || busy}
          className="btn-primary flex items-center justify-center px-3"
          aria-label={t('agentChat.send')}
          title={t('agentChat.send')}
        >
          <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor"
               strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
            <path d="M22 2 11 13" />
            <path d="M22 2 15 22l-4-9-9-4 20-7z" />
          </svg>
        </button>
      </div>
    </div>
  )
}
