// VoteChart — résultat d'un scrutin public de l'Assemblée nationale.
//
// Barres horizontales en CSS flex (pas de lib de charts) : une ligne par groupe
// politique, segments colorés par POSITION (pour / contre / abstention /
// non-votants). La pastille devant le nom du groupe reprend la couleur du
// groupe (payload `couleur`). Tous les nombres sont AUSSI en texte + aria-label
// (accessibilité, lecture sans couleurs).

import { parseWidgetData, votesPayloadSchema, type VotesPayload } from '../../lib/widgetSchemas'
import { WidgetErrorCard } from './WidgetErrorCard'

// Couleurs par POSITION de vote (contrat UX fixe, indépendant des groupes).
const POSITION_COLORS = {
  pour: '#059669',
  contre: '#dc2626',
  abstention: '#f59e0b',
  nonVotant: '#94a3b8',
} as const

const POSITION_LABELS: Record<keyof typeof POSITION_COLORS, string> = {
  pour: 'Pour',
  contre: 'Contre',
  abstention: 'Abstention',
  nonVotant: 'Non-votants',
}

function sortBadge(sort?: string) {
  if (!sort) return null
  const adopted = /adopt/i.test(sort)
  const rejected = /rejet/i.test(sort)
  const cls = adopted
    ? 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-300 dark:border-emerald-500/30'
    : rejected
      ? 'bg-red-50 text-red-700 border-red-200 dark:bg-red-500/15 dark:text-red-300 dark:border-red-500/30'
      : 'bg-slate-100 text-slate-600 border-slate-300 dark:bg-ink-700 dark:text-slate-300 dark:border-ink-500'
  return (
    <span className={`inline-block rounded border px-2 py-0.5 text-[11px] font-semibold ${cls}`}>
      {sort}
    </span>
  )
}

// Barre empilée d'un groupe : segments flex proportionnels aux voix.
function GroupBar({ g }: { g: NonNullable<VotesPayload['groupes']>[number] }) {
  const pour = g.pour ?? 0
  const contre = g.contre ?? 0
  const abst = g.abstention ?? 0
  const nv = g.nonVotant ?? 0
  const total = pour + contre + abst + nv
  const label = g.nom || g.abrege || 'Groupe'
  const aria = `${label} : ${pour} pour, ${contre} contre, ${abst} abstention${abst > 1 ? 's' : ''}, ${nv} non-votant${nv > 1 ? 's' : ''}`
  const segs: { key: string; n: number; color: string }[] = [
    { key: 'pour', n: pour, color: POSITION_COLORS.pour },
    { key: 'contre', n: contre, color: POSITION_COLORS.contre },
    { key: 'abstention', n: abst, color: POSITION_COLORS.abstention },
    { key: 'nonVotant', n: nv, color: POSITION_COLORS.nonVotant },
  ]
  return (
    <li aria-label={aria}>
      <div className="mb-0.5 flex items-center gap-1.5">
        {/* Pastille = couleur du GROUPE (pas de la position) */}
        <span
          className="inline-block h-2.5 w-2.5 shrink-0 rounded-full border border-black/10"
          style={{ background: g.couleur || '#cbd5e1' }}
          aria-hidden
        />
        <span className="truncate text-[11.5px] font-medium text-slate-700 dark:text-slate-200" title={g.nom}>
          {label}
        </span>
        {g.position_majoritaire && (
          <span className="mono ml-auto shrink-0 text-[10px] text-slate-400 dark:text-slate-500">
            majorité : {g.position_majoritaire}
          </span>
        )}
      </div>
      {total > 0 ? (
        <div className="flex h-3 w-full overflow-hidden rounded bg-slate-100 dark:bg-ink-700" aria-hidden>
          {segs.filter((s) => s.n > 0).map((s) => (
            <div
              key={s.key}
              className="h-full"
              style={{ width: `${(s.n / total) * 100}%`, background: s.color }}
              title={`${POSITION_LABELS[s.key as keyof typeof POSITION_LABELS]} : ${s.n}`}
            />
          ))}
        </div>
      ) : (
        <div className="h-3 w-full rounded bg-slate-100 dark:bg-ink-700" aria-hidden />
      )}
      <div className="mono mt-0.5 text-[10px] text-slate-500 dark:text-slate-400">
        {pour} pour · {contre} contre · {abst} abst. · {nv} NV
      </div>
    </li>
  )
}

