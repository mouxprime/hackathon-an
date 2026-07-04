// Vue DLQ — messages dead-letter : le signal qu'un message est structurellement intraitable.

import { api } from '../lib/api'
import { usePolling } from '../lib/hooks'
import { useT } from '../lib/i18n'
import type { DlqEntry } from '../lib/types'
import { Panel, Pill } from '../lib/ui'

export function DlqView() {
  const t = useT()
  const entries = (usePolling(api.dlq, 2000) || []) as DlqEntry[]

  return (
    <Panel title={t('monitoring.dlq.title', { count: entries.length })}>
      {entries.length === 0 ? (
        <p className="py-6 text-center text-xs text-slate-400 dark:text-slate-500">
          {t('monitoring.dlq.empty')}
        </p>
      ) : (
        <div className="space-y-2">
          {entries.map((e, i) => (
            <div key={i} className="rounded border border-red-200 dark:border-red-500/30 bg-red-50 dark:bg-red-500/15 p-2">
              <div className="flex items-center gap-2">
                <Pill label="DLQ" cls="text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-500/15 border-red-200 dark:border-red-500/30" />
                <span className="mono text-[11px] text-slate-500 dark:text-slate-400">{e.subject}</span>
              </div>
              <p className="mt-1 text-xs text-red-700 dark:text-red-300">{t('monitoring.dlq.reason', { reason: e.reason })}</p>
              <p className="mono mt-0.5 text-[10px] text-slate-400 dark:text-slate-500">
                {t('monitoring.dlq.origSubject', { subject: e.orig_subject })}
              </p>
              <pre className="mono mt-1 overflow-x-auto rounded bg-slate-50 dark:bg-ink-700 p-1.5 text-[10px] text-slate-400 dark:text-slate-500">
                {e.payload}
              </pre>
            </div>
          ))}
        </div>
      )}
    </Panel>
  )
}
