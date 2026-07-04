// Picker de conversations pour le déclencheur @ — s'affiche au-dessus de l'input.
// Navigation ↑/↓ + Enter pour sélectionner, Esc pour fermer.
// Suit exactement le même pattern que SlashCommandMenu (capture clavier sur window).

import { useEffect, useState } from 'react'
import type { Conversation } from '../lib/types'

type Props = {
  items: Conversation[]
  onSelect: (conv: Conversation) => void
  onClose: () => void
}

export function ConvMentionPicker({ items, onSelect, onClose }: Props) {
  const [cursor, setCursor] = useState(0)

  // Recentre le curseur si la liste filtrée change.
  useEffect(() => { setCursor(0) }, [items.length])

  // Navigation clavier en phase capture — avant les keydown de l'input parent.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'ArrowDown') {
        e.preventDefault(); setCursor((c) => Math.min(c + 1, items.length - 1))
      } else if (e.key === 'ArrowUp') {
        e.preventDefault(); setCursor((c) => Math.max(c - 1, 0))
      } else if (e.key === 'Enter' || e.key === 'Tab') {
        if (items[cursor]) { e.preventDefault(); onSelect(items[cursor]) }
      } else if (e.key === 'Escape') {
        e.preventDefault(); onClose()
      }
    }
    window.addEventListener('keydown', handler, { capture: true })
    return () => window.removeEventListener('keydown', handler, { capture: true })
  }, [items, cursor, onSelect, onClose])

  if (items.length === 0) return null

  return (
    <div className="absolute bottom-full left-0 right-0 mb-1 z-50 animate-popIn">
      <ul
        role="listbox"
        className="card shadow-pop overflow-hidden py-1 max-h-48 overflow-y-auto"
      >
        {items.map((conv, i) => (
          <li
            key={conv.conv_id}
            role="option"
            aria-selected={i === cursor}
            onMouseEnter={() => setCursor(i)}
            onClick={() => onSelect(conv)}
            className={`flex cursor-pointer items-center gap-2.5 px-3 py-1.5 transition ${
              i === cursor
                ? 'bg-navy-50 dark:bg-navy-500/15'
                : 'hover:bg-slate-50 dark:hover:bg-ink-700'
            }`}
          >
            <span className="shrink-0 text-sm">🔗</span>
            <span className="mono truncate text-[11px] font-medium text-navy-700 dark:text-navy-200">
              {conv.title || conv.conv_id.slice(0, 8)}
            </span>
            <span className={`ml-auto shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${
              conv.fsm_state === 'DONE'
                ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300'
                : 'bg-slate-100 text-slate-500 dark:bg-ink-700 dark:text-slate-400'
            }`}>
              {conv.fsm_state}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
