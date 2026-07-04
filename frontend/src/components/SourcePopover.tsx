// Popover de source (charte « Hémicycle ») — affiché au survol d'une
// puce de citation. Montre l'extrait cité + une carte fichier (icône, nom, type,
// fiabilité/source). Positionné en absolu au-dessus de la puce.

export type SourceItem = {
  title?: string
  // URL de la source officielle (fournie par le backend) : rend le titre cliquable.
  url?: string
  snippet?: string
  source?: string
  reliability?: string | number
  date?: string
}

// Icône « lien externe » discrète (titres de sources cliquables).
function ExternalIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-3 w-3 shrink-0 opacity-60" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
      <path d="M15 3h6v6M10 14 21 3" />
    </svg>
  )
}

export function SourcePopover({ source }: { source: SourceItem }) {
  return (
    <div
      role="tooltip"
      className="card absolute bottom-full left-1/2 z-50 mb-1.5 w-64 -translate-x-1/2 animate-popIn p-3 text-left shadow-pop"
    >
      {source.snippet && (
        <p className="mb-2 line-clamp-4 text-[11.5px] leading-relaxed text-slate-700 dark:text-slate-200">
          {source.snippet}
        </p>
      )}
      <div className="flex items-start gap-2 border-t border-slate-100 pt-2 dark:border-ink-600">
        <span className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-teal-500/15 text-teal-600 dark:text-teal-300">
          <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round">
            <path d="M14 3v5h5M7 3h8l5 5v11a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" />
          </svg>
        </span>
        <div className="min-w-0">
          {source.url ? (
            <a
              href={source.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex max-w-full items-center gap-1 truncate text-[12px] font-semibold text-teal-700 underline decoration-teal-300 hover:text-teal-800 dark:text-teal-300 dark:hover:text-teal-200"
              title={source.url}
            >
              <span className="truncate">{source.title || source.source || source.url}</span>
              <ExternalIcon />
            </a>
          ) : (
            <div className="truncate text-[12px] font-semibold text-teal-700 dark:text-teal-300" title={source.title}>
              {source.title || source.source || '—'}
            </div>
          )}
          <div className="text-[10px] text-slate-500 dark:text-slate-400">Source officielle</div>
          {(source.date || source.source) && (
            <div className="mono text-[10px] text-slate-400 dark:text-slate-500">
              {source.date || source.source}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
