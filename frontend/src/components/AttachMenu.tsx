// Menu du trombone (barre de saisie) — charte « Hémicycle ».
// « Local file » ouvre le sélecteur de fichier ; « From Federated Search » (si
// fourni) renvoie l'analyste vers la requête en langage naturel (la recherche
// fédérée s'invoque par le chat, pas par un picker). Se ferme au clic extérieur.

import { useEffect, useRef } from 'react'
import { useT } from '../lib/i18n'

type Props = {
  onLocalFile: () => void
  onFedSearch?: () => void
  onClose: () => void
}

export function AttachMenu({ onLocalFile, onFedSearch, onClose }: Props) {
  const t = useT()
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [onClose])

  return (
    <div ref={ref} className="card absolute bottom-full left-0 z-50 mb-2 w-48 animate-popIn py-1 shadow-pop">
      <button
        onClick={() => { onLocalFile(); onClose() }}
        className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-[13px] text-slate-700 transition hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-ink-700"
      >
        <svg viewBox="0 0 24 24" className="h-4 w-4 shrink-0 text-slate-500" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="4" width="18" height="13" rx="2" /><path d="M8 21h8M12 17v4" />
        </svg>
        {t('chat.localFile')}
      </button>
      {onFedSearch && (
        <button
          onClick={() => { onFedSearch(); onClose() }}
          className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-[13px] text-slate-700 transition hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-ink-700"
        >
          <svg viewBox="0 0 24 24" className="h-4 w-4 shrink-0 text-slate-500" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" />
          </svg>
          {t('chat.fedSearch')}
        </button>
      )}
    </div>
  )
}
