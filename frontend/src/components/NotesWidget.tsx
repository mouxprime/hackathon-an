// Widget Notes / Post-it — bloc-notes libre de l'analyste, persistant PAR INSTANCE.
//
// Chaque post-it sauvegarde son contenu en localStorage sous une clé dérivée de
// son instanceId : on peut donc en poser plusieurs sur le tableau sans qu'ils se
// marchent dessus, et le texte survit au rechargement. Deux modes : édition
// (textarea) et aperçu (Markdown rendu). Sauvegarde auto debouncée — l'analyste
// n'a aucun bouton « enregistrer » à actionner.

import { useEffect, useRef, useState } from 'react'
import { Markdown } from './Markdown'
import { useT } from '../lib/i18n'

const LS_PREFIX = 'hemicycle.notes.'

// Lit le contenu persisté d'un post-it (vide si jamais sauvegardé / quota).
function loadNote(instanceId: string): string {
  try { return localStorage.getItem(LS_PREFIX + instanceId) ?? '' } catch { return '' }
}

// Écrit le contenu d'un post-it (silencieux si le quota localStorage explose).
function persistNote(instanceId: string, text: string) {
  try { localStorage.setItem(LS_PREFIX + instanceId, text) } catch { /* quota */ }
}

export function NotesWidget({ instanceId }: { instanceId: string }) {
  const t = useT()
  const [text, setText] = useState(() => loadNote(instanceId))
  const [preview, setPreview] = useState(false)
  const [saved, setSaved] = useState(false)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Sauvegarde auto debouncée (400 ms) : on persiste après la frappe et on
  // affiche brièvement « Enregistré » pour rassurer l'analyste.
  useEffect(() => {
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => {
      persistNote(instanceId, text)
      setSaved(true)
      setTimeout(() => setSaved(false), 1200)
    }, 400)
    return () => { if (saveTimer.current) clearTimeout(saveTimer.current) }
  }, [text, instanceId])

  return (
    <div className="flex h-full flex-col bg-[#fffdf2] dark:bg-ink-800">
      {/* Barre d'action : bascule édition / aperçu + indicateur de sauvegarde */}
      <div className="flex shrink-0 items-center justify-between border-b border-amber-200/60 dark:border-ink-600 px-3 py-1.5">
        <span className="text-[11px] tabular-nums text-amber-700/70 dark:text-slate-500">
          {saved ? `✓ ${t('widgets.notes.saved')}` : `${text.length}`}
        </span>
        <button
          onClick={() => setPreview((v) => !v)}
          className="rounded px-2 py-0.5 text-[11px] font-medium text-amber-800 dark:text-slate-300
                     hover:bg-amber-100 dark:hover:bg-ink-700 transition"
          title={preview ? t('widgets.notes.edit') : t('widgets.notes.preview')}
        >
          {preview ? `✎ ${t('widgets.notes.edit')}` : `👁 ${t('widgets.notes.preview')}`}
        </button>
      </div>

      {/* Corps : textarea (édition) ou Markdown rendu (aperçu) */}
      {preview ? (
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 text-sm text-slate-800 dark:text-slate-100">
          {text.trim()
            ? <Markdown>{text}</Markdown>
            : <p className="text-amber-700/50 dark:text-slate-500 italic">{t('widgets.notes.empty')}</p>}
        </div>
      ) : (
        <textarea
          className="min-h-0 flex-1 resize-none bg-transparent px-4 py-3 text-sm leading-relaxed
                     text-slate-800 dark:text-slate-100 placeholder:text-amber-700/40
                     dark:placeholder:text-slate-500 focus:outline-none"
          placeholder={t('widgets.notes.placeholder')}
          value={text}
          onChange={(e) => setText(e.target.value)}
          spellCheck={false}
        />
      )}
    </div>
  )
}
