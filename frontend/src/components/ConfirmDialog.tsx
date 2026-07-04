// Modale de confirmation centrée (charte « Hémicycle »).
// Remplace les window.confirm() natifs par un dialogue cohérent avec la charte.
// Bouton de confirmation en teal par défaut (cf. maquette « Delete conversation »),
// ou en rouge si `danger` (action destructrice à signaler fortement).

import { useEffect } from 'react'
import { useT } from '../lib/i18n'

type Props = {
  open: boolean
  title: string
  body?: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmDialog({
  open, title, body, confirmLabel, cancelLabel, danger, onConfirm, onCancel,
}: Props) {
  const t = useT()
  // Échap = annuler ; on n'attache l'écouteur que lorsque la modale est ouverte.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onCancel])

  if (!open) return null
  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-navy-900/50 p-4 backdrop-blur-sm"
      onClick={onCancel}
    >
      <div
        className="card w-full max-w-sm animate-popIn p-5 shadow-pop"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <h2 className="text-base font-semibold text-slate-800 dark:text-slate-100">{title}</h2>
        {body && <p className="mt-2 whitespace-pre-line text-sm text-slate-600 dark:text-slate-300">{body}</p>}
        <div className="mt-5 flex items-center justify-end gap-2">
          <button onClick={onCancel} className="btn-ghost uppercase tracking-wide">
            {cancelLabel ?? t('common.cancel')}
          </button>
          <button
            onClick={onConfirm}
            className={
              danger
                ? 'inline-flex items-center justify-center gap-1.5 rounded-lg bg-red-600 px-3 py-1.5 text-sm font-semibold uppercase tracking-wide text-white transition hover:bg-red-700'
                : 'btn-accent uppercase tracking-wide'
            }
          >
            {confirmLabel ?? t('common.delete')}
          </button>
        </div>
      </div>
    </div>
  )
}
