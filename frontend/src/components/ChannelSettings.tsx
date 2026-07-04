// Pop-up plein écran des réglages d'un salon d'état-major (ouvert depuis ⚙ dans
// l'en-tête du ChannelPanel). Deux sections :
//   1. Prompt système de la cellule (= Channel.description) — oriente la
//      planification ET la réponse d'Hémicycle quand on l'appelle depuis ce salon.
//   2. Mémoire de la cellule (ltm_memory scopée channel_id) — ajout/suppression,
//      même UI que Monitoring › Mémoire, mais filtrée sur ce salon.

import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { usePolling } from '../lib/hooks'
import { useT } from '../lib/i18n'
import { Pill, timeAgo } from '../lib/ui'
import { useToast } from '../lib/toast'
import type { Channel } from '../lib/types'

const KIND_CLS: Record<string, string> = {
  learned_fact: 'text-navy-700 dark:text-navy-200 bg-navy-50 dark:bg-navy-500/15 border-navy-200 dark:border-navy-500/30',
  about_user: 'text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-500/15 border-amber-200 dark:border-amber-500/30',
}
const KINDS = ['learned_fact', 'about_user']

export function ChannelSettings({ channel, onClose }: { channel: Channel; onClose: () => void }) {
  const t = useT()
  const toast = useToast()

  // --- Prompt système ---
  const [prompt, setPrompt] = useState(channel.description || '')
  const [savingPrompt, setSavingPrompt] = useState(false)
  // Resync si la liste polled rafraîchit la description en arrière-plan.
  useEffect(() => { setPrompt(channel.description || '') }, [channel.description])

  const savePrompt = async () => {
    setSavingPrompt(true)
    try {
      await api.updateChannel(channel.id, { description: prompt.trim() })
      toast.success(t('channels.settings.promptSaved'))
    } catch (e: any) {
      toast.error(t('channels.settings.promptSaveFailed'), e?.message || String(e))
    } finally { setSavingPrompt(false) }
  }

  // --- Mémoire de cellule ---
  const [tick, setTick] = useState(0)
  const memories = usePolling(() => api.memory(channel.id), 5000, [tick, channel.id]) || []
  const [content, setContent] = useState('')
  const [kind, setKind] = useState('learned_fact')
  const [busy, setBusy] = useState(false)
  const reload = () => setTick((n) => n + 1)

  const addMemory = async () => {
    if (!content.trim() || busy) return
    setBusy(true)
    try {
      await api.createMemory({ content: content.trim(), kind, channel_id: channel.id })
      setContent('')
      toast.success(t('monitoring.memory.added'))
      reload()
    } catch (e: any) {
      toast.error(t('monitoring.memory.addFailed'), e?.message || String(e))
    } finally { setBusy(false) }
  }

  const removeMemory = async (id: number) => {
    try {
      await api.deleteMemory(id)
      reload()
    } catch (e: any) {
      toast.error(t('monitoring.memory.removeFailed'), e?.message || String(e))
    }
  }

  // Échap ferme la modale.
  useEffect(() => {
    const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onEsc)
    return () => window.removeEventListener('keydown', onEsc)
  }, [onClose])

  return (
    // Modale CENTRÉE : le reste de l'interface reste visible derrière un fond flouté.
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-navy-900/40 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="card flex max-h-[85vh] w-full max-w-2xl animate-popIn flex-col overflow-hidden shadow-pop"
        onClick={(e) => e.stopPropagation()}
      >
        {/* En-tête */}
        <div className="flex items-center gap-2 border-b border-slate-200 dark:border-ink-600 bg-white/70 dark:bg-ink-800/70 px-4 py-3">
          <span className="text-base" aria-hidden>⚙</span>
          <h1 className="text-sm font-bold tracking-tight text-navy-700 dark:text-navy-200">
            {t('channels.settings.title', { name: channel.name })}
          </h1>
          <button
            onClick={onClose}
            className="ml-auto rounded-lg border border-slate-200 dark:border-ink-600 bg-white dark:bg-ink-800 px-3 py-1 text-[12px] font-semibold text-slate-600 dark:text-slate-300 transition hover:bg-slate-50 dark:hover:bg-ink-700"
            title={t('common.close')}
          >
            ✕ {t('common.close')}
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          <div className="space-y-6">
          {/* Prompt système */}
          <section className="space-y-2">
            <div>
              <h2 className="text-base font-bold tracking-tight text-navy-700 dark:text-navy-200">
                {t('channels.settings.promptSection')}
              </h2>
              <p className="text-[12px] text-slate-500 dark:text-slate-400">{t('channels.settings.promptHelp')}</p>
            </div>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={5}
              placeholder={t('channels.promptPlaceholder')}
              className="field w-full resize-y text-[13px] leading-relaxed"
            />
            <button onClick={savePrompt} disabled={savingPrompt} className="btn-primary px-4">
              {savingPrompt ? '…' : t('common.save')}
            </button>
          </section>

          {/* Mémoire de cellule */}
          <section className="space-y-3">
            <div>
              <h2 className="text-base font-bold tracking-tight text-navy-700 dark:text-navy-200">
                {t('channels.settings.memorySection')}
              </h2>
              <p className="text-[12px] text-slate-500 dark:text-slate-400">
                {t('channels.settings.memoryHelp', { count: memories.length })}
              </p>
            </div>

            {/* Ajout */}
            <div className="card flex flex-col gap-2 p-3 sm:flex-row sm:items-center">
              <select value={kind} onChange={(e) => setKind(e.target.value)} className="field w-full sm:w-40">
                {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
              </select>
              <input
                value={content}
                onChange={(e) => setContent(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') addMemory() }}
                placeholder={t('monitoring.memory.placeholder')}
                className="field flex-1"
              />
              <button onClick={addMemory} disabled={!content.trim() || busy} className="btn-primary px-4">
                {busy ? '…' : t('monitoring.memory.add')}
              </button>
            </div>

            {/* Liste */}
            <div className="space-y-1.5">
              {memories.length === 0 && (
                <p className="text-[12px] text-slate-400 dark:text-slate-500">{t('channels.settings.memoryEmpty')}</p>
              )}
              {memories.map((m) => (
                <div key={m.id} className="card group flex items-start gap-2 p-2.5">
                  <Pill label={m.kind} cls={KIND_CLS[m.kind] || 'text-slate-600 dark:text-slate-300 bg-slate-100 dark:bg-ink-700 border-slate-300 dark:border-ink-600'} />
                  <span className="min-w-0 flex-1 text-[13px] text-slate-700 dark:text-slate-200">{m.content}</span>
                  <span className="mono shrink-0 text-[10px] text-slate-400 dark:text-slate-500" title={m.created_at}>{timeAgo(m.created_at)}</span>
                  <button
                    onClick={() => removeMemory(m.id)}
                    className="shrink-0 rounded px-1.5 py-0.5 text-[11px] text-slate-300 transition hover:bg-red-50 dark:hover:bg-red-500/15 hover:text-red-600 dark:hover:text-red-400 group-hover:text-slate-400 dark:group-hover:text-slate-500"
                    title={t('monitoring.memory.removeTitle')}
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          </section>
          </div>
        </div>
      </div>
    </div>
  )
}
