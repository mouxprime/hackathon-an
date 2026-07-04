// Barre fixe en bas du canvas — toutes les fenêtres ouvertes ET minimisées.
// Clic sur une fenêtre ouverte → focus sur le canvas.
// Clic sur une fenêtre minimisée → restaure sur le canvas.
// Bouton ✕ → ferme définitivement.

import { WidgetIcon } from './WidgetIcon'

export type TaskbarEntry = {
  id: string
  specId: string
  title: string
  status: 'open' | 'minimized' | 'fullscreen'
}

type Props = {
  widgets: TaskbarEntry[]
  onFocus: (id: string) => void    // fenêtre open/fullscreen → centrer la vue
  onRestore: (id: string) => void  // fenêtre minimisée → remettre sur le canvas
  onRemove: (id: string) => void   // fermer définitivement
  chatWidth?: number
}

export function WidgetTaskbar({ widgets, onFocus, onRestore, onRemove, chatWidth = 0 }: Props) {
  return (
    <div
      className="absolute bottom-0 right-0 z-30 flex h-9 items-center gap-1.5 bg-navy-700/90 px-3 backdrop-blur-sm rounded-t-3xl"
      style={{ left: chatWidth }}
    >
      {widgets.map((w) => {
        const isMinimized = w.status === 'minimized'
        return (
          <div
            key={w.id}
            className={`group flex max-w-[160px] cursor-pointer items-center gap-1.5 rounded-md border px-2 py-1 text-white transition ${
              isMinimized
                ? 'border-white/15 bg-white/10 opacity-70 hover:opacity-100 hover:bg-white/20'
                : 'border-white/25 bg-white/20 hover:bg-white/30'
            }`}
            onClick={() => isMinimized ? onRestore(w.id) : onFocus(w.id)}
            title={isMinimized ? `Restaurer : ${w.title}` : w.title}
          >
            <WidgetIcon specId={w.specId} className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate text-[11px] font-medium">{w.title}</span>
            {/* Indicateur de statut : point vert = ouvert, tiret = minimisé */}
            <span
              className={`shrink-0 h-1.5 w-1.5 rounded-full ${isMinimized ? 'bg-white/30' : 'bg-emerald-400'}`}
            />
            <button
              onClick={(e) => { e.stopPropagation(); onRemove(w.id) }}
              className="ml-auto shrink-0 text-white/40 hover:text-red-300 text-[10px] leading-none"
              title="Fermer définitivement"
              aria-label="Fermer définitivement"
            >
              ✕
            </button>
          </div>
        )
      })}
    </div>
  )
}
