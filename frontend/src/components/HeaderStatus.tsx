// Panneau d'état à droite du header global (header navy → texte clair).
//
// Distingue clairement deux familles :
//   - À GAUCHE — les AGENTS (Agent Registry) : icône d'agent + compteur « up/total ».
//     Au survol, popover (carte blanche) listant chaque agent (transport, pods/A2A, état).
//   - À DROITE — les SERVICES de la stack (infra + core) : pastille verte si tout
//     est up, sinon pills rouges des services fautifs ; popover détaillé au survol.
//   - L'heure UTC reste à l'extrémité droite.

import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useT } from '../lib/i18n'
import type { ServiceHealth } from '../lib/types'
import { ThemeToggle } from './ThemeToggle'

// Heure UTC formatée HH:MM:SS — pas de dépendance à une lib date.
function useUtcTime(): string {
  const [t, setT] = useState(() => fmtUtc(new Date()))
  useEffect(() => {
    const id = setInterval(() => setT(fmtUtc(new Date())), 1000)
    return () => clearInterval(id)
  }, [])
  return t
}

function fmtUtc(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`
}

// Petite icône « agent » (puce / cœur de traitement).
function AgentIcon({ className = '' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.6">
      <rect x="7" y="8" width="10" height="9" rx="2" />
      <circle cx="10" cy="12.5" r="1" fill="currentColor" stroke="none" />
      <circle cx="14" cy="12.5" r="1" fill="currentColor" stroke="none" />
      <path d="M12 8V5M9 5h6M5 11v3M19 11v3" strokeLinecap="round" />
    </svg>
  )
}

export function HeaderStatus() {
  const t = useT()
  const utc = useUtcTime()
  const [services, setServices] = useState<ServiceHealth[] | null>(null)
  const [agentsHover, setAgentsHover] = useState(false)
  const [svcHover, setSvcHover] = useState(false)

  // Poll dédié — le gateway cache 3 s côté serveur, on peut tirer toutes les 5 s.
  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const data = await api.healthServices()
        if (alive) setServices(data)
      } catch {
        if (alive) setServices(null)
      }
    }
    tick()
    const id = setInterval(tick, 5000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  const agents = services?.filter((s) => s.category === 'agents') ?? []
  const stack = services?.filter((s) => s.category !== 'agents') ?? []
  const agentsUp = agents.filter((a) => a.status === 'up').length
  const stackDown = stack.filter((s) => s.status === 'down')

  return (
    <div className="flex items-center gap-3">
      {/* --- Bascule clair / nuit (à gauche des agents) --- */}
      <ThemeToggle />

      <span className="h-4 w-px bg-white/20" />

      {/* --- AGENTS (gauche) --- */}
      <div
        className="relative flex items-center"
        onMouseEnter={() => setAgentsHover(true)}
        onMouseLeave={() => setAgentsHover(false)}
      >
        {services === null ? (
          <span className="mono text-[10px] text-navy-300">…</span>
        ) : (
          <div className="flex items-center gap-1.5" title={t('header.registryAgents')}>
            <AgentIcon className="h-4 w-4 text-navy-100" />
            <span className="mono text-[11px] text-white">
              {agentsUp}/{agents.length} <span className="text-navy-300">{t('header.agents')}</span>
            </span>
          </div>
        )}
        {agentsHover && agents.length > 0 && (
          <div className="card absolute left-0 top-full z-50 mt-2 w-60 p-2 shadow-pop">
            <h3 className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              {t('header.availableAgents')}
            </h3>
            <ul className="space-y-0.5">
              {agents.map((a) => (
                <li key={a.name} className="flex items-center justify-between text-[11px]">
                  <div className="flex items-center gap-1.5">
                    <span className={`h-1.5 w-1.5 rounded-full ${a.status === 'up' ? 'bg-emerald-500' : 'bg-red-500'}`} />
                    <span className="mono text-slate-800 dark:text-slate-100">{a.name}</span>
                  </div>
                  <span className="mono text-[10px] text-slate-400 dark:text-slate-500" title={a.detail}>
                    {a.transport === 'a2a' ? 'A2A' : `${a.pods ?? 0} pod(s)`}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <span className="h-4 w-px bg-white/20" />

      {/* --- SERVICES (droite) --- */}
      <div
        className="relative flex items-center"
        onMouseEnter={() => setSvcHover(true)}
        onMouseLeave={() => setSvcHover(false)}
      >
        {services === null && <span className="mono text-[10px] text-navy-300">…</span>}
        {services !== null && stackDown.length === 0 && (
          <div className="flex items-center gap-1.5" title={t('header.allServicesUp')}>
            <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_6px_#34d399]" />
            <span className="mono text-[10px] text-navy-200">{stack.length}/{stack.length} ↑</span>
          </div>
        )}
        {services !== null && stackDown.length > 0 && (
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-red-400 shadow-[0_0_6px_#f87171]" />
            <div className="flex flex-wrap items-center gap-1">
              {stackDown.map((s) => (
                <span
                  key={s.name}
                  className="mono rounded border border-red-400/50 bg-red-500/15 px-1.5 py-0.5 text-[10px] text-red-100"
                  title={s.detail || t('header.unavailable', { name: s.name })}
                >
                  {s.name} ↓
                </span>
              ))}
            </div>
          </div>
        )}
        {svcHover && services !== null && (
          <div className="card absolute right-0 top-full z-50 mt-2 w-56 p-2 shadow-pop">
            <h3 className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              {t('header.services')}
            </h3>
            <ul className="space-y-0.5">
              {stack.map((s) => (
                <li key={s.name} className="flex items-center justify-between text-[11px]">
                  <div className="flex items-center gap-1.5">
                    <span className={`h-1.5 w-1.5 rounded-full ${s.status === 'up' ? 'bg-emerald-500' : 'bg-red-500'}`} />
                    <span className="text-slate-800 dark:text-slate-100">{s.name}</span>
                    <span className="mono text-[9px] text-slate-400 dark:text-slate-500">{s.category}</span>
                  </div>
                  <span
                    className={`mono text-[10px] ${s.status === 'up' ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}`}
                    title={s.detail}
                  >
                    {s.status}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <span className="h-4 w-px bg-white/20" />

      {/* Heure UTC — toujours visible */}
      <div className="mono flex items-baseline gap-1.5">
        <span className="text-[10px] uppercase tracking-wider text-navy-300">{t('header.utc')}</span>
        <span className="text-[12px] text-white">{utc}</span>
      </div>
    </div>
  )
}
