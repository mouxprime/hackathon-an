// Vue Tâches — inspecteur live de la table `tasks` : corrélation, lease, retries, outbox.

import { api } from '../lib/api'
import { usePolling } from '../lib/hooks'
import { useT } from '../lib/i18n'
import type { Task } from '../lib/types'
import { Bar, isFresh, Panel, Pill, STATUS_COLORS, timeAgo } from '../lib/ui'

export function TasksView() {
  const t = useT()
  const tasks = (usePolling(api.tasks, 1500) || []) as Task[]

  return (
    <Panel title={t('monitoring.tasks.title', { count: tasks.length })}>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="text-[10px] uppercase tracking-wide text-slate-400 dark:text-slate-500">
            <tr className="border-b border-slate-200 dark:border-ink-600">
              <th className="py-1.5 pr-2">corr_id</th>
              <th className="pr-2">agent</th>
              <th className="pr-2">{t('monitoring.tasks.colStatus')}</th>
              <th className="pr-2">{t('monitoring.tasks.colProgress')}</th>
              <th className="pr-2">{t('monitoring.tasks.colAttempts')}</th>
              <th className="pr-2">worker</th>
              <th className="pr-2">lease</th>
              <th className="pr-2">outbox</th>
              <th>conv</th>
            </tr>
          </thead>
          <tbody className="text-slate-800 dark:text-slate-100">
            {tasks.map((task) => {
              const leaseOk = isFresh(task.lease_until, 9999) && task.lease_until
                ? new Date(task.lease_until).getTime() > Date.now()
                : false
              const active = ['pending', 'running', 'retrying'].includes(task.status)
              return (
                <tr key={task.corr_id} className="border-b border-slate-100 dark:border-ink-600 hover:bg-slate-50 dark:hover:bg-ink-700">
                  <td className="mono py-1.5 pr-2 text-slate-400 dark:text-slate-500">{task.corr_id.slice(0, 8)}</td>
                  <td className="mono pr-2 uppercase text-slate-800 dark:text-slate-100">{task.agent_type}</td>
                  <td className="pr-2">
                    <Pill label={task.status} cls={STATUS_COLORS[task.status]} />
                  </td>
                  <td className="w-32 pr-2">
                    <Bar pct={task.progress_pct} cls={task.status === 'done' ? 'bg-emerald-500' : 'bg-cyan-500'} />
                    <span className="mono text-[10px] text-slate-400 dark:text-slate-500">{task.progress_note}</span>
                  </td>
                  <td className="mono pr-2 text-slate-500 dark:text-slate-400">{task.attempt_count}</td>
                  <td className="mono pr-2 text-slate-400 dark:text-slate-500">{task.worker_id?.slice(0, 12) || '—'}</td>
                  <td className="mono pr-2">
                    {active ? (
                      <span className={leaseOk ? 'text-emerald-400' : 'text-red-400'}>
                        {leaseOk ? t('monitoring.tasks.leaseOk', { ago: timeAgo(task.lease_until) }) : t('monitoring.tasks.leaseExpired')}
                      </span>
                    ) : (
                      <span className="text-slate-400 dark:text-slate-500">—</span>
                    )}
                  </td>
                  <td className="mono pr-2">
                    {task.published_at ? (
                      <span className="text-slate-400 dark:text-slate-500">{t('monitoring.tasks.published')}</span>
                    ) : (
                      <span className="text-amber-400">{t('monitoring.tasks.waiting')}</span>
                    )}
                  </td>
                  <td className="mono text-slate-400 dark:text-slate-500">{task.conv_id.slice(0, 8)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {tasks.length === 0 && (
          <p className="py-6 text-center text-xs text-slate-400 dark:text-slate-500">{t('monitoring.tasks.empty')}</p>
        )}
      </div>
    </Panel>
  )
}
