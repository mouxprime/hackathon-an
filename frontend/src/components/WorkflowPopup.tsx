// Popup « Workflow » — liste des workflows pré-définis + accès au constructeur.
//
// Ouvert depuis le bouton Workflow au-dessus de l'input. Chaque workflow peut être
// **Utilisé** (pré-sélectionné pour la prochaine requête : l'orchestrateur proposera
// sa chaîne d'agents) ou supprimé. Le bouton **+ Créer un workflow** renvoie vers la
// page /workflows (gestion complète).

import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { Workflow } from '../lib/types'
import { useToast } from '../lib/toast'
import { useT } from '../lib/i18n'

type Props = {
  onClose: () => void
  onUse: (wf: Workflow) => void
  onCreateNew: () => void
}

export function WorkflowPopup({ onClose, onUse, onCreateNew }: Props) {
  const t = useT()
  const toast = useToast()
  const [workflows, setWorkflows] = useState<Workflow[] | null>(null)

  const load = () => api.workflows().then(setWorkflows).catch(() => setWorkflows([]))
  useEffect(() => { load() }, [])

  const remove = async (wf: Workflow) => {
    if (!confirm(t('workflows.list.deleteConfirm', { name: wf.name }))) return
    try {
      await api.deleteWorkflow(wf.id)
      toast.success(t('workflows.list.deleted'))
      load()
    } catch (e: any) {
      toast.error(t('workflows.list.deleteError', { error: e?.message || e }))
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center" onClick={onClose}>
      <div className="absolute inset-0 bg-navy-900/30 backdrop-blur-sm" />
      <div
        className="card relative flex max-h-[70vh] w-[520px] animate-popIn flex-col overflow-hidden shadow-pop"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex shrink-0 items-center justify-between border-b border-slate-200 dark:border-ink-600 px-4 py-2.5">
          <h2 className="text-sm font-bold uppercase tracking-wide text-navy-700 dark:text-navy-200">{t('workflows.popup.title')}</h2>
          <button onClick={onClose} className="text-sm text-slate-400 dark:text-slate-500 transition hover:text-navy-700 dark:hover:text-navy-200">✕</button>
        </div>

        <div className="flex-1 space-y-2 overflow-y-auto p-3">
          {workflows === null && <p className="text-xs text-slate-500 dark:text-slate-400">{t('workflows.list.loading')}</p>}
          {workflows !== null && workflows.length === 0 && (
            <p className="text-xs text-slate-500 dark:text-slate-400">
              {t('workflows.popup.empty')}
            </p>
          )}
          {workflows?.map((wf) => (
            <div key={wf.id} className="rounded-xl border border-slate-200 dark:border-ink-600 bg-slate-50 dark:bg-ink-700 p-3 transition hover:border-navy-200 dark:hover:border-navy-500/30">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate font-semibold text-slate-800 dark:text-slate-100">{wf.name}</div>
                  {wf.description && (
                    <div className="mt-0.5 line-clamp-2 text-[11px] text-slate-500 dark:text-slate-400">{wf.description}</div>
                  )}
                  <div className="mono mt-1 flex flex-wrap gap-1 text-[10px] font-semibold text-navy-700 dark:text-navy-200">
                    {wf.steps.map((s, i) => (
                      <span key={i}>
                        {i > 0 && <span className="text-slate-400 dark:text-slate-500"> → </span>}
                        {s.agent_type.toUpperCase()}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  <button
                    onClick={() => { onUse(wf); onClose() }}
                    className="rounded-lg bg-navy-700 px-2.5 py-1 text-[11px] font-semibold text-white transition hover:bg-navy-800"
                  >
                    {t('workflows.popup.use')}
                  </button>
                  <button
                    onClick={() => remove(wf)}
                    className="rounded-lg border border-slate-300 dark:border-ink-600 px-2 py-1 text-[11px] text-slate-400 dark:text-slate-500 transition hover:border-red-300 hover:text-red-600"
                    title={t('workflows.list.delete')}
                  >
                    ✕
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>

        <div className="shrink-0 border-t border-slate-200 dark:border-ink-600 p-3">
          <button
            onClick={() => { onCreateNew(); onClose() }}
            className="w-full rounded-xl border border-dashed border-navy-300 bg-navy-50 dark:bg-navy-500/15 py-2 text-sm font-semibold text-navy-700 dark:text-navy-200 transition hover:bg-navy-100"
          >
            {t('workflows.list.create')}
          </button>
        </div>
      </div>
    </div>
  )
}
