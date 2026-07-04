// Page Workflows — gestion des chaînes d'agents réutilisables.
//
// Vraie page routée (/workflows) : hérite du header/logo/nav de l'app shell et
// respecte la structure des autres vues. Liste les workflows enregistrés
// (nom, description, chaîne d'agents) et ouvre le constructeur **inline** pour en
// créer un nouveau. La sélection « pour la prochaine requête » se fait depuis le
// popup du chat ; ici on crée / inspecte / supprime.

import { useEffect, useState } from 'react'
import { WorkflowBuilder } from '../components/WorkflowBuilder'
import { api } from '../lib/api'
import type { Workflow } from '../lib/types'
import { useToast } from '../lib/toast'
import { useT } from '../lib/i18n'

export function WorkflowsView() {
  const t = useT()
  const toast = useToast()
  const [workflows, setWorkflows] = useState<Workflow[] | null>(null)
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<Workflow | null>(null)

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

  // Mode création / édition : le constructeur occupe la page sous le header global.
  if (creating || editing) {
    return (
      <div className="h-full">
        <WorkflowBuilder
          mode="save"
          editWorkflow={editing}
          onClose={() => { setCreating(false); setEditing(null) }}
          onSaved={() => { setCreating(false); setEditing(null); load() }}
        />
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-6 py-7">
        {/* En-tête de page — titre centré + bouton de création centré dessous */}
        <div className="mb-6 text-center">
          <h1 className="text-xl font-bold tracking-tight text-navy-700 dark:text-navy-200">{t('workflows.list.title')}</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            {t('workflows.list.subtitle')}
          </p>
          <div className="mt-4 flex justify-center">
            <button className="btn-primary animate-fadeIn px-6" onClick={() => setCreating(true)}>
              {t('workflows.list.create')}
            </button>
          </div>
        </div>

        {/* Liste */}
        {workflows === null ? (
          <p className="text-sm text-slate-400 dark:text-slate-500">{t('workflows.list.loading')}</p>
        ) : workflows.length === 0 ? (
          <div className="card flex flex-col items-center gap-2 px-6 py-12 text-center">
            <span className="text-3xl">🧩</span>
            <p className="text-sm font-medium text-slate-700 dark:text-slate-200">{t('workflows.list.empty')}</p>
            <p className="text-[12px] text-slate-400 dark:text-slate-500">
              {t('workflows.list.emptyHint')}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {workflows.map((wf) => (
              <div
                key={wf.id}
                onClick={() => setEditing(wf)}
                className="card flex animate-fadeInUp cursor-pointer flex-col p-4 transition hover:-translate-y-0.5 hover:border-navy-200 hover:shadow-md"
                title={t('workflows.list.editHint')}
              >
                <div className="flex items-start justify-between gap-2">
                  <h2 className="truncate font-bold text-slate-800 dark:text-slate-100">{wf.name}</h2>
                  <button
                    onClick={(e) => { e.stopPropagation(); remove(wf) }}
                    className="shrink-0 rounded-lg border border-slate-300 dark:border-ink-600 px-2 py-0.5 text-[11px] text-slate-400 dark:text-slate-500 transition hover:border-red-300 hover:text-red-600"
                    title={t('workflows.list.delete')}
                  >
                    ✕
                  </button>
                </div>
                {wf.description && (
                  <p className="mt-1 line-clamp-2 text-[12px] text-slate-500 dark:text-slate-400">{wf.description}</p>
                )}
                <div className="mt-3 flex flex-wrap items-center gap-1">
                  {wf.steps.map((s, i) => (
                    <span key={i} className="flex items-center gap-1">
                      {i > 0 && <span className="text-slate-300 dark:text-slate-600">→</span>}
                      <span className="chip border-navy-200 dark:border-navy-500/30 bg-navy-50 dark:bg-navy-500/15 text-navy-700 dark:text-navy-300">
                        {s.agent_type.toUpperCase()}
                      </span>
                    </span>
                  ))}
                </div>
                <div className="mt-3 border-t border-slate-100 dark:border-ink-600 pt-2 text-[11px] text-slate-400 dark:text-slate-500">
                  {t('workflows.list.stepsSummary', { count: wf.steps.length })}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
