// FicheDepute — carte d'identité parlementaire d'un député (ou d'une liste).
//
// Photo officielle (url_photo) avec repli sur les initiales dans un rond aux
// couleurs du groupe si l'image ne charge pas. Badge « Ancien député » quand
// actif=false. Stats (participation / loyauté) en barres simples ; votes
// récents en liste compacte avec position colorée.

import { useState } from 'react'
import {
  deputesPayloadSchema,
  parseWidgetData,
  type DeputesPayload,
} from '../../lib/widgetSchemas'
import { WidgetErrorCard } from './WidgetErrorCard'

type Depute = NonNullable<DeputesPayload['depute']>

// Couleur de position d'un vote récent (mêmes codes que VoteChart).
function positionColor(pos?: string): string {
  if (!pos) return '#94a3b8'
  if (/pour/i.test(pos)) return '#059669'
  if (/contre/i.test(pos)) return '#dc2626'
  if (/abst/i.test(pos)) return '#f59e0b'
  return '#94a3b8'
}

// Initiales du député (repli photo) : « Jean-Luc Dupont » → « JD ».
function initials(nom?: string): string {
  if (!nom) return '?'
  const parts = nom.trim().split(/\s+/)
  const first = parts[0]?.[0] || ''
  const last = parts.length > 1 ? parts[parts.length - 1][0] : ''
  return (first + last).toUpperCase() || '?'
}

// Couleur déterministe dérivée du nom de groupe (pas de mapping en dur des
// partis : neutralité — juste un hash stable pour distinguer visuellement).
function groupeColor(groupe?: string): string {
  if (!groupe) return '#47598f'
  let h = 0
  for (const c of groupe) h = (h * 31 + c.charCodeAt(0)) % 360
  return `hsl(${h}, 45%, 40%)`
}

function Avatar({ d }: { d: Depute }) {
  const [broken, setBroken] = useState(false)
  if (d.url_photo && !broken) {
    return (
      <img
        src={d.url_photo}
        alt={d.nom_complet || 'Photo du député'}
        onError={() => setBroken(true)}
        className="h-14 w-14 shrink-0 rounded-full border border-slate-200 object-cover dark:border-ink-600"
      />
    )
  }
  return (
    <span
      className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full text-[18px] font-bold text-white"
      style={{ background: groupeColor(d.groupe) }}
      aria-label={d.nom_complet}
    >
      {initials(d.nom_complet)}
    </span>
  )
}

// Barre de stat simple (0–100 ou 0–1 tolérés).
function StatBar({ label, value }: { label: string; value?: number }) {
  if (typeof value !== 'number') return null
  const pct = Math.max(0, Math.min(100, value <= 1 ? value * 100 : value))
  return (
    <div aria-label={`${label} : ${Math.round(pct)} %`}>
      <div className="flex items-center justify-between text-[10.5px] text-slate-500 dark:text-slate-400">
        <span>{label}</span>
        <span className="mono">{Math.round(pct)} %</span>
      </div>
      <div className="mt-0.5 h-1.5 w-full overflow-hidden rounded bg-slate-100 dark:bg-ink-700">
        <div className="h-full rounded bg-navy-500 dark:bg-navy-400" style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

function DeputeRow({ d }: { d: Depute }) {
  return (
    <div className="flex items-center gap-3">
      <Avatar d={d} />
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[13px] font-semibold text-slate-800 dark:text-slate-100">
            {d.nom_complet || 'Député'}
          </span>
          {d.actif === false && (
            <span className="rounded border border-slate-300 bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-600 dark:border-ink-500 dark:bg-ink-700 dark:text-slate-300">
              Ancien député
            </span>
          )}
        </div>
        {d.groupe && <div className="text-[11.5px] text-slate-600 dark:text-slate-300">{d.groupe}</div>}
        {d.circo && <div className="text-[11px] text-slate-500 dark:text-slate-400">{d.circo}</div>}
        {d.url_an && (
          <a href={d.url_an} target="_blank" rel="noopener noreferrer"
             className="text-[11px] font-medium text-teal-600 underline hover:text-teal-700 dark:text-teal-300">
            Fiche officielle ↗
          </a>
        )}
      </div>
    </div>
  )
}

export function FicheDepute({ data }: { data: any }) {
  const parsed = parseWidgetData(deputesPayloadSchema, data)
  if (!parsed.ok) return <WidgetErrorCard details={parsed.error} />
  const p = parsed.data
  const main = p.depute
  const others = (p.deputes || []).filter((d) => d.uid !== main?.uid || d.nom_complet !== main?.nom_complet)
  const votes = p.votes_recents || []

  if (!main && others.length === 0) {
    return <WidgetErrorCard details="payload fiche_depute sans champ depute ni deputes" />
  }

  return (
    <div className="card border-l-4 p-3" style={{ borderLeftColor: '#8a6420' }}>
      <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-navy-700 dark:text-navy-200">
        🧑‍💼 Fiche député
      </div>

      {main && <DeputeRow d={main} />}

      {/* Stats simples */}
      {p.stats && (
        <div className="mt-2.5 space-y-1.5">
          <StatBar label="Participation aux scrutins" value={p.stats.participation} />
          <StatBar label="Loyauté au groupe" value={p.stats.loyaute} />
        </div>
      )}

      {/* Votes récents */}
      {votes.length > 0 && (
        <div className="mt-2.5">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
            Votes récents
          </div>
          <ul className="mt-1 space-y-1">
            {votes.map((v, i) => (
              <li key={i} className="flex items-start gap-1.5 text-[11.5px]">
                <span
                  className="mt-1 inline-block h-2 w-2 shrink-0 rounded-full"
                  style={{ background: positionColor(v.position) }}
                  aria-hidden
                />
                <span className="min-w-0 text-slate-700 dark:text-slate-200">
                  {v.url_an ? (
                    <a href={v.url_an} target="_blank" rel="noopener noreferrer" className="hover:underline">
                      {v.scrutin_titre || 'Scrutin'}
                    </a>
                  ) : (v.scrutin_titre || 'Scrutin')}
                  <span className="text-slate-400 dark:text-slate-500">
                    {v.date ? ` — ${v.date}` : ''}
                  </span>
                  {v.position && (
                    <strong className="ml-1" style={{ color: positionColor(v.position) }}>
                      {v.position}
                    </strong>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Liste secondaire (homonymes / plusieurs résultats) */}
      {others.length > 0 && (
        <div className="mt-2.5 space-y-2 border-t border-slate-100 pt-2 dark:border-ink-600">
          {!main && others.map((d, i) => <DeputeRow key={i} d={d} />)}
          {main && (
            <>
              <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
                Autres résultats
              </div>
              {others.map((d, i) => <DeputeRow key={i} d={d} />)}
            </>
          )}
        </div>
      )}

      {p.summary && <p className="mt-2 text-[11px] italic text-slate-500 dark:text-slate-400">{p.summary}</p>}
    </div>
  )
}
