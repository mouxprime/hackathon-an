// Monitoring › Historique — vue lecture des conversations passées.
//
// Liste détaillée (titre, état FSM, dernier message, seq d'événements, lock courant).
// La suppression / sélection se fait depuis le panneau latéral du dashboard ; ici
// on observe l'état d'orchestration de chaque investigation.

import { api } from '../lib/api'
import { usePolling } from '../lib/hooks'
import { useT } from '../lib/i18n'
import { FSM_COLORS, Pill, timeAgo } from '../lib/ui'

export function ConversationsMonitorView() {
  const t = useT()
  const convs = usePolling(api.conversations, 3000) || []

  return (
    <div className="mx-auto max-w-5xl space-y-3">
      <div>
        <h2 className="text-base font-bold tracking-tight text-navy-700 dark:text-navy-200">
          {t('monitoring.conversations.title')}
        </h2>
        <p className="text-[12px] text-slate-500 dark:text-slate-400">
          {t('monitoring.conversations.subtitle', { count: convs.length })}
        </p>
      </div>

      <div className="card overflow-hidden">
        <table className="w-full text-left text-[12px]">
          <thead className="border-b border-slate-200 dark:border-ink-600 bg-slate-50 dark:bg-ink-700 text-[10px] uppercase tracking-wide text-slate-400 dark:text-slate-500">
            <tr>
              <th className="px-3 py-2 font-semibold">{t('monitoring.conversations.colInvestigation')}</th>
              <th className="px-3 py-2 font-semibold">{t('monitoring.conversations.colState')}</th>
              <th className="px-3 py-2 font-semibold">{t('monitoring.conversations.colActivity')}</th>
              <th className="px-3 py-2 text-right font-semibold">seq</th>
              <th className="px-3 py-2 font-semibold">{t('monitoring.conversations.colLock')}</th>
            </tr>
          </thead>
          <tbody>
            {convs.length === 0 && (
              <tr><td colSpan={5} className="px-3 py-6 text-center text-slate-400 dark:text-slate-500">{t('monitoring.conversations.empty')}</td></tr>
            )}
            {convs.map((c) => (
              <tr key={c.conv_id} className="border-b border-slate-100 dark:border-ink-600 last:border-0 hover:bg-slate-50 dark:hover:bg-ink-700">
                <td className="max-w-[280px] px-3 py-2">
                  <div className="truncate text-slate-800 dark:text-slate-100">{c.title}</div>
                  <div className="mono text-[10px] text-slate-400 dark:text-slate-500">{c.conv_id.slice(0, 8)}</div>
                </td>
                <td className="px-3 py-2">
                  <Pill label={c.fsm_state} cls={FSM_COLORS[c.fsm_state]} />
                </td>
                <td className="px-3 py-2 text-slate-500 dark:text-slate-400" title={c.updated_at}>{timeAgo(c.updated_at)}</td>
                <td className="mono px-3 py-2 text-right text-slate-500 dark:text-slate-400">{c.event_seq}</td>
                <td className="px-3 py-2">
                  {c.lock_holder
                    ? <span className="mono text-[10px] text-amber-600 dark:text-amber-400">{c.lock_holder.slice(0, 10)}</span>
                    : <span className="text-[11px] text-slate-300">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
