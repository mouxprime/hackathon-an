// Menu contextuel flottant (clic-droit) — charte « Hémicycle ».
// Positionné aux coordonnées du curseur ; se ferme au clic extérieur, au scroll
// ou sur Échap. Utilisé p.ex. par la liste de conversations (« Delete conversation »).

import { useEffect, useRef, type ReactNode } from 'react'

export type ContextMenuItem = {
  label: string
  icon?: ReactNode
  danger?: boolean
  onClick: () => void
}

type Props = {
  x: number
  y: number
  items: ContextMenuItem[]
  onClose: () => void
}

export function ContextMenu({ x, y, items, onClose }: Props) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    // Fermeture sur interaction extérieure : clic ailleurs, scroll, Échap.
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    window.addEventListener('scroll', onClose, true)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', onClose, true)
    }
  }, [onClose])

  // Décalage léger pour ne pas masquer l'élément cliqué sous le curseur.
  return (
    <div
      ref={ref}
      className="card fixed z-[60] min-w-[180px] animate-popIn py-1 shadow-pop"
      style={{ left: x, top: y }}
      role="menu"
    >
      {items.map((it, i) => (
        <button
          key={i}
          role="menuitem"
          onClick={() => { it.onClick(); onClose() }}
          className={`flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-[13px] transition hover:bg-slate-50 dark:hover:bg-ink-700 ${
            it.danger
              ? 'text-red-600 dark:text-red-400'
              : 'text-slate-700 dark:text-slate-200'
          }`}
        >
          {it.icon && <span className="h-4 w-4 shrink-0">{it.icon}</span>}
          {it.label}
        </button>
      ))}
    </div>
  )
}
