// Système de widgets server-driven.
//
// L'orchestrateur (ou un sub-agent) décide quoi montrer à l'analyste en publiant des
// événements `widget` typés sur `orchestrator.events`. Le frontend les rend via ce
// registre : `widget_type` → composant React. Ajouter un widget = une entrée ici.
//
// Thème clair : cartes blanches + accents par discipline (SEARCH navy, IMINT violet,
// GEOINT emerald, RAPPORT amber). Mode nuit : surfaces ink + accents conservés.

import { useState } from 'react'
import { Markdown } from './components/Markdown'
import { api } from './lib/api'
import { useReportStreamStore } from './lib/store'
import type { Progress } from './lib/types'
import { Bar, Pill, STATUS_COLORS } from './lib/ui'
import { translate, useT } from './lib/i18n'
import { humanizeTrace } from './lib/traceLabels'
import { VoteChart } from './components/widgets/VoteChart'
import { FicheDepute } from './components/widgets/FicheDepute'
import { TimelineLoi } from './components/widgets/TimelineLoi'
import { WidgetErrorCard } from './components/widgets/WidgetErrorCard'

type WidgetProps = { data: any; progress?: Progress; traces?: Progress[]; widgetId?: string }

// --- timeline de traces : raisonnement + appels d'outils/MCP en direct ---
// Les tool_call/tool_result sont HUMANISÉS (« Recherche du scrutin… »,
// « 3 votes trouvés ») via lib/traceLabels ; le serveur MCP interrogé est
// affiché en chip « via {mcp_server} » bien visible (IA de confiance).
function TraceTimeline({ traces }: { traces: Progress[] }) {
  const t = useT()
  if (!traces.length) return null
  const icon = (k: string) =>
    k === 'thinking' ? '💭' : k === 'tool_call' ? '🔧' : k === 'tool_result' ? '✓' : '•'
  return (
    <ul className="mt-2 space-y-1 border-t border-slate-100 pt-2 dark:border-ink-600">
      {traces.slice(-8).map((tr, i) => {
        const d = tr.trace_data || {}
        let label = ''
        let via: string | undefined
        let technical: string | undefined
        if (tr.kind === 'thinking') {
          label = d.text || t('widgets.trace.thinking')
        } else {
          const h = humanizeTrace(tr.kind, d)
          label = h.label || t('widgets.trace.tool')
          via = h.via
          technical = h.technical
        }
        return (
          <li key={i} className="flex animate-fadeInUp items-start gap-1.5 text-[10px] text-slate-500 dark:text-slate-400">
            <span className="shrink-0">{icon(tr.kind)}</span>
            <span className="truncate" title={technical}>{label}</span>
            {via && (
              <span className="mono ml-auto shrink-0 rounded-full border border-teal-200 bg-teal-50 px-1.5 py-px text-[9px] font-semibold text-teal-700 dark:border-teal-500/30 dark:bg-teal-500/15 dark:text-teal-300">
                via {via}
              </span>
            )}
          </li>
        )
      })}
    </ul>
  )
}

// --- task_progress : suivi live d'une tâche dispatchée à un sub-agent ---
function TaskProgressWidget({ data, progress, traces }: WidgetProps) {
  const t = useT()
  const terminal = ['done', 'failed', 'timeout', 'cancelled'].includes(data.status)
  // La progression live (éphémère) prime tant que la tâche n'est pas terminale.
  const pct = !terminal && progress ? progress.pct : data.pct ?? 0
  const note = !terminal && progress ? progress.note : data.instruction
  const barCls =
    data.status === 'done' ? 'bg-emerald-500'
    : data.status === 'failed' || data.status === 'timeout' ? 'bg-red-500'
    : data.status === 'cancelled' ? 'bg-slate-400'
    : 'bg-navy-600 dark:bg-navy-500'
  return (
    <div className="card p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="mono text-xs font-semibold uppercase text-navy-700 dark:text-navy-200">
          {data.agent_type}
        </span>
        <div className="flex items-center gap-2">
          {data.attempt && <Pill label={t('widgets.taskProgress.attempt', { n: data.attempt })} cls={STATUS_COLORS.retrying} />}
          <Pill label={data.status} cls={STATUS_COLORS[data.status] || ''} />
        </div>
      </div>
      <Bar pct={pct >= 0 ? pct : 0} cls={barCls} />
      <p className="mt-2 truncate text-[11px] text-slate-500 dark:text-slate-400">{note}</p>
      {traces && <TraceTimeline traces={traces} />}
    </div>
  )
}

