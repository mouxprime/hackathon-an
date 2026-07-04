// TimelineLoi — parcours d'un dossier législatif (navette parlementaire).
//
// Timeline verticale à points : done = point plein bleu Assemblée, current =
// anneau or, pending = gris. Le bandeau reprend le statut EXACT du dossier tel
// que fourni par le backend : on n'écrit JAMAIS « loi » tant que le texte n'est
// pas promulgué (neutralité + exactitude juridique).

import { loisPayloadSchema, parseWidgetData } from '../../lib/widgetSchemas'
import { WidgetErrorCard } from './WidgetErrorCard'

function Dot({ statut }: { statut?: string }) {
  if (statut === 'done') {
    return (
      <span className="mt-0.5 inline-block h-3 w-3 shrink-0 rounded-full bg-navy-700 dark:bg-navy-300" aria-hidden />
    )
  }
  if (statut === 'current') {
    return (
      <span
        className="mt-0.5 inline-block h-3 w-3 shrink-0 rounded-full border-[3px] bg-white dark:bg-ink-800"
        style={{ borderColor: '#8a6420' }}
        aria-hidden
      />
    )
  }
  return (
    <span className="mt-0.5 inline-block h-3 w-3 shrink-0 rounded-full border-2 border-slate-300 bg-slate-100 dark:border-ink-500 dark:bg-ink-700" aria-hidden />
  )
}

function statutLabel(statut?: string): string {
  return statut === 'done' ? 'étape franchie' : statut === 'current' ? 'étape en cours' : 'étape à venir'
}

export function TimelineLoi({ data }: { data: any }) {
  const parsed = parseWidgetData(loisPayloadSchema, data)
  if (!parsed.ok) return <WidgetErrorCard details={parsed.error} />
  const p = parsed.data
  const dossier = p.dossier
  const etapes = p.etapes || []

  return (
    <div className="card border-l-4 p-3" style={{ borderLeftColor: '#223061' }}>
      <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-navy-700 dark:text-navy-200">
        📜 Parcours du texte
      </div>

      {dossier?.titre && (
        <p className="text-[12.5px] font-medium leading-snug text-slate-800 dark:text-slate-100">
          {dossier.titre}
        </p>
      )}

      {/* Bandeau de statut : le statut EXACT du dossier, tel que sourcé. */}
      {dossier?.statut && (
        <div className="mt-1.5 rounded-lg border border-navy-200 bg-navy-50/60 px-2.5 py-1.5 text-[12px] font-medium text-navy-700 dark:border-navy-500/30 dark:bg-navy-500/10 dark:text-navy-200">
          Statut actuel : {dossier.statut}
        </div>
      )}

      {/* Timeline verticale à points */}
      {etapes.length > 0 && (
        <ol className="mt-2.5">
          {etapes.map((e, i) => (
            <li key={i} className="relative flex gap-2.5 pb-3 last:pb-0" aria-label={`${e.libelle || 'Étape'} — ${statutLabel(e.statut)}`}>
              {/* Trait vertical reliant les points (sauf dernier) */}
              {i < etapes.length - 1 && (
                <span className="absolute left-[5px] top-4 bottom-0 w-px bg-slate-200 dark:bg-ink-600" aria-hidden />
              )}
              <Dot statut={e.statut} />
              <div className="min-w-0">
                <div className={`text-[12px] leading-snug ${
                  e.statut === 'current'
                    ? 'font-semibold text-navy-700 dark:text-navy-200'
                    : e.statut === 'done'
                      ? 'text-slate-700 dark:text-slate-200'
                      : 'text-slate-400 dark:text-slate-500'
                }`}>
                  {e.libelle || 'Étape'}
                </div>
                <div className="mono text-[10px] text-slate-400 dark:text-slate-500">
                  {[e.chambre, e.date].filter(Boolean).join(' · ')}
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}

      {p.summary && <p className="mt-2 text-[11px] italic text-slate-500 dark:text-slate-400">{p.summary}</p>}

      {dossier?.url_an && (
        <a href={dossier.url_an} target="_blank" rel="noopener noreferrer"
           className="mt-1.5 inline-block text-[11px] font-medium text-teal-600 underline hover:text-teal-700 dark:text-teal-300">
          Dossier législatif officiel ↗
        </a>
      )}
    </div>
  )
}
