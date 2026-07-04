// Vue État FSM — la fenêtre directe sur la machinerie de résilience par conversation :
// état de la machine à états, version (fencing token), event_seq, détenteur du lock.

import { api } from '../lib/api'
import { usePolling } from '../lib/hooks'
import { useT } from '../lib/i18n'
import type { Conversation } from '../lib/types'
import { FSM_COLORS, isFresh, Panel, Pill, timeAgo } from '../lib/ui'

export function ConvStateView() {
  const t = useT()
  const convs = (usePolling(api.conversations, 1500) || []) as Conversation[]

  return (
    <Panel title={t('monitoring.convState.title', { count: convs.length })}>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="text-[10px] uppercase tracking-wide text-slate-400 dark:text-slate-500">
            <tr className="border-b border-slate-200 dark:border-ink-600">
              <th className="py-1.5 pr-2">conv_id</th>
              <th className="pr-2">{t('monitoring.convState.colTitle')}</th>
              <th className="pr-2">{t('monitoring.convState.colFsmState')}</th>
              <th className="pr-2">{t('monitoring.convState.colVersion')}</th>
              <th className="pr-2">event_seq</th>
              <th className="pr-2">{t('monitoring.convState.colPlanSteps')}</th>
              <th className="pr-2">{t('monitoring.convState.colLockHolder')}</th>
              <th>{t('monitoring.convState.colUpdated')}</th>
            </tr>
          </thead>
          <tbody className="text-slate-800 dark:text-slate-100">
            {convs.map((c) => {
              const locked = c.lock_holder && isFresh(c.lock_expires_at, 9999)
                && c.lock_expires_at && new Date(c.lock_expires_at).getTime() > Date.now()
              const steps = c.plan?.steps?.length || 0
              return (
                <tr key={c.conv_id} className="border-b border-slate-100 dark:border-ink-600 hover:bg-slate-50 dark:hover:bg-ink-700">
                  <td className="mono py-1.5 pr-2 text-slate-400 dark:text-slate-500">{c.conv_id.slice(0, 8)}</td>
                  <td className="max-w-[200px] truncate pr-2 text-slate-800 dark:text-slate-100">{c.title}</td>
                  <td className="pr-2">
                    <Pill label={c.fsm_state} cls={FSM_COLORS[c.fsm_state]} />
                  </td>
                  <td className="mono pr-2 text-slate-500 dark:text-slate-400">v{c.version}</td>
                  <td className="mono pr-2 text-slate-500 dark:text-slate-400">{c.event_seq}</td>
                  <td className="mono pr-2 text-slate-400 dark:text-slate-500">
                    {steps > 0 ? t('monitoring.convState.planSteps', { turn: c.plan.turn, count: steps }) : '—'}
                  </td>
                  <td className="mono pr-2">
                    {locked ? (
                      <span className="text-amber-400">
                        🔒 {c.lock_holder?.slice(0, 12)} ({timeAgo(c.lock_expires_at)})
                      </span>
                    ) : (
                      <span className="text-slate-400 dark:text-slate-500">{t('monitoring.convState.free')}</span>
                    )}
                  </td>
                  <td className="mono text-slate-400 dark:text-slate-500">{timeAgo(c.updated_at)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {convs.length === 0 && (
          <p className="py-6 text-center text-xs text-slate-400 dark:text-slate-500">{t('monitoring.convState.empty')}</p>
        )}
      </div>
    </Panel>
  )
}
