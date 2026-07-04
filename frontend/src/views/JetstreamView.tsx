// Monitoring JetStream — vue dédiée aux streams + consumers + peek messages.
//
// Polling 2s. Sparklines : on garde un anneau des 30 derniers échantillons de
// `messages` par stream pour visualiser le rythme d'écriture.

import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { usePolling } from '../lib/hooks'
import { useT } from '../lib/i18n'
import type { JetstreamConsumer, JetstreamMessage, JetstreamStream } from '../lib/types'
import { Pill } from '../lib/ui'

function Sparkline({ values, max }: { values: number[]; max: number }) {
  if (values.length === 0) return null
  const width = 80, height = 16
  const safeMax = Math.max(max, 1)
  const points = values.map((v, i) => {
    const x = (i / Math.max(values.length - 1, 1)) * width
    const y = height - (v / safeMax) * height
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  return (
    <svg width={width} height={height} className="inline-block align-middle">
      <polyline fill="none" className="stroke-navy-500 dark:stroke-navy-300" strokeWidth="1.2" points={points} />
    </svg>
  )
}

export function JetstreamView() {
  const t = useT()
  const streams = usePolling(api.streams, 2000) || []
  const [selected, setSelected] = useState<string | null>(null)
  const [consumers, setConsumers] = useState<JetstreamConsumer[]>([])
  const [messages, setMessages] = useState<JetstreamMessage[]>([])
  const histRef = useRef<Record<string, number[]>>({})

  // Met à jour les sparklines à chaque tick.
  useEffect(() => {
    for (const s of streams) {
      const h = histRef.current[s.name] || []
      h.push(s.messages)
      if (h.length > 30) h.shift()
      histRef.current[s.name] = h
    }
  }, [streams])

  // Charge consumers + messages quand on clique sur un stream.
  useEffect(() => {
    if (!selected) return
    let alive = true
    const load = async () => {
      try {
        const [c, m] = await Promise.all([api.consumers(selected), api.streamMessages(selected, 12)])
        if (alive) { setConsumers(c); setMessages(m) }
      } catch {}
    }
    load()
    const id = setInterval(load, 2000)
    return () => { alive = false; clearInterval(id) }
  }, [selected])

  return (
    <div className="grid h-full grid-cols-2 gap-3 p-4">
      {/* Streams */}
      <div className="flex min-h-0 flex-col overflow-hidden rounded-xl border border-slate-200 dark:border-ink-600 bg-white dark:bg-ink-800 shadow-card">
        <div className="border-b border-slate-200 dark:border-ink-600 px-3 py-2">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
            {t('monitoring.jetstream.streamsTitle')}
          </h2>
          <p className="text-[10px] text-slate-400 dark:text-slate-500">{t('monitoring.jetstream.streamsHint')}</p>
        </div>
        <div className="flex-1 overflow-y-auto">
          <table className="w-full text-left text-xs">
            <thead className="sticky top-0 bg-slate-50 dark:bg-ink-700 text-[10px] uppercase text-slate-400 dark:text-slate-500">
              <tr>
                <th className="px-3 py-1.5">{t('monitoring.jetstream.colStream')}</th>
                <th className="px-2 py-1.5">Msgs</th>
                <th className="px-2 py-1.5">Bytes</th>
                <th className="px-2 py-1.5">Seq</th>
                <th className="px-2 py-1.5">Cons.</th>
                <th className="px-2 py-1.5">{t('monitoring.jetstream.colRate')}</th>
              </tr>
            </thead>
            <tbody>
              {streams.map((s: JetstreamStream) => {
                const isSel = selected === s.name
                const hist = histRef.current[s.name] || []
                const max = Math.max(...hist, 1)
                return (
                  <tr
                    key={s.name}
                    onClick={() => setSelected(s.name)}
                    className={`cursor-pointer border-t border-slate-100 dark:border-ink-600 hover:bg-slate-50 dark:hover:bg-ink-700 ${
                      isSel ? 'bg-navy-50 dark:bg-navy-500/15' : ''
                    }`}
                  >
                    <td className="px-3 py-1.5">
                      <div className="mono font-semibold text-navy-700 dark:text-navy-200">{s.name}</div>
                      <div className="mono text-[10px] text-slate-400 dark:text-slate-500">{s.subjects.join(' ')}</div>
                    </td>
                    <td className="mono px-2 py-1.5 text-slate-800 dark:text-slate-100">{s.messages}</td>
                    <td className="mono px-2 py-1.5 text-slate-400 dark:text-slate-500">{Math.round(s.bytes / 1024)} K</td>
                    <td className="mono px-2 py-1.5 text-slate-400 dark:text-slate-500">
                      {s.first_seq}–{s.last_seq}
                    </td>
                    <td className="mono px-2 py-1.5 text-slate-800 dark:text-slate-100">{s.consumer_count}</td>
                    <td className="px-2 py-1.5">
                      <Sparkline values={hist} max={max} />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Détail */}
      <div className="flex min-h-0 flex-col gap-3">
        {/* Consumers */}
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-slate-200 dark:border-ink-600 bg-white dark:bg-ink-800 shadow-card">
          <div className="border-b border-slate-200 dark:border-ink-600 px-3 py-2">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              {t('monitoring.jetstream.consumersTitle')} {selected && <span className="mono text-navy-700 dark:text-navy-200">{selected}</span>}
            </h2>
          </div>
          <div className="flex-1 overflow-y-auto p-2">
            {!selected && (
              <p className="mt-6 text-center text-xs text-slate-400 dark:text-slate-500">{t('monitoring.jetstream.selectStream')}</p>
            )}
            {consumers.map((c) => (
              <div key={c.name} className="mb-2 rounded border border-slate-200 dark:border-ink-600 bg-slate-50 dark:bg-ink-700 p-2 text-xs">
                <div className="flex items-center justify-between">
                  <span className="mono font-semibold text-slate-800 dark:text-slate-100">{c.name}</span>
                  <div className="flex gap-1">
                    {c.num_pending > 0 && <Pill label={`pending=${c.num_pending}`} cls="text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-500/15 border-amber-200 dark:border-amber-500/30" />}
                    {c.num_ack_pending > 0 && <Pill label={`ack=${c.num_ack_pending}`} cls="text-navy-700 dark:text-navy-200 bg-navy-50 dark:bg-navy-500/15 border-navy-200 dark:border-navy-500/30" />}
                    {c.num_redelivered > 0 && <Pill label={`redeliver=${c.num_redelivered}`} cls="text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-500/15 border-red-200 dark:border-red-500/30" />}
                  </div>
                </div>
                <div className="mono mt-1 text-[10px] text-slate-400 dark:text-slate-500">
                  delivered: {c.delivered_consumer_seq} / ack_floor: {c.ack_floor_stream_seq}
                  {c.filter_subject && ` · subject: ${c.filter_subject}`}
                  {c.ack_wait && ` · ack_wait=${c.ack_wait}s · max_deliver=${c.max_deliver}`}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Messages */}
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-slate-200 dark:border-ink-600 bg-white dark:bg-ink-800 shadow-card">
          <div className="border-b border-slate-200 dark:border-ink-600 px-3 py-2">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              {t('monitoring.jetstream.lastMessages')}
            </h2>
          </div>
          <div className="flex-1 overflow-y-auto p-2">
            {!selected && (
              <p className="mt-6 text-center text-xs text-slate-400 dark:text-slate-500">{t('monitoring.jetstream.selectStream')}</p>
            )}
            {messages.slice().reverse().map((m) => (
              <div key={m.seq} className="mb-1.5 rounded border border-slate-200 dark:border-ink-600 bg-slate-50 dark:bg-ink-700 p-2 text-[10px]">
                <div className="mono flex items-center justify-between text-slate-500 dark:text-slate-400">
                  <span>#{m.seq}</span>
                  <span className="text-slate-400 dark:text-slate-500">{m.subject}</span>
                </div>
                <pre className="mono mt-1 max-h-24 overflow-auto text-[10px] text-slate-400 dark:text-slate-500">
                  {typeof m.payload === 'string' ? m.payload : JSON.stringify(m.payload).slice(0, 300)}
                </pre>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
