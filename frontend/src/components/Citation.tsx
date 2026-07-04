// Puce de citation numérotée (charte « Hémicycle ») : petit cercle
// teal ①②③ au survol duquel s'affiche le SourcePopover (extrait + carte fichier).
//
// Réutilisable : aujourd'hui posée par SourcesFooter (liste sous la réponse) ;
// extensible plus tard à un marquage inline `[n]` dans Markdown quand le backend
// fournira les ancres de citation par fragment de texte.

import { useState } from 'react'
import { SourcePopover, type SourceItem } from './SourcePopover'

export function Citation({ index, source }: { index: number; source?: SourceItem }) {
  const [hover, setHover] = useState(false)
  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <span className="inline-flex h-4 w-4 cursor-help items-center justify-center rounded-full bg-teal-500 text-[9px] font-bold leading-none text-white">
        {index}
      </span>
      {hover && source && <SourcePopover source={source} />}
    </span>
  )
}