export function VoteChart({ data }: { data: any }) {
  const parsed = parseWidgetData(votesPayloadSchema, data)
  if (!parsed.ok) return <WidgetErrorCard details={parsed.error} />
  const p = parsed.data
  const s = p.scrutin
  const groupes = p.groupes || []
  const dep = p.position_depute

  const totals = s
    ? `${s.pour ?? 0} pour, ${s.contre ?? 0} contre, ${s.abstentions ?? 0} abstentions, ${s.non_votants ?? 0} non-votants`
    : ''

  return (
    <div className="card border-l-4 p-3" style={{ borderLeftColor: '#223061' }}>
      {/* En-tête : titre du scrutin + sort + totaux en texte */}
      <div className="mb-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-navy-700 dark:text-navy-200">
            🗳️ Scrutin{s?.numero != null ? ` n° ${s.numero}` : ''}
          </span>
          {sortBadge(s?.sort)}
          {s?.date && <span className="mono text-[10.5px] text-slate-400 dark:text-slate-500">{s.date}</span>}
        </div>
        {s?.titre && (
          <p className="mt-1 text-[12.5px] leading-snug text-slate-800 dark:text-slate-100">{s.titre}</p>
        )}
        {totals && (
          <p className="mt-1 text-[11px] text-slate-600 dark:text-slate-300" aria-label={`Résultat : ${totals}`}>
            <strong className="font-semibold" style={{ color: POSITION_COLORS.pour }}>{s?.pour ?? 0} pour</strong>
            {' · '}
            <strong className="font-semibold" style={{ color: POSITION_COLORS.contre }}>{s?.contre ?? 0} contre</strong>
            {' · '}
            <span style={{ color: POSITION_COLORS.abstention }}>{s?.abstentions ?? 0} abstentions</span>
            {' · '}
            <span className="text-slate-500 dark:text-slate-400">{s?.non_votants ?? 0} non-votants</span>
          </p>
        )}
      </div>

      {/* Encart « votre député » */}
      {dep?.nom && dep?.position && (
        <div className="mb-2 rounded-lg border border-navy-200 bg-navy-50/60 px-2.5 py-1.5 text-[12px] text-navy-700 dark:border-navy-500/30 dark:bg-navy-500/10 dark:text-navy-200">
          🧑‍💼 Votre député : <strong>{dep.nom}</strong>
          {dep.groupe ? ` (${dep.groupe})` : ''} a voté <strong>{dep.position}</strong>
        </div>
      )}

      {/* Barres par groupe */}
      {groupes.length > 0 && (
        <ul className="space-y-2">
          {groupes.map((g, i) => <GroupBar key={i} g={g} />)}
        </ul>
      )}

      {/* Légende des positions */}
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
        {(Object.keys(POSITION_COLORS) as (keyof typeof POSITION_COLORS)[]).map((k) => (
          <span key={k} className="inline-flex items-center gap-1 text-[10px] text-slate-500 dark:text-slate-400">
            <span className="inline-block h-2 w-2 rounded-sm" style={{ background: POSITION_COLORS[k] }} aria-hidden />
            {POSITION_LABELS[k]}
          </span>
        ))}
      </div>

      {p.summary && <p className="mt-2 text-[11px] italic text-slate-500 dark:text-slate-400">{p.summary}</p>}

      {/* Liens officiels */}
      {(s?.url_an || s?.url_data) && (
        <div className="mt-1.5 flex flex-wrap gap-3">
          {s?.url_an && (
            <a href={s.url_an} target="_blank" rel="noopener noreferrer"
               className="text-[11px] font-medium text-teal-600 underline hover:text-teal-700 dark:text-teal-300">
              Voir sur assemblee-nationale.fr ↗
            </a>
          )}
          {s?.url_data && (
            <a href={s.url_data} target="_blank" rel="noopener noreferrer"
               className="text-[11px] font-medium text-teal-600 underline hover:text-teal-700 dark:text-teal-300">
              Données ouvertes ↗
            </a>
          )}
        </div>
      )}

      {/* Note pédagogique : absent ≠ non-votant ≠ abstention */}
      <p className="mt-2 border-t border-slate-100 pt-1.5 text-[10.5px] leading-snug text-slate-400 dark:border-ink-600 dark:text-slate-500">
        ℹ️ Les députés absents n'apparaissent pas dans un scrutin : ne pas confondre
        avec « non-votant » ou « abstention ».
      </p>
    </div>
  )
}
