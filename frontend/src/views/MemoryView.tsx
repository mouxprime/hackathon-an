// Monitoring › Mémoire — fenêtre sur la mémoire interne long-terme (ltm_memory).
//
// Deux kinds : `about_user` (profil/préférences analyste) et `learned_fact` (faits
// du monde). Un fait ajouté ici est ensuite proposé au routeur LLM, qui choisit
// lesquels utiliser via `relevant_memory_ids` (cf. orchestrator/memory.py).

import { useState } from 'react'
import { api } from '../lib/api'
import { usePolling } from '../lib/hooks'
import { useT } from '../lib/i18n'
import { Pill, timeAgo } from '../lib/ui'
import { useToast } from '../lib/toast'

const KIND_CLS: Record<string, string> = {
  learned_fact: 'text-navy-700 dark:text-navy-200 bg-navy-50 dark:bg-navy-500/15 border-navy-200 dark:border-navy-500/30',
  about_user: 'text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-500/15 border-amber-200 dark:border-amber-500/30',
}

const KINDS = ['learned_fact', 'about_user']

export function MemoryView() {
  const t = useT()
  const toast = useToast()
  const [tick, setTick] = useState(0)
  const memories = usePolling(api.memory, 5000, [tick]) || []
  const [content, setContent] = useState('')
  const [kind, setKind] = useState('learned_fact')
  const [busy, setBusy] = useState(false)

  const reload = () => setTick((t) => t + 1)

  const add = async () => {
    if (!content.trim() || busy) return
    setBusy(true)
    try {
      await api.createMemory({ content: content.trim(), kind })
      setContent('')
      toast.success(t('monitoring.memory.added'))
      reload()
    } catch (e: any) {
      toast.error(t('monitoring.memory.addFailed'), e?.message || String(e))
    } finally {
      setBusy(false)
    }
  }

  const remove = async (id: number) => {
    if (!confirm(t('monitoring.memory.confirmRemove'))) return
    try {
      await api.deleteMemory(id)
      reload()
    } catch (e: any) {
      toast.error(t('monitoring.memory.removeFailed'), e?.message || String(e))
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div>
        <h2 className="text-base font-bold tracking-tight text-navy-700 dark:text-navy-200">{t('monitoring.memory.title')}</h2>
        <p className="text-[12px] text-slate-500 dark:text-slate-400">
          {t('monitoring.memory.subtitle', { count: memories.length })}
        </p>
      </div>

      {/* Formulaire d'ajout */}
      <div className="card flex flex-col gap-2 p-3 sm:flex-row sm:items-center">
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value)}
          className="field w-full sm:w-40"
        >
          {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
        </select>
        <input
          value={content}
          onChange={(e) => setContent(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') add() }}
          placeholder={t('monitoring.memory.placeholder')}
          className="field flex-1"
        />
        <button onClick={add} disabled={!content.trim() || busy} className="btn-primary px-4">
          {busy ? '…' : t('monitoring.memory.add')}
        </button>
      </div>

      {/* Liste */}
      <div className="space-y-1.5">
        {memories.length === 0 && (
          <p className="text-[12px] text-slate-400 dark:text-slate-500">{t('monitoring.memory.empty')}</p>
        )}
        {memories.map((m) => (
          <div
            key={m.id}
            className="card group flex items-start gap-2 p-2.5"
          >
            <Pill label={m.kind} cls={KIND_CLS[m.kind] || 'text-slate-600 dark:text-slate-300 bg-slate-100 dark:bg-ink-700 border-slate-300 dark:border-ink-600'} />
            <span className="min-w-0 flex-1 text-[13px] text-slate-700 dark:text-slate-200">{m.content}</span>
            <span className="mono shrink-0 text-[10px] text-slate-400 dark:text-slate-500" title={m.created_at}>
              {timeAgo(m.created_at)}
            </span>
            <button
              onClick={() => remove(m.id)}
              className="shrink-0 rounded px-1.5 py-0.5 text-[11px] text-slate-300 transition hover:bg-red-50 dark:hover:bg-red-500/15 hover:text-red-600 dark:hover:text-red-400 group-hover:text-slate-400 dark:group-hover:text-slate-500"
              title={t('monitoring.memory.removeTitle')}
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
