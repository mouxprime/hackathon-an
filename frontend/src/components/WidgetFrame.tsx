// Cadre commun d'un widget sur le dashboard.
//
// Header navy (bg-navy-700) : trois points style macOS à gauche + titre centré en
// majuscules. Drag handle sur la bande d'en-tête (à droite des 3 points).
// Dessous : zone de contenu flexible, scrolling géré par chaque widget.

import type { ReactNode } from 'react'

type Props = {
  onClose: () => void
  onMinimize: () => void
  onFullscreen: () => void
  children: ReactNode
  isFullscreen?: boolean
  title?: string
}

export function WidgetFrame({ onClose, onMinimize, onFullscreen, children, isFullscreen, title }: Props) {
  return (
    <div className="flex h-full flex-col">
      {/* Bande d'en-tête navy — même couleur que le header global de l'app */}
      <div className="relative flex h-7 shrink-0 items-center rounded-t-[36px] bg-navy-700 pl-4 pr-2">
        {/* Drag handle : couvre la bande à droite des 3 points */}
        <div
          className="widget-drag-handle absolute inset-0 z-0 cursor-move"
          style={{ left: '52px' }}
        />
        {/* Trois points style macOS — centrés verticalement */}
        <div className="relative z-10 flex items-center gap-1.5">
          <button
            onClick={onClose}
            className="h-3 w-3 rounded-full transition-opacity hover:opacity-80 focus:outline-none"
            style={{ backgroundColor: '#ff5f57' }}
            title="Fermer"
            aria-label="Fermer le widget"
          />
          <button
            onClick={onMinimize}
            className="h-3 w-3 rounded-full transition-opacity hover:opacity-80 focus:outline-none"
            style={{ backgroundColor: '#febc2e' }}
            title="Minimiser"
            aria-label="Minimiser le widget"
          />
          <button
            onClick={onFullscreen}
            className="h-3 w-3 rounded-full transition-opacity hover:opacity-80 focus:outline-none"
            style={{ backgroundColor: '#28c840' }}
            title={isFullscreen ? 'Quitter le plein écran' : 'Plein écran'}
            aria-label={isFullscreen ? 'Quitter le plein écran' : 'Plein écran'}
          />
        </div>
        {/* Titre centré — majuscules, blanc */}
        {title && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
            <span className="max-w-[60%] truncate text-[10px] font-bold uppercase tracking-widest text-white/90">
              {title}
            </span>
          </div>
        )}
      </div>
      {/* Zone de contenu — le widget gère son propre scroll */}
      <div className="flex-1 overflow-hidden">
        {children}
      </div>
    </div>
  )
}
