// Menu inline d'agents (commande /agents) — même look que SlashCommandMenu :
// carte flottante au-dessus de l'input. Navigation ↑/↓, Enter pour ouvrir une
// discussion directe avec l'agent, Esc pour quitter la commande.
// Ne liste que les agents A2A (le chat direct passe par A2A uniquement).

import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { usePolling } from '../lib/hooks'
import { useT } from '../lib/i18n'
import { agentEmoji } from '../lib/ui'
import type { AgentEnriched } from '../lib/types'

type Props = {
  onSelect: (agent: AgentEnriched) => void
  onClose: () => void
}

export function AgentSlashMenu({ onSelect, onClose }: Props) {
  const t = useT()
  const all = (usePolling(api.agents, 5000) as AgentEnriched[] | null) || []
  const agents = all.filter((a) => a.transport === 'a2a')
  const [cursor, setCursor] = useState(0)

  // Recentre le curseur si la liste change (polling).
  useEffect(() => { if (cursor > agents.length - 1) setCursor(0) }, [agents.length, cursor])

  // Navigation clavier captée au niveau window (cf. SlashCommandMenu) pour primer
  // sur la logique de l'input parent pendant que le menu est ouvert.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setCursor((c) => Math.min(c + 1, agents.length - 1))
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setCursor((c) => Math.max(c - 1, 0))
      } else if (e.key === 'Enter' || e.key === 'Tab') {
        if (agents[cursor]) { e.preventDefault(); onSelect(agents[cursor]) }
      } else if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      }
    }
    window.addEventListener('keydown', handler, { capture: true })
    return () => window.removeEventListener('keydown', handler, { capture: true })
  }, [agents, cursor, onSelect, onClose])

  return (
    <div className="absolute bottom-full left-0 right-0 mb-1 z-50 animate-popIn">
      <ul role="listbox" className="card shadow-pop max-h-72 overflow-y-auto py-1">
        <li className="px-3 py-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
          {t('chat.cmd.agents.popupTitle')}
        </li>
        {agents.length === 0 && (
          <li className="px-3 py-3 text-center text-[11px] text-slate-400 dark:text-slate-500">
            {t('chat.cmd.agents.empty')}
          </li>
        )}
        {agents.map((a, i) => (
          <li
            key={a.id}
            role="option"
            aria-selected={i === cursor}
            onMouseEnter={() => setCursor(i)}
            onClick={() => onSelect(a)}
            className={`flex cursor-pointer items-center gap-2.5 px-3 py-1.5 transition ${
              i === cursor ? 'bg-navy-50 dark:bg-navy-500/15' : 'hover:bg-slate-50 dark:hover:bg-ink-700'
            }`}
          >
            <span className="text-base leading-none">{agentEmoji(a.discipline)}</span>
            <span className="mono shrink-0 text-[11px] font-semibold text-navy-700 dark:text-navy-200">
              {a.id}
            </span>
            {a.description && (
              <span className="truncate text-[11px] text-slate-500 dark:text-slate-400">{a.description}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