// Icône « lien externe » discrète accolée aux titres de sources cliquables.
export function ExternalLinkIcon({ className = 'h-3 w-3 shrink-0 opacity-60' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
      <path d="M15 3h6v6M10 14 21 3" />
    </svg>
  )
}

// --- source_list : résultat de recherche fédérée (sources du corpus + synthèse) ---
function SourceListWidget({ data }: WidgetProps) {
  const t = useT()
  const sources: any[] = data.sources || []
  return (
    <div className="card border-l-4 border-l-navy-500 p-3">
      <div className="mb-2 flex items-center gap-2">
        <Pill label="SEARCH" cls="text-navy-700 bg-navy-50 border-navy-200 dark:text-navy-200 dark:bg-navy-500/15 dark:border-navy-500/30" />
        <span className="text-xs text-slate-500 dark:text-slate-400">{t('widgets.sourceList.sources', { count: sources.length })}</span>
      </div>
      <ul className="space-y-1.5">
        {sources.map((s, i) => (
          <li key={i} className="rounded-lg border border-slate-200 bg-slate-50 px-2 py-1.5 dark:border-ink-600 dark:bg-ink-700">
            <div className="flex items-center justify-between gap-2">
              {/* Source cliquable si le backend fournit `url` (IA de confiance). */}
              {s.url ? (
                <a
                  href={s.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex min-w-0 items-center gap-1 truncate text-xs font-medium text-slate-800 underline decoration-slate-300 hover:text-teal-700 dark:text-slate-100 dark:hover:text-teal-300"
                  title={s.url}
                >
                  <span className="truncate">{s.title}</span>
                  <ExternalLinkIcon />
                </a>
              ) : (
                <span className="truncate text-xs font-medium text-slate-800 dark:text-slate-100">{s.title}</span>
              )}
              <Pill label={t('widgets.sourceList.reliability', { value: s.reliability })} />
            </div>
            <p className="mt-0.5 line-clamp-2 text-[11px] text-slate-500 dark:text-slate-400">{s.snippet}</p>
            <span className="mono text-[10px] text-slate-400 dark:text-slate-500">{s.source}</span>
          </li>
        ))}
      </ul>
      {data.summary && <p className="mt-2 text-[11px] italic text-slate-500 dark:text-slate-400">{data.summary}</p>}
    </div>
  )
}

// --- image_card : résultat IMINT (détections sur prises de vue) ---
function ImageCardWidget({ data }: WidgetProps) {
  const t = useT()
  const images: any[] = data.images || []
  return (
    <div className="card border-l-4 border-l-violet-400 p-3">
      <div className="mb-2">
        <Pill label="IMINT" cls="text-violet-700 bg-violet-50 border-violet-200 dark:text-violet-300 dark:bg-violet-500/15 dark:border-violet-500/30" />
      </div>
      <div className="grid grid-cols-3 gap-2">
        {images.map((im, i) => (
          <div key={i} className="rounded-lg border border-slate-200 bg-slate-50 p-2 dark:border-ink-600 dark:bg-ink-700">
            <div className="mb-1 flex h-14 items-center justify-center rounded bg-gradient-to-br from-slate-100 to-slate-200 text-slate-400 dark:from-ink-600 dark:to-ink-500 dark:text-slate-500">
              ▦
            </div>
            <p className="truncate text-[11px] text-slate-700 dark:text-slate-300">{im.detection}</p>
            <span className="mono text-[10px] text-slate-400 dark:text-slate-500">
              {t('widgets.imageCard.confidence', { pct: Math.round((im.confidence || 0) * 100) })}
            </span>
          </div>
        ))}
      </div>
      {data.summary && <p className="mt-2 text-[11px] italic text-slate-500 dark:text-slate-400">{data.summary}</p>}
    </div>
  )
}

// --- map : résultat GEOINT (localisations sur une grille tactique) ---
function MapWidget({ data }: WidgetProps) {
  const locs: any[] = data.locations || []
  // Projection lat/lon fixe → boîte SVG (cohérente avec les coords de la fake data).
  const W = 280, H = 150
  const px = (lon: number) => ((lon - 0) / 5) * W
  const py = (lat: number) => H - ((lat - 46) / 5) * H
  return (
    <div className="card border-l-4 border-l-emerald-400 p-3">
      <div className="mb-2">
        <Pill label="GEOINT" cls="text-emerald-700 bg-emerald-50 border-emerald-200 dark:text-emerald-300 dark:bg-emerald-500/15 dark:border-emerald-500/30" />
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full rounded-lg border border-slate-200 bg-slate-50 dark:border-ink-600 dark:bg-ink-700">
        {/* grille : stroke piloté par classe (theme-aware), pas d'attribut en dur */}
        {[1, 2, 3, 4].map((i) => (
          <line key={`v${i}`} x1={(W / 5) * i} y1={0} x2={(W / 5) * i} y2={H} className="stroke-slate-200 dark:stroke-ink-500" />
        ))}
        {[1, 2, 3, 4].map((i) => (
          <line key={`h${i}`} x1={0} y1={(H / 5) * i} x2={W} y2={(H / 5) * i} className="stroke-slate-200 dark:stroke-ink-500" />
        ))}
        {locs.map((l, i) => (
          <g key={i}>
            <circle cx={px(l.lon)} cy={py(l.lat)} r={5} fill="#059669" opacity={0.9} />
            <circle cx={px(l.lon)} cy={py(l.lat)} r={9} fill="none" stroke="#059669" opacity={0.4} />
            <text x={px(l.lon) + 11} y={py(l.lat) + 3} className="fill-slate-600 dark:fill-slate-300" fontSize={9}>
              {l.name}
            </text>
          </g>
        ))}
      </svg>
      <ul className="mt-2 space-y-1">
        {locs.map((l, i) => (
          <li key={i} className="mono text-[10px] text-slate-500 dark:text-slate-400">
            {l.lat}, {l.lon} — {l.note}
          </li>
        ))}
      </ul>
      {data.summary && <p className="mt-1 text-[11px] italic text-slate-500 dark:text-slate-400">{data.summary}</p>}
    </div>
  )
}

// --- report : rapport de synthèse final ---
// Streaming : tant que `data.text` est vide (widget canonique pas encore reçu),
// on rend le buffer streamé par stream_id (= widget_id du futur widget). Quand
// le widget canonique arrive avec son texte complet, on bascule dessus.
function ReportWidget({ data, widgetId }: WidgetProps) {
  const t = useT()
  const stream = useReportStreamStore(
    (s) => (widgetId ? s.buffers[widgetId] : undefined),
  )
  const live = !data.text && stream && !stream.done
  const text = data.text || stream?.text || ''
  return (
    <div className="card border-l-4 border-l-teal-400 p-4">
      <div className="mb-2 flex items-center gap-2">
        <Pill label={t('widgets.labels.report')} cls="text-teal-700 bg-teal-50 border-teal-200 dark:text-teal-300 dark:bg-teal-500/15 dark:border-teal-500/30" />
        {live && (
          <span className="mono inline-flex items-center gap-1 text-[10px] text-teal-600 dark:text-teal-300">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-teal-500" />
            streaming…
          </span>
        )}
      </div>
      <Markdown>{text}</Markdown>
      {live && (
        <span className="mono ml-0.5 inline-block h-3 w-1.5 animate-pulse bg-teal-500/70 align-baseline" />
      )}
    </div>
  )
}

// --- panneau « Requêtes » : les recherches brutes qu'un agent A2A a lancées ---
// (tool calls MCP — ex. recherche fédérée). Replié par défaut :
// c'est de la transparence d'investigation, pas la réponse. Équivalent du JSON
// d'action affiché pour le data-management-agent, mais structuré par appel.
function QueriesPanel({ queries }: { queries: any[] }) {
  const t = useT()
  if (!Array.isArray(queries) || queries.length === 0) return null
  return (
    <details className="mt-2 rounded-lg border border-slate-200 bg-slate-50 dark:border-ink-600 dark:bg-ink-700">
      <summary className="mono cursor-pointer select-none px-2 py-1.5 text-[11px] font-semibold text-navy-700 dark:text-navy-200">
        {t('widgets.queries.title', { count: queries.length })}
      </summary>
      <ul className="space-y-1.5 px-2 pb-2">
        {queries.map((q, i) => (
          <li key={i} className="rounded border border-slate-200 bg-white px-2 py-1.5 dark:border-ink-600 dark:bg-ink-800">
            <div className="mono text-[11px] font-semibold text-navy-700 dark:text-navy-200">{q.tool || t('widgets.queries.tool')}</div>
            {q.args && Object.keys(q.args).length > 0 && (
              <pre className="mono mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words text-[10px] leading-relaxed text-slate-600 dark:text-slate-300">
                {JSON.stringify(q.args, null, 2)}
              </pre>
            )}
          </li>
        ))}
      </ul>
    </details>
  )
}

// Taille d'octets lisible (379 → « 379 B », 1536 → « 1.5 KB »). Fonction hors
// composant : on lit la langue courante via `translate` (les consommateurs se
// re-rendent au changement de drapeau, donc l'unité suit la langue en pratique).
function bytesHuman(n: number): string {
  if (typeof n !== 'number' || !isFinite(n)) return ''
  if (n < 1024) return translate('widgets.bytes.b', { n })
  if (n < 1024 * 1024) return translate('widgets.bytes.kb', { n: (n / 1024).toFixed(1) })
  return translate('widgets.bytes.mb', { n: (n / 1024 / 1024).toFixed(1) })
}

// Détecte un résultat d'action structuré du data-management-agent. Il arrive soit
// directement dans `data` (si l'orchestrateur le relaie un jour), soit sérialisé
// en JSON dans le texte de synthèse (cas actuel). Retourne l'objet ou null.
function asActionResult(data: any, text: string): any | null {
  const looksLikeAction = (o: any) =>
    o && typeof o === 'object' && (o.action || o.s3_upload || o.collection_created)
  if (looksLikeAction(data)) return data
  if (typeof text === 'string' && text.trim()[0] === '{') {
    try {
      const o = JSON.parse(text.trim())
      if (looksLikeAction(o)) return o
    } catch { /* pas du JSON exploitable */ }
  }
  return null
}

// --- data-management : carte d'action (ingestion S3 + collection créée) ---
// Rend lisible le JSON brut renvoyé par le data-management-agent : badge d'action,
// fichier versé sur S3 (nom/bucket/taille/lien), collection(s) créée(s), note.
function DataManagementCard({ data, corrId }: { data: any; corrId?: string }) {
  const t = useT()
  const s3 = data.s3_upload
  const note: string | undefined = data.note
  const cols: any[] = Array.isArray(data.collection_created)
    ? data.collection_created
    : data.collection_created ? [data.collection_created] : []
  return (
    <div className="card border-l-4 border-l-teal-500 p-3">
      <div className="mb-2 flex items-center gap-2">
        <Pill label="DATA-MGMT" cls="text-teal-700 bg-teal-50 border-teal-200 dark:text-teal-300 dark:bg-teal-500/15 dark:border-teal-500/30" />
        {data.action && (
          <span className="mono text-xs font-semibold uppercase text-teal-700 dark:text-teal-300">{data.action}</span>
        )}
        {corrId && <span className="mono ml-auto text-[10px] text-slate-400 dark:text-slate-500">{corrId.slice(0, 8)}</span>}
      </div>

      {s3 && (
        <div className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2 dark:border-ink-600 dark:bg-ink-700">
          <div className="flex items-center gap-2">
            <span>📄</span>
            <span className="mono truncate text-xs font-medium text-slate-800 dark:text-slate-100">{s3.objectKey || t('widgets.dataMgmt.file')}</span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 text-[10px] text-slate-500 dark:text-slate-400">
            {s3.bucketName && <span>{t('widgets.dataMgmt.bucket', { name: s3.bucketName })}</span>}
            {typeof s3.objectLength === 'number' && <span>· {bytesHuman(s3.objectLength)}</span>}
            {s3.mimeType && <span>· {s3.mimeType}</span>}
          </div>
          {s3.link && (
            <a href={s3.link} target="_blank" rel="noreferrer"
               className="mono mt-1 inline-flex items-center gap-1 text-[11px] text-teal-700 underline hover:text-teal-800 dark:text-teal-300 dark:hover:text-teal-200">
              {t('widgets.dataMgmt.download')}
            </a>
          )}
        </div>
      )}

      {cols.length > 0 && (
        <div className="mt-2 space-y-1.5">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
            {t('widgets.dataMgmt.collectionsCreated')}
          </div>
          {cols.map((c, i) => (
            <div key={i} className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 dark:border-ink-600 dark:bg-ink-800">
              <div className="flex items-center justify-between gap-2">
                <span className="mono truncate text-xs font-medium text-slate-800 dark:text-slate-100">{c.coreName || t('widgets.dataMgmt.collection')}</span>
                {c.coreType && <Pill label={c.coreType} />}
              </div>
              {c.coreDataId && <span className="mono text-[10px] text-slate-400 dark:text-slate-500">{c.coreDataId}</span>}
              {c.coreDescription && <p className="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400">{c.coreDescription}</p>}
            </div>
          ))}
        </div>
      )}

      {note && <p className="mt-2 text-[11px] italic text-slate-500 dark:text-slate-400">{note}</p>}
    </div>
  )
}

// --- markdown : widget de repli générique, utilisé notamment pour les
// résultats d'agents A2A (mi_search & co). Affiche le texte de synthèse, et,
// s'ils sont présents dans le payload : le panneau des requêtes brutes et un
// rappel des points poussés sur la carte du dashboard. Cas particulier : un
// résultat d'action data-management (JSON) → carte dédiée plutôt que JSON brut.
function MarkdownWidget({ data }: WidgetProps) {
  const t = useT()
  let text: string
  if (typeof data === 'string') text = data
  else text = data.text || data.summary || JSON.stringify(data)
  const agent: string | undefined = typeof data === 'object' ? data?.agent_type : undefined
  const corrId: string | undefined = typeof data === 'object' ? data?.corr_id : undefined
  const queries: any[] = typeof data === 'object' ? data?.queries || [] : []
  const locations: any[] = typeof data === 'object' ? data?.locations || [] : []
  const polygons: any[] = typeof data === 'object' ? data?.polygons || [] : []
  // On ne compte que les lieux RÉELLEMENT plaçables (lat/lon numériques) — même
  // filtre que GeoMap.extractLocations. Sinon un agent NER, qui renvoie des noms
  // de lieux sans coordonnées, ferait afficher « N points placés » sans marqueur.
  const mappable = locations.filter(
    (l) => typeof l?.lat === 'number' && typeof l?.lon === 'number')

  const action = asActionResult(data, text)
  if (action) return <DataManagementCard data={action} corrId={corrId} />

  return (
    <div className="card p-3">
      {(agent || corrId) && (
        <div className="mb-2 flex items-center justify-between text-[10px] uppercase tracking-wide">
          {agent && <span className="mono font-semibold text-navy-700 dark:text-navy-200">{agent}</span>}
          {corrId && <span className="mono text-slate-400 dark:text-slate-500">{corrId.slice(0, 8)}</span>}
        </div>
      )}
      <Markdown>{text}</Markdown>
      <QueriesPanel queries={queries} />
      {mappable.length > 0 && (
        <p className="mt-2 text-[11px] text-slate-500 dark:text-slate-400">
          {t('widgets.markdown.pointsOnMap', { count: mappable.length })}
        </p>
      )}
      {polygons.length > 0 && (
        <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
          {t('widgets.markdown.zonesOnMap', { count: polygons.length })}
        </p>
      )}
    </div>
  )
}

// --- memory_recall : ce que l'orchestrateur a CONSULTÉ pour ce tour ---
// Affichage compact : un 🧠 à gauche, et à droite chaque souvenir = son type +
// son contenu. Pas de ligne d'en-tête (pastille « MÉMOIRE » + compteur retirés).
function MemoryRecallWidget({ data }: WidgetProps) {
  const t = useT()
  const memories: any[] = data.memories || []
  const contextRefs: any[] = data.context_refs || []
  const refNames = contextRefs.map((r) => r.title).filter(Boolean).join(', ')
  // Rien consulté : ni souvenir, ni conversation référencée → ligne minimale.
  if (memories.length === 0 && contextRefs.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-dashed border-slate-300 bg-cream/70 px-3 py-1.5 text-[11px] italic text-slate-500 shadow-sm dark:border-ink-500 dark:bg-ink-800/60 dark:text-slate-400">
        <span aria-hidden>🧠</span>
        {t('widgets.memoryRecall.none')}
      </div>
    )
  }
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-cream/70 p-2 shadow-sm dark:border-ink-500 dark:bg-ink-800/60">
      {contextRefs.length > 0 && (
        <div className="mb-1.5 flex items-center gap-1.5 rounded-md border border-dashed border-sky-300 bg-sky-50/80 px-2 py-1 text-[11px] text-sky-800 dark:border-sky-700/60 dark:bg-sky-900/25 dark:text-sky-300">
          <span aria-hidden>📎</span>
          {t('widgets.memoryRecall.fromContext', { name: refNames })}
        </div>
      )}
      {memories.length > 0 && (
        <div className="flex items-start gap-2">
          <span className="mt-px shrink-0 text-[13px]" aria-hidden>🧠</span>
          <ul className="min-w-0 flex-1 space-y-1">
            {memories.map((m) => (
              <li key={m.id} className="text-[12px] leading-snug">
                <span className="mono mr-1.5 text-[9.5px] uppercase tracking-wider text-slate-400 dark:text-slate-500">
                  {m.kind}
                </span>
                <span className="text-slate-800 dark:text-slate-200">{m.content}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

// --- memory_proposal : faits extraits par le routeur LLM, à valider/rejeter par l'analyste ---
// La source de vérité de l'état décidé = `data.decisions` (map "index"→"saved"|"rejected"|
// "error") POSÉE par l'orchestrateur via `widget_update`. Ce patch survit au replay/refresh
// (event-sourcing). L'édition du contenu reste locale tant qu'on n'a pas validé. Si
// `onDecision` est fourni (par ChatPanel), la décision part en WS ; sinon repli POST direct
// pour les contextes sans canal WS (AnalystView legacy).
type MemoryDecision = { index: number; action: 'confirm' | 'reject'; kind?: string; content?: string }
export type MemoryProposalProps = WidgetProps & {
  onDecision?: (widgetId: string, decisions: MemoryDecision[]) => void
  widgetId?: string
}
export function MemoryProposalWidget({ data, onDecision, widgetId }: MemoryProposalProps) {
  const t = useT()
  const proposals: { kind: string; content: string }[] = data.proposals || []
  const serverDecisions: Record<string, string> = data.decisions || {}
  // Drafts locaux : le contenu peut être édité dans la textarea AVANT validation, sans
  // que ça contamine la source server (data.proposals reste l'orig). Indexé par row idx.
  const [drafts, setDrafts] = useState<Record<number, string>>({})
  // Décisions optimistes locales : le clic met l'état EN ATTENTE de la confirmation par
  // `widget_update`. À la réception du patch, `data.decisions` arrive et écrase l'optimiste.
  const [optimistic, setOptimistic] = useState<Record<number, 'saved' | 'rejected'>>({})
  const setOpt = (i: number, v: 'saved' | 'rejected') =>
    setOptimistic((prev) => ({ ...prev, [i]: v }))
  const statusFor = (i: number): 'pending' | 'saved' | 'rejected' | 'error' => {
    const s = (serverDecisions[String(i)] || optimistic[i] || 'pending') as
      'pending' | 'saved' | 'rejected' | 'error'
    return s
  }
  const contentFor = (i: number, p: { content: string }) =>
    drafts[i] !== undefined ? drafts[i] : p.content
  const confirm = async (i: number) => {
    const content = contentFor(i, proposals[i]).trim()
    const kind = proposals[i].kind
    if (!content) return
    setOpt(i, 'saved')
    if (onDecision && widgetId) {
      onDecision(widgetId, [{ index: i, action: 'confirm', kind, content }])
    } else {
      try { await api.createMemory({ content, kind }) }
      catch { setOptimistic((prev) => { const n = { ...prev }; delete n[i]; return n }) }
    }
  }
  const reject = (i: number) => {
    setOpt(i, 'rejected')
    if (onDecision && widgetId) onDecision(widgetId, [{ index: i, action: 'reject' }])
  }
  if (proposals.length === 0) return null
  return (
    // Compact : conteneur amber discret (= proposition mémoire), une proposition
    // par ligne. Épingle 📌 et en-tête « À MÉMORISER » retirés — le 🧠 + les boutons
    // ✓/✕ suffisent à signaler la nature actionnable.
    <div className="rounded-xl border border-dashed border-amber-300/70 bg-amber-50/60 p-1.5 shadow-sm dark:border-amber-500/30 dark:bg-amber-500/10">
      <ul className="space-y-1">
        {proposals.map((p, i) => {
          const status = statusFor(i)
          const content = contentFor(i, p)
          return (
            <li key={i} className="flex items-start gap-2 px-1 py-0.5">
              <span className="mt-0.5 shrink-0 text-[12px]" aria-hidden>🧠</span>
              <div className="min-w-0 flex-1">
                <span className="mono mr-1.5 align-middle text-[9px] uppercase tracking-wider text-amber-700 dark:text-amber-300">
                  {p.kind}
                </span>
                {status === 'pending' ? (
                  <textarea
                    value={content}
                    onChange={(e) => setDrafts((prev) => ({ ...prev, [i]: e.target.value }))}
                    rows={Math.max(1, Math.ceil(content.length / 80))}
                    className="mt-1 w-full resize-y rounded border border-amber-200/60 bg-white/80 px-1.5 py-1 text-[12.5px] leading-snug text-slate-800 focus:border-amber-400 focus:outline-none focus:ring-1 focus:ring-amber-300 dark:border-amber-500/20 dark:bg-ink-700/60 dark:text-slate-100 dark:focus:border-amber-400 dark:focus:ring-amber-500/40"
                  />
                ) : (
                  <span
                    className={`align-middle text-[12.5px] leading-snug text-slate-800 dark:text-slate-100 ${status === 'rejected' ? 'line-through opacity-50' : ''}`}
                  >
                    {content}
                  </span>
                )}
              </div>
              <div className="mt-0.5 flex shrink-0 items-center gap-1">
                {status === 'pending' && (
                  <>
                    <button
                      onClick={() => reject(i)}
                      title={t('widgets.memoryProposal.reject')}
                      aria-label={t('widgets.memoryProposal.reject')}
                      className="flex h-5 w-5 items-center justify-center rounded border border-slate-300/70 bg-white/60 text-[11px] text-slate-500 transition hover:border-red-300 hover:bg-red-50 hover:text-red-700 dark:border-ink-500 dark:bg-ink-700/40 dark:text-slate-300 dark:hover:border-red-500/40 dark:hover:bg-red-500/15 dark:hover:text-red-400"
                    >
                      ✕
                    </button>
                    <button
                      onClick={() => confirm(i)}
                      disabled={!content.trim()}
                      title={t('widgets.memoryProposal.confirm')}
                      aria-label={t('widgets.memoryProposal.confirm')}
                      className="flex h-5 w-5 items-center justify-center rounded bg-emerald-600 text-[11px] font-semibold text-white shadow-sm transition hover:bg-emerald-700 disabled:opacity-40 dark:bg-emerald-500 dark:hover:bg-emerald-400"
                    >
                      ✓
                    </button>
                  </>
                )}
                {status === 'saved' && (
                  <span title={t('widgets.memoryProposal.saved')} className="text-[12px] leading-none text-emerald-600 dark:text-emerald-400">
                    ✓
                  </span>
                )}
                {status === 'rejected' && (
                  <span title={t('widgets.memoryProposal.rejected')} className="text-[11px] leading-none text-slate-400 dark:text-slate-500">
                    ✕
                  </span>
                )}
                {status === 'error' && (
                  <span title={t('widgets.memoryProposal.error')} className="text-[11px] font-bold leading-none text-red-500 dark:text-red-400">
                    !
                  </span>
                )}
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

// Registre : widget_type → composant. C'est le point d'extension du système.
// Widgets métier Assemblée nationale : vote_chart / fiche_depute / timeline_loi
// (payloads validés par Zod — cf. lib/widgetSchemas ; repli WidgetErrorCard).
export const WIDGETS: Record<string, React.FC<WidgetProps>> = {
  task_progress: TaskProgressWidget,
  source_list: SourceListWidget,
  image_card: ImageCardWidget,
  map: MapWidget,
  report: ReportWidget,
  markdown: MarkdownWidget,
  memory_recall: MemoryRecallWidget,
  memory_proposal: MemoryProposalWidget,
  vote_chart: ({ data }) => <VoteChart data={data} />,
  fiche_depute: ({ data }) => <FicheDepute data={data} />,
  timeline_loi: ({ data }) => <TimelineLoi data={data} />,
}

// Types de widgets métier (résultats d'agents) — consommé par InvestigationFeed
// pour les rendre dans le fil (au lieu de les avaler comme « widget inconnu »).
export const DOMAIN_WIDGET_TYPES = ['vote_chart', 'fiche_depute', 'timeline_loi'] as const

// Ré-export pratique du repli d'erreur (utilisé par le fil).
export { WidgetErrorCard }

// Instancie le widget correspondant au type (repli sur `markdown` si inconnu).
export function renderWidget(
  type: string,
  data: any,
  progress?: Progress,
  traces?: Progress[],
  widgetId?: string,
) {
  const Widget = WIDGETS[type] || MarkdownWidget
  return <Widget data={data} progress={progress} traces={traces} widgetId={widgetId} />
}
