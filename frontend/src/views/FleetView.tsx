// Vue Flotte — Agent Registry (catalogue), pods live (liveness KV) et état du pool LLM.

import { api } from '../lib/api'
import { usePolling } from '../lib/hooks'
import { useT } from '../lib/i18n'
import type { AgentType, LlmStats, Pod } from '../lib/types'
import { isFresh, Panel, Pill, POD_COLORS, timeAgo } from '../lib/ui'

export function FleetView() {
  const t = useT()
  const agents = (usePolling(api.agentTypes, 5000) || []) as AgentType[]
  const pods = (usePolling(api.pods, 1500) || []) as Pod[]
  const llm = (usePolling(api.llm, 1500) || {}) as LlmStats

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
      {/* Catalogue de l'Agent Registry */}
      <div className="lg:col-span-2">
        <Panel title={t('monitoring.fleet.registryTitle')}>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
            {agents.map((a) => {
              const live = pods.filter((p) => p.agent_id === a.id)
              return (
                <div key={a.id} className="rounded-md border border-slate-200 dark:border-ink-600 bg-slate-50 dark:bg-ink-700 p-3">
                  <div className="flex items-center justify-between">
                    <span className="mono text-sm font-semibold text-slate-800 dark:text-slate-100">{a.discipline}</span>
                    <Pill label={t('monitoring.fleet.pods', { count: live.length })} />
                  </div>
                  <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">{a.description}</p>
                  <p className="mt-2 text-[11px] italic text-slate-400 dark:text-slate-500">« {a.when_to_use} »</p>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {a.tools.map((t) => (
                      <span key={t} className="mono rounded bg-navy-50 dark:bg-navy-500/15 px-1 py-0.5 text-[10px] text-slate-500 dark:text-slate-400">
                        {t}
                      </span>
                    ))}
                  </div>
                  <div className="mono mt-2 text-[10px] text-slate-400 dark:text-slate-500">
                    {t('monitoring.fleet.cost', { cost: a.cost_hint, p95: a.latency_p95_s, timeout: a.hard_timeout_s })}
                  </div>
                </div>
              )
            })}
            {agents.length === 0 && <Empty />}
          </div>
        </Panel>
      </div>

      {/* Pool LLM */}
      <Panel title={t('monitoring.fleet.llmTitle')}>
        {llm.error ? (
          <p className="text-xs text-red-400">{t('monitoring.fleet.poolUnavailable')}</p>
        ) : (
          <div className="space-y-2 text-xs">
            <Row label={t('monitoring.fleet.slots')} value={llm.slots ?? '—'} />
            <Row label={t('monitoring.fleet.queueHigh')} value={llm.queue_high ?? '—'} />
            <Row label={t('monitoring.fleet.queueLow')} value={llm.queue_low ?? '—'} />
            <Row label={t('monitoring.fleet.inFlight')} value={llm.in_flight ?? '—'} />
            <div className="h-px bg-slate-200 dark:bg-ink-600" />
            <Row label={t('monitoring.fleet.servedHigh')} value={llm.served_high ?? '—'} />
            <Row label={t('monitoring.fleet.servedLow')} value={llm.served_low ?? '—'} />
          </div>
        )}
      </Panel>

      {/* Pods live */}
      <div className="lg:col-span-3">
        <Panel title={t('monitoring.fleet.podsTitle')}>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4 lg:grid-cols-6">
            {pods.map((p) => {
              const fresh = isFresh(p.ts, 12)
              return (
                <div
                  key={`${p.agent_id}.${p.worker_id}`}
                  className={`rounded-md border bg-slate-50 dark:bg-ink-700 p-2 ${
                    fresh ? 'border-slate-200 dark:border-ink-600' : 'border-red-200 dark:border-red-500/30'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="mono text-[11px] font-semibold text-slate-800 dark:text-slate-100">{p.agent_id}</span>
                    <Pill label={p.status} cls={POD_COLORS[p.status]} />
                  </div>
                  <div className="mono mt-1 truncate text-[10px] text-slate-400 dark:text-slate-500">{p.worker_id}</div>
                  <div className="mono mt-1 text-[10px] text-slate-400 dark:text-slate-500">
                    {p.current_corr ? `▸ ${p.current_corr.slice(0, 8)}` : t('monitoring.fleet.idle')}
                  </div>
                  <div className={`mono mt-1 text-[10px] ${fresh ? 'text-slate-400 dark:text-slate-500' : 'text-red-400 dark:text-red-400'}`}>
                    {t('monitoring.fleet.heartbeat', { ago: timeAgo(p.ts) })}
                  </div>
                </div>
              )
            })}
            {pods.length === 0 && <Empty />}
          </div>
        </Panel>
      </div>
    </div>
  )
}

function Row({ label, value }: { label: string; value: any }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-slate-500 dark:text-slate-400">{label}</span>
      <span className="mono font-semibold text-slate-800 dark:text-slate-100">{value}</span>
    </div>
  )
}

function Empty() {
  const t = useT()
  return <p className="text-xs text-slate-400 dark:text-slate-500">{t('monitoring.fleet.empty')}</p>
}
