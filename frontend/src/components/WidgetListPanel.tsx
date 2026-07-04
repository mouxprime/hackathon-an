// Panel droit collapsible listant tous les widgets actifs + minimisés du tableau.
// Clic → recentre le canvas sur ce widget (via onFocus).
// Double-clic sur titre → renommage in-place.
// Badge de statut : ↗ fullscreen, 🔽 minimisé.

import { useState, useRef } from 'react'
import { WIDGETS } from '../lib/widgetRegistry'
import { WidgetIcon } from './WidgetIcon'

type WidgetEntry = {
  id: string
  specId: string
  title: string
  status: 'normal' | 'minimized' | 'fullscreen'
}

type Props = {
  open: boolean
  onToggle: () => void
  widgets: WidgetEntry[]
  widgetTitles: Record<string, string>
  onFocus: (id: string) => void
  onRename: (id: string, title: string) => void
  onRestore?: (id: string) => void
}

// Retourne le titre brut du spec (non traduit) ou le specId.
function defaultTitle(specId: string): string {
  return WIDGETS.find((w) => w.id === specId)?.title ?? specId
}

export function WidgetListPanel({ open, onToggle, widgets, widgetTitles, onFocus, onRename, onRestore }: Props) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  // Démarre le renommage in-place sur un widget.
  const startEdit = (id: string, currentTitle: string) => {
    setEditingId(id)
    setEditValue(currentTitle)
    setTimeout(() => inputRef.current?.select(), 0)
  }

  // Valide et propage le nouveau titre ; annule si vide.
  const commitEdit = () => {
    if (editingId && editValue.trim()) {
      onRename(editingId, editValue.trim())
    }
    setEditingId(null)
  }

  // Gère Entrée (commit) et Échap (annule).
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') commitEdit()
    if (e.key === 'Escape') setEditingId(null)
  }

  return (
    <div
      className={`absolute right-0 top-0 bottom-0 z-20 flex flex-col transition-all duration-200 ${
        open ? 'w-[220px]' : 'w-8'
      }`}
    >
      {/* Bouton toggle (toujours visible). */}
      <button
        onClick={onToggle}
        className="absolute left-0 top-3 flex h-7 w-7 items-center justify-center rounded-l-md bg-navy-700 text-white text-xs shadow hover:bg-navy-600 transition z-10"
        title={open ? 'Masquer le panel' : 'Afficher la liste des widgets'}
        aria-label="Basculer le panel de widgets"
      >
        {open ? '›' : '‹'}
      </button>

      {/* Contenu du panel : affiché uniquement si ouvert. */}
      {open && (
        <div className="ml-7 flex h-full flex-col border-l border-navy-700/20 bg-white/95 dark:bg-ink-800/95 shadow-lg backdrop-blur-sm">
          <div className="border-b border-slate-200 dark:border-ink-700 px-3 py-2">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Widgets ({widgets.length})
            </p>
          </div>
          <div className="flex-1 overflow-y-auto py-1">
            {widgets.length === 0 && (
              <p className="px-3 py-4 text-[11px] text-slate-400 dark:text-slate-500">
                Aucun widget sur le tableau
              </p>
            )}
            {widgets.map((w) => {
              const displayTitle = widgetTitles[w.id] || defaultTitle(w.specId)
              const isEditing = editingId === w.id
              return (
                <div
                  key={w.id}
                  className={`group flex items-center gap-2 px-3 py-1.5 cursor-pointer transition hover:bg-navy-50 dark:hover:bg-navy-500/10 ${
                    w.status === 'minimized' ? 'opacity-50' : ''
                  }`}
                  onClick={() => {
                    if (w.status === 'minimized' && onRestore) onRestore(w.id)
                    else onFocus(w.id)
                  }}
                >
                  <WidgetIcon specId={w.specId} className="h-4 w-4 shrink-0 text-navy-700 dark:text-navy-200" />
                  {isEditing ? (
                    <input
                      ref={inputRef}
                      value={editValue}
                      onChange={(e) => setEditValue(e.target.value)}
                      onBlur={commitEdit}
                      onKeyDown={handleKeyDown}
                      className="flex-1 min-w-0 rounded border border-navy-300 px-1 py-0.5 text-[11px] outline-none"
                      onClick={(e) => e.stopPropagation()}
                      autoFocus
                    />
                  ) : (
                    <span
                      className="flex-1 min-w-0 truncate text-[11px] text-slate-700 dark:text-slate-200"
                      onDoubleClick={(e) => { e.stopPropagation(); startEdit(w.id, displayTitle) }}
                      title="Double-clic pour renommer"
                    >
                      {displayTitle}
                    </span>
                  )}
                  {w.status === 'fullscreen' && (
                    <span className="shrink-0 text-[10px] text-green-600" title="Plein écran">↗</span>
                  )}
                  {w.status === 'minimized' && (
                    <span className="shrink-0 text-[10px] text-amber-500" title="Minimisé">🔽</span>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
