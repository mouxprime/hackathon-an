// Fil d'investigation réutilisable : rend le déroulé FSM (plan → agents →
// rapport) d'une conversation orchestrateur. Extrait de ChatPanel pour être
// partagé entre le chat du dashboard ET les salons d'état-major (ChannelPanel,
// mode @hemicycle). Rendu PUR : il reçoit events/progress/traces/… en props et
// renvoie les blocs sémantiques (PlanCard, AgentCard, rapport, synthèse live).
//
// `suppressUserMessages` : en mode channel, la question de l'analyste est déjà
// affichée comme bulle de channel → on n'affiche pas le doublon (echo FSM).

import type React from 'react'
import { useMemo, useState } from 'react'
import { useT } from '../lib/i18n'
import { useReportStreamStore } from '../lib/store'
import type { PlanStep, Progress, UIEvent } from '../lib/types'
import { agentLabel } from '../lib/ui'
import { traceCallLabel } from '../lib/traceLabels'
import { AUTO_APPROVE_PLAN } from '../lib/hooks'
import { Markdown } from './Markdown'
import { RecycleIcon } from './ContextPanel'
import { renderWidget, MemoryProposalWidget, ExternalLinkIcon, DOMAIN_WIDGET_TYPES } from '../widgets'
import { Citation } from './Citation'
import type { SourceItem } from './SourcePopover'

// Types de widgets métier rendus DANS le fil (résultats d'agents Assemblée).
const DOMAIN_TYPES = new Set<string>(DOMAIN_WIDGET_TYPES)

type Attach = { id: string; filename: string }

// --- Icônes & actions de message (charte « Hémicycle ») ---

function ClipboardIcon({ className = 'h-3.5 w-3.5' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M5 15V5a2 2 0 0 1 2-2h8" />
    </svg>
  )
}

function CheckIcon({ className = 'h-3.5 w-3.5' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 6L9 17l-5-5" />
    </svg>
  )
}

function PencilIcon({ className = 'h-3.5 w-3.5' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
    </svg>
  )
}

// Bouton « copier la réponse » sous une bulle de l'orchestrateur. Apparaît au
// survol (group-hover du conteneur de message) ; coche brève au clic.
function CopyButton({ text, className = '' }: { text: string; className?: string }) {
  const t = useT()
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard?.writeText(text)
        setCopied(true)
        setTimeout(() => setCopied(false), 1200)
      }}
      title={t('chat.copyAnswer')}
      aria-label={t('chat.copyAnswer')}
      className={`rounded p-1 text-slate-400 opacity-0 transition hover:bg-slate-100 hover:text-teal-600 group-hover:opacity-100 dark:text-slate-500 dark:hover:bg-ink-700 dark:hover:text-teal-300 ${className}`}
    >
      {copied ? <CheckIcon /> : <ClipboardIcon />}
    </button>
  )
}

// Pied de réponse « Sources » repliable : liste les sources citées (puces ①②③
// avec popover au survol). GÉNÉRALISÉ : affiché sous TOUT widget résultat dont
// `data.sources` est non vide (pas seulement source_list). Si une source porte
// une `url`, son titre devient un lien officiel cliquable (IA de confiance).
function SourcesFooter({ sources }: { sources: SourceItem[] }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  return (
    <div className="ml-1 mt-1 max-w-[85%] animate-fadeInUp">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex items-center gap-1 text-[11px] font-medium text-teal-600 transition hover:text-teal-700 dark:text-teal-400 dark:hover:text-teal-300"
      >
        <svg viewBox="0 0 24 24" className={`h-3 w-3 transition-transform ${open ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M9 6l6 6-6 6" />
        </svg>
        {t('chat.sourcesSectionLabel')} ({sources.length})
      </button>
      {open && (
        <div className="mt-1.5 space-y-1.5">
          {sources.map((s, i) => (
            <div key={i} className="flex items-start gap-2 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 dark:border-ink-600 dark:bg-ink-800">
              <span className="mt-0.5"><Citation index={i + 1} source={s} /></span>
              <div className="min-w-0">
                {s.url ? (
                  <a
                    href={s.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex max-w-full items-center gap-1 truncate text-[11.5px] font-medium text-slate-700 underline decoration-slate-300 hover:text-teal-700 dark:text-slate-200 dark:hover:text-teal-300"
                    title={s.url}
                  >
                    <span className="truncate">{s.title || s.source || s.url}</span>
                    <ExternalLinkIcon />
                  </a>
                ) : (
                  <div className="truncate text-[11.5px] font-medium text-slate-700 dark:text-slate-200" title={s.title}>{s.title || s.source || '—'}</div>
                )}
                {s.snippet && <p className="line-clamp-2 text-[10.5px] text-slate-500 dark:text-slate-400">{s.snippet}</p>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export type TimelineItem =
  | { kind: 'message'; seq: number; role: string; text: string; msgKind?: string; attachments?: Attach[]; reportWidgetId?: string; filename?: string }
  | { kind: 'status'; seq: number; state: string }
  | { kind: 'widget'; seq: number; widget_id: string; widget_type: string; data: any }

export function buildTimeline(events: UIEvent[]): TimelineItem[] {
  const items: TimelineItem[] = []
  const byId: Record<string, any> = {}
  for (const ev of events) {
    if (ev.type === 'message') {
      items.push({ kind: 'message', seq: ev.seq, role: ev.payload.role, text: ev.payload.text,
                   msgKind: ev.payload.kind, attachments: ev.payload.attachments,
                   reportWidgetId: ev.payload.report_widget_id, filename: ev.payload.filename })
    } else if (ev.type === 'status') {
      items.push({ kind: 'status', seq: ev.seq, state: ev.payload.state })
    } else if (ev.type === 'widget') {
      const item: any = {
        kind: 'widget', seq: ev.seq, widget_id: ev.payload.widget_id,
        widget_type: ev.payload.widget_type, data: { ...ev.payload.data },
      }
      byId[ev.payload.widget_id] = item
      items.push(item)
    } else if (ev.type === 'widget_update') {
      const item = byId[ev.payload.widget_id]
      if (item) Object.assign(item.data, ev.payload.patch)
    }
  }
  return items
}

type InvestigationFeedProps = {
  events: UIEvent[]
  progress: Record<string, Progress>
  traces: Progress[]
  thinking: Record<string, { text: string; done: boolean }>
  assistantStream: Record<string, { text: string; done: boolean }>
  reportEnabled: boolean
  planFocus: 'approve' | 'modify'
  pendingPlanSeq: number | null
  onApprovePlan: (steps: PlanStep[]) => void
  onModifyPlan: (steps: PlanStep[]) => void
  onMemoryDecision: (
    widgetId: string,
    decisions: { index: number; action: 'confirm' | 'reject'; kind?: string; content?: string }[],
  ) => void
  suppressUserMessages?: boolean
  // Clic sur le badge 📎 d'une pièce jointe → ouvre le lecteur de fichier.
  onOpenAttachment?: (att: Attach) => void
  // Si fourni, chaque message porte au survol une action « transférer vers un
  // salon » (chat privé orchestrateur → channel). `role` = 'user' | 'assistant'.
  onTransferToChannel?: (msg: { role: string; text: string }) => void
}

export function InvestigationFeed({ events, progress, traces, thinking, assistantStream, reportEnabled, planFocus, pendingPlanSeq, onApprovePlan, onModifyPlan, onMemoryDecision, suppressUserMessages, onTransferToChannel, onOpenAttachment }: InvestigationFeedProps) {
  const t = useT()
  // Buffers de rapports streamés (kind=report_chunk, stream_id=report-t{turn}) :
  // pendant WRITING_REPORT en régime « rapport complet » (force_report), le rapport
  // s'écrit ici token-par-token. On l'affiche dans le fil pour combler le « trou »
  // entre la fin des agents et l'apparition de la carte rapport.
  const reportBuffers = useReportStreamStore((s) => s.buffers)
  const timeline = useMemo(() => buildTimeline(events), [events])
  const out = (() => {
          const stepIdxByCorr = new Map<string, number>()
          const taskByCorr = new Map<string, TimelineItem & { kind: 'widget' }>()
          const resultByCorr = new Map<string, TimelineItem & { kind: 'widget' }>()
          // Les étapes se comptent PAR TOUR : chaque plan_proposal ouvre un nouveau
          // tour et remet dispatchOrder à 0. Sans ce reset, le compteur courait sur
          // TOUTE la conversation — le tour N reprenait « Step 7 » là où le tour N-1
          // s'était arrêté (au lieu de repartir à Step 1), et la PlanCard du tour N
          // mappait ses étapes sur les corr_id du tour N-1 (corrByIdx global décalé).
          // corrIdsByPlanSeq garde, par plan, la liste ordonnée des corr_id du tour
          // → la PlanCard reconstruit son pont étape→tâche sur SON propre tour.
          const corrIdsByPlanSeq = new Map<number, string[]>()
          let curPlanSeq = -1
          let dispatchOrder = 0
          for (const it of timeline) {
            if (it.kind !== 'widget') continue
            if (it.widget_type === 'plan_proposal') {
              curPlanSeq = it.seq
              dispatchOrder = 0
              if (!corrIdsByPlanSeq.has(curPlanSeq)) corrIdsByPlanSeq.set(curPlanSeq, [])
            } else if (it.widget_type === 'task_progress') {
              const cid = (it.data?.corr_id as string) || it.widget_id
              if (!stepIdxByCorr.has(cid)) {
                stepIdxByCorr.set(cid, dispatchOrder++)
                corrIdsByPlanSeq.get(curPlanSeq)?.push(cid)
              }
              taskByCorr.set(cid, it)
            } else if (it.widget_id.endsWith('-result')) {
              const cid = it.widget_id.replace(/-result$/, '')
              resultByCorr.set(cid, it)
            }
          }
          const userSeqsSorted = timeline
            .filter((it) => it.kind === 'message' && it.role === 'user')
            .map((it) => it.seq)
            .sort((a, b) => a - b)

          let lastStatus: string | null = null
          for (let i = timeline.length - 1; i >= 0; i--) {
            const it = timeline[i]
            if (it.kind === 'status') { lastStatus = it.state; break }
          }
          const reportSeen = timeline.some((it) => it.kind === 'widget' && it.widget_type === 'report')
          const reportStatus: 'pending' | 'running' | 'done' =
            reportSeen ? 'done' : lastStatus === 'WRITING_REPORT' ? 'running' : 'pending'
          let lastUserSeq = -1
          for (let i = timeline.length - 1; i >= 0; i--) {
            const it = timeline[i]
            if (it.kind === 'message' && it.role === 'user') { lastUserSeq = it.seq; break }
          }
          const planAfterUser = timeline.some(
            (it) => it.seq > lastUserSeq && it.kind === 'widget' && it.widget_type === 'plan_proposal',
          )
          const assistantAfterUser = timeline.some(
            (it) => it.seq > lastUserSeq && it.kind === 'message' && it.role !== 'user' && it.msgKind !== 'compaction',
          )
          // Rapport canonique du TOUR courant déjà arrivé ? (scopé au dernier message
          // user — dans le chat classique, un seul feed rend toute la conv, donc un
          // rapport d'un tour antérieur ne doit pas masquer le streaming du tour suivant.)
          const reportAfterUser = timeline.some(
            (it) => it.seq > lastUserSeq && it.kind === 'widget' && it.widget_type === 'report',
          )
          // « Preparing plan » UNIQUEMENT pendant la planification réelle (état FSM
          // PLANNING). Sans ce garde, il clignotait aussi entre les messages lors des
          // tours de reprise (input_required : point carte posé, réponse enum → état
          // AWAITING_SUBAGENTS, pas PLANNING) en réaffichant le raisonnement périmé du
          // tour précédent — d'où des blocs sporadiques « sans contexte ».
          const showPreparing = lastStatus === 'PLANNING' && lastUserSeq >= 0 && !planAfterUser && !assistantAfterUser
          // Synthèse de chat streamée du tour courant (kind=message_chunk) : on prend le
          // stream du turn le plus élevé. Affiché tant que le message canonique (durable)
          // n'est pas encore arrivé — il prend alors le relais sans transition.
          let liveSummary = ''
          let liveSummaryTurn = -1
          let liveSummaryDone = false
          for (const [sid, v] of Object.entries(assistantStream)) {
            if (!v.text) continue
            const m = /-t(\d+)$/.exec(sid)
            const tn = m ? Number(m[1]) : 0
            if (tn >= liveSummaryTurn) { liveSummaryTurn = tn; liveSummary = v.text; liveSummaryDone = v.done }
          }
          // Rapport complet streamé (force_report) du tour le plus avancé. Même
          // logique que la synthèse : tant que la carte rapport canonique n'est pas
          // arrivée, on rend ce buffer token-par-token.
          let liveReport = ''
          let liveReportTurn = -1
          let liveReportDone = false
          for (const [sid, v] of Object.entries(reportBuffers)) {
            const m = /report-t(\d+)$/.exec(sid)
            if (!m) continue
            const tn = Number(m[1])
            if (tn >= liveReportTurn) { liveReportTurn = tn; liveReport = v.text; liveReportDone = v.done }
          }

          const out: React.ReactNode[] = []
          const rendered = new Set<string>()
          for (const item of timeline) {
            if (item.kind === 'message') {
              if (suppressUserMessages && item.role === 'user') continue
              if (item.msgKind === 'compaction') {
                out.push(<div key={item.seq} className="animate-fadeInUp"><CompactionCard text={item.text} /></div>)
                continue
              }
              if (item.msgKind === 'report_ready') {
                out.push(
                  <div key={item.seq} className="animate-fadeInUp">
                    <ReportReadyCard
                      text={item.text}
                      reportWidgetId={item.reportWidgetId}
                      filename={item.filename}
                      events={events}
                    />
                  </div>,
                )
                continue
              }
              const isUser = item.role === 'user'
              const atts = item.attachments || []
              // Action « transférer vers un salon » au survol (chat privé → channel).
              const transferBtn = onTransferToChannel && item.text ? (
                <button
                  onClick={() => onTransferToChannel({ role: item.role, text: item.text })}
                  className="shrink-0 self-center rounded px-1 py-0.5 text-[13px] leading-none text-slate-400 opacity-0 transition hover:text-navy-700 group-hover:opacity-100 dark:text-slate-500 dark:hover:text-navy-200"
                  title={t('channels.transfer')}
                  aria-label={t('channels.transfer')}
                >
                  ⤳
                </button>
              ) : null
              out.push(
                <div key={item.seq} className={`group flex animate-fadeInUp items-end gap-1 ${isUser ? 'justify-end' : 'justify-start'}`}>
                  {isUser && transferBtn}
                  {/* Crayon d'édition (maquette) — TODO édition : re-soumettre un message édité. */}
                  {isUser && !suppressUserMessages && (
                    <button
                      type="button"
                      title={t('chat.editMessage')}
                      aria-label={t('chat.editMessage')}
                      className="shrink-0 self-center rounded p-1 text-slate-400 opacity-0 transition hover:text-teal-600 group-hover:opacity-100 dark:text-slate-500 dark:hover:text-teal-300"
                    >
                      <PencilIcon />
                    </button>
                  )}
                  <div className={`flex max-w-[85%] flex-col ${isUser ? 'items-end' : 'items-start'}`}>
                    {/* Label d'auteur « Hémicycle » au-dessus des réponses de
                        l'orchestrateur, en miroir du label de cellule des bulles de salon. */}
                    {!isUser && (
                      <span className="mb-0.5 px-1 text-[9px] font-medium text-teal-600 dark:text-teal-400">{t('header.appName')}</span>
                    )}
                    <div
                      className={`break-words rounded-2xl px-3.5 py-2 text-[13px] leading-relaxed ${
                        isUser
                          // Bulle de l'analyste : teal clair à droite (charte maquette).
                          ? 'rounded-br-md bg-teal-100 text-slate-800 shadow-xs dark:bg-teal-500/20 dark:text-slate-100'
                          // Réponse de l'orchestrateur : carte blanche neutre à gauche.
                          : 'rounded-bl-md border border-slate-200 bg-white text-slate-800 shadow-card dark:border-ink-600 dark:bg-ink-800 dark:text-slate-100'
                      }`}
                    >
                      {isUser ? item.text : <Markdown>{item.text}</Markdown>}
                      {atts.length > 0 && (
                        <div className="mt-1.5 flex flex-wrap gap-1">
                          {atts.map((a) => (
                            <button
                              key={a.id}
                              type="button"
                              onClick={() => onOpenAttachment?.(a)}
                              title={onOpenAttachment ? `Ouvrir ${a.filename}` : a.filename}
                              className={`mono rounded px-1.5 py-0.5 text-[10px] transition ${
                                onOpenAttachment ? 'cursor-pointer' : 'cursor-default'
                              } ${
                                isUser
                                  ? 'border border-teal-300/70 bg-white/70 text-teal-800 hover:bg-white dark:border-teal-400/40 dark:bg-ink-700 dark:text-teal-200'
                                  : 'border border-slate-200 dark:border-ink-600 bg-slate-50 dark:bg-ink-700 text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-ink-600'
                              }`}
                            >
                              📎 {a.filename}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                    {/* Copier la réponse — sous la carte de l'orchestrateur (survol). */}
                    {!isUser && <CopyButton text={item.text} className="mt-0.5 self-start" />}
                  </div>
                  {!isUser && transferBtn}
                </div>,
              )
              // Sous une réponse de l'orchestrateur : sources citées (footnotes)
              // déduites du widget source_list du MÊME tour (entre ce message et
              // le prochain message de l'analyste). Les widgets métier rendus
              // dans le fil portent leur PROPRE SourcesFooter (cf. plus bas).
              if (!isUser) {
                const nextUserSeq = userSeqsSorted.find((s) => s > item.seq) ?? Infinity
                const srcWidget = timeline.find(
                  (it) => it.kind === 'widget' && it.widget_type === 'source_list'
                    && it.seq > item.seq && it.seq < nextUserSeq,
                ) as Extract<TimelineItem, { kind: 'widget' }> | undefined
                const sources = srcWidget?.data?.sources
                if (Array.isArray(sources) && sources.length > 0) {
                  out.push(<SourcesFooter key={`src-${item.seq}`} sources={sources} />)
                }
              }
              continue
            }
            if (item.kind === 'status') continue
            if (item.widget_type === 'report') continue   // → widget texteditor
            if (item.widget_type === 'plan_proposal') {
              out.push(
                <PlanCard
                  key={item.seq}
                  data={item.data}
                  corrIds={corrIdsByPlanSeq.get(item.seq) ?? []}
                  taskByCorr={taskByCorr}
                  resultByCorr={resultByCorr}
                  reportEnabled={reportEnabled}
                  reportStatus={reportStatus}
                  onApprove={onApprovePlan}
                  onModify={onModifyPlan}
                  focus={item.seq === pendingPlanSeq ? planFocus : null}
                />,
              )
              continue
            }
            if (item.widget_type === 'task_progress') {
              const cid = (item.data?.corr_id as string) || item.widget_id
              if (rendered.has(cid)) continue
              rendered.add(cid)
              const taskItem = taskByCorr.get(cid) || item
              const resultItem = resultByCorr.get(cid)
              const stepIdx = stepIdxByCorr.get(cid) ?? 0
              out.push(
                <AgentCard
                  key={cid}
                  stepIdx={stepIdx}
                  task={taskItem.data}
                  result={resultItem?.data}
                  traces={traces.filter((tr) => tr.corr_id === cid)}
                  progress={progress[cid]}
                  liveText={thinking[`draft-${cid}`]?.text || thinking[`a2a-${cid}`]?.text}
                />,
              )
              // Résultat métier (vote_chart / fiche_depute / timeline_loi) : le
              // widget suffixé `-result` était historiquement ABSORBÉ sous
              // l'AgentCard — on le rend désormais dans le fil, sous la carte,
              // avec son pied de sources (généralisation du SourcesFooter).
              if (resultItem && DOMAIN_TYPES.has(resultItem.widget_type)) {
                out.push(
                  <div key={`${cid}-domain`} className="animate-fadeInUp px-1">
                    {renderWidget(resultItem.widget_type, resultItem.data)}
                  </div>,
                )
                const srcs = resultItem.data?.sources
                if (Array.isArray(srcs) && srcs.length > 0) {
                  out.push(<SourcesFooter key={`${cid}-domain-src`} sources={srcs} />)
                }
              } else if (resultItem && resultItem.widget_type !== 'source_list'
                         && Array.isArray(resultItem.data?.sources) && resultItem.data.sources.length > 0) {
                // Tout AUTRE widget résultat porteur de sources : pied de sources
                // généralisé (source_list garde son rendu historique sous la
                // réponse de l'orchestrateur, cf. bloc message plus haut).
                out.push(<SourcesFooter key={`${cid}-src`} sources={resultItem.data.sources} />)
              }
              continue
            }
            // Widget mémoire « rappel » : info passive, rendu via le registre.
            if (item.widget_type === 'memory_recall') {
              out.push(
                <div key={item.seq} className="px-1 pb-2">
                  {renderWidget(item.widget_type, item.data)}
                </div>,
              )
              continue
            }
            // Widget mémoire « proposition » : actionnable → on rend directement le
            // composant avec le callback WS (la décision part en round-trip vers la FSM
            // qui crée la LtmMemory et patche le widget — l'état survit au refresh).
            if (item.widget_type === 'memory_proposal') {
              out.push(
                <div key={item.seq} className="px-1 pb-2">
                  <MemoryProposalWidget
                    data={item.data}
                    widgetId={item.widget_id}
                    onDecision={onMemoryDecision}
                  />
                </div>,
              )
              continue
            }
            // Widgets métier Assemblée émis HORS résultat d'agent (directement par
            // l'orchestrateur) : rendus dans le fil + pied de sources. Les widgets
            // `-result` rattachés à une tâche sont déjà rendus sous leur AgentCard
            // (cf. branche task_progress) — on ne les double pas ici.
            if (DOMAIN_TYPES.has(item.widget_type)) {
              const isResult = item.widget_id.endsWith('-result')
              const cid = item.widget_id.replace(/-result$/, '')
              const attachedToTask = isResult && taskByCorr.has(cid)
              if (!attachedToTask) {
                out.push(
                  <div key={`w-${item.seq}`} className="animate-fadeInUp px-1">
                    {renderWidget(item.widget_type, item.data)}
                  </div>,
                )
                const srcs = item.data?.sources
                if (Array.isArray(srcs) && srcs.length > 0) {
                  out.push(<SourcesFooter key={`w-${item.seq}-src`} sources={srcs} />)
                }
              }
              continue
            }
            // Tout autre widget (résultats déjà absorbés par AgentCard, divers
            // widgets server-driven non liés au déroulé) → silencieux dans le fil.
          }
          // Bubble assistant live : la synthèse de chat se rédige token-par-token.
          // Masqué dès qu'un message assistant canonique du tour est arrivé.
          if (liveSummary && !assistantAfterUser) {
            out.push(
              <div key="live-summary" className="flex animate-fadeInUp justify-start">
                <div className="max-w-[85%] break-words rounded-2xl rounded-bl-md border border-slate-200 dark:border-ink-600 bg-white dark:bg-ink-800 px-3.5 py-2 text-[13px] leading-relaxed text-slate-800 dark:text-slate-100 shadow-card">
                  <Markdown>{liveSummary}</Markdown>
                  {/* Curseur d'écriture seulement TANT QUE le flux n'est pas terminé
                      (done=true à l'EOF du LLM) — sinon il clignotait jusqu'à l'arrivée
                      du message canonique, donnant l'impression d'une écriture sans fin. */}
                  {!liveSummaryDone && (
                    <span className="ml-0.5 inline-block h-3.5 w-1 animate-pulse bg-navy-400 align-middle dark:bg-navy-500" aria-hidden />
                  )}
                </div>
              </div>,
            )
          } else if (liveReport && !reportAfterUser) {
            // Régime rapport complet (force_report) : le rapport s'écrit token-par-token.
            // Remplacé sans transition par la ReportReadyCard / le message canonique.
            out.push(<WritingReport key="live-report" text={liveReport} done={liveReportDone} />)
          } else if (lastStatus === 'WRITING_REPORT' && !reportAfterUser && !assistantAfterUser) {
            // WRITING_REPORT actif mais aucun token encore arrivé (latence du LLM) :
            // on affiche immédiatement le bandeau « rédaction » pour combler le trou.
            out.push(<WritingReport key="writing-report" text="" done={false} />)
          } else if (showPreparing) {
            // Bandeau de préparation + raisonnement de planification streamé en live
            // (kind=thinking, stream_id="planning" — émis par l'orchestrateur quand
            // `thinking` est activé). Vide tant qu'aucun token n'est arrivé.
            out.push(<PreparingPlan key="preparing" thinking={thinking['planning']?.text || ''} />)
          }
          return out
  })()
  // space-y-3 PROPRE au feed : dans le chat orchestrateur les blocs héritaient du
  // space-y-3 du conteneur de scroll, mais en SALON chaque tour est rendu dans une
  // seule <div> (ChannelPanel) → les blocs (plan, étapes/agents, état, rédaction de
  // la réponse) se retrouvaient collés. On porte l'espacement sur le feed lui-même.
  return <div className="space-y-3">{out}</div>
}

// ─── Atomes du nouveau déroulé ──────────────────────────────────────────────

// Spinner SVG discret (cercle qui tourne). Utilisé dans les en-têtes d'étape
// running et dans la PlanRow active.
function Spinner({ size = 12, color = 'currentColor' }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className="shrink-0" aria-hidden>
      <circle cx="12" cy="12" r="9" fill="none" stroke={color} strokeOpacity="0.18" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke={color} strokeWidth="3" strokeLinecap="round">
        <animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="0.9s" repeatCount="indefinite" />
      </path>
    </svg>
  )
}

// Trois points qui pulsent par décalage (signal « live, sans texte »).
function ThinkingDots({ className = '' }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-0.5 ${className}`} aria-hidden>
      <span className="h-1 w-1 rounded-full bg-current animate-pulse" style={{ animationDelay: '0ms', animationDuration: '1.4s' }} />
      <span className="h-1 w-1 rounded-full bg-current animate-pulse" style={{ animationDelay: '200ms', animationDuration: '1.4s' }} />
      <span className="h-1 w-1 rounded-full bg-current animate-pulse" style={{ animationDelay: '400ms', animationDuration: '1.4s' }} />
    </span>
  )
}

// Puce d'état :
//   • orange  = en cours (scintille via animate-ping)
//   • vert    = succès   (statique)
//   • rouge   = échec    (statique)
// Seul `running` pulse — un état terminé reste stable pour ne pas distraire.
function StatusChip({ state }: { state: 'running' | 'done' | 'failed' | 'waiting' }) {
  const color =
    state === 'done' ? 'bg-emerald-500'
      : state === 'failed' ? 'bg-red-500'
      : state === 'waiting' ? 'bg-sky-500'   // attente d'une saisie analyste (pas « en cours »)
      : 'bg-amber-500'
  return (
    <span className="relative inline-flex h-2.5 w-2.5 shrink-0" aria-hidden>
      {state === 'running' && (
        <span className="absolute inset-0 rounded-full bg-amber-500/60 animate-ping" />
      )}
      <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${color}`} />
    </span>
  )
}

// Icône check (étape done).
function IconCheck({ className = 'h-3 w-3' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}
         strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
      <path d="M20 6 9 17l-5-5" />
    </svg>
  )
}

// Icône éclair (plan, en-tête).
function IconBolt({ className = 'h-3.5 w-3.5' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
         strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
      <path d="M13 2 4 14h7l-1 8 9-12h-7z" />
    </svg>
  )
}

// Icône cerveau (PreparingPlan).
function IconBrain({ className = 'h-3.5 w-3.5' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
         strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
      <path d="M12 4a3 3 0 0 0-3 3v10a3 3 0 0 0 6 0V7a3 3 0 0 0-3-3z" />
      <path d="M9 7a3 3 0 0 0-3 3 3 3 0 0 0 3 3M15 7a3 3 0 0 1 3 3 3 3 0 0 1-3 3" />
    </svg>
  )
}

// ─── PreparingPlan : bandeau « l'orchestrateur réfléchit » ──────────────────
// S'affiche dans le créneau entre le message user et l'apparition du plan.
function PreparingPlan({ thinking = '' }: { thinking?: string }) {
  const t = useT()
  return (
    <div className="animate-fadeInUp rounded-xl border border-navy-200 dark:border-navy-500/30 bg-navy-50/50 dark:bg-navy-500/10 px-3 py-2">
      <div className="flex items-center gap-2">
        <IconBrain className="h-3.5 w-3.5 text-navy-600 dark:text-navy-300" />
        <span className="mono text-[10px] font-semibold uppercase tracking-wide text-navy-700 dark:text-navy-200">
          {t('chat.preparingPlan')}
        </span>
        <ThinkingDots className="text-navy-400 dark:text-navy-500" />
      </div>
      {/* Raisonnement de planification streamé token-par-token, en Markdown
          incrémental (mise en forme appliquée au fil de l'eau). */}
      {thinking && (
        <div className="mt-2 max-h-48 overflow-y-auto break-words border-t border-navy-200/60 pt-2 text-[11px] leading-relaxed text-slate-600 dark:border-navy-500/20 dark:text-slate-300 [&_*]:my-0.5">
          <Markdown>{thinking}</Markdown>
          <span className="ml-0.5 inline-block h-3 w-1 animate-pulse bg-navy-400 align-middle dark:bg-navy-500" aria-hidden />
        </div>
      )}
    </div>
  )
}

// ─── WritingReport : bandeau « rédaction de la réponse » streamée ───────────
// Comble le créneau entre la fin des agents et l'apparition du rapport canonique.
// Affiche le texte du rapport qui se rédige token-par-token (régime force_report) ;
// avant le premier token, seul le bandeau + dots sont visibles (pas de page blanche).
function WritingReport({ text, done }: { text: string; done: boolean }) {
  const t = useT()
  return (
    <div className="animate-fadeInUp rounded-xl border border-teal-200 dark:border-teal-500/40 bg-teal-50/60 dark:bg-teal-500/10 px-3 py-2">
      <div className="flex items-center gap-2">
        <span className="text-sm" aria-hidden>📄</span>
        <span className="mono text-[10px] font-semibold uppercase tracking-wide text-teal-700 dark:text-teal-300">
          {t('chat.writingReport')}
        </span>
        <ThinkingDots className="text-teal-500 dark:text-teal-400" />
      </div>
      {text && (
        <div className="mt-2 max-h-64 overflow-y-auto border-t border-teal-200/70 pt-2 text-[12.5px] leading-relaxed text-slate-700 dark:border-teal-500/20 dark:text-slate-200">
          <Markdown>{text}</Markdown>
          {!done && (
            <span className="ml-0.5 inline-block h-3.5 w-1 animate-pulse bg-teal-500 align-middle dark:bg-teal-400" aria-hidden />
          )}
        </div>
      )}
    </div>
  )
}

// ─── StepDot : pastille du n° d'étape, change selon le statut ───────────────
function StepDot({ index, status }: { index: number; status: 'pending' | 'running' | 'done' }) {
  if (status === 'done') {
    return (
      <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-emerald-600 text-white shadow-xs" aria-label={`étape ${index + 1} terminée`}>
        <IconCheck className="h-3 w-3" />
      </span>
    )
  }
  if (status === 'running') {
    return (
      <span className="relative inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-navy-700 text-white shadow-xs animate-ringPulse" aria-label={`étape ${index + 1} en cours`}>
        <span className="mono text-[10px] font-semibold">{index + 1}</span>
      </span>
    )
  }
  return (
    <span className="mono inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-dashed border-navy-300 dark:border-navy-500/40 bg-white dark:bg-ink-800 text-[10px] font-semibold text-navy-400 dark:text-navy-500" aria-label={`étape ${index + 1}`}>
      {index + 1}
    </span>
  )
}

// ─── PlanRow : ligne d'une étape dans la PlanCard ───────────────────────────
// Le sous-titre est dynamique : instruction (pending) → "N outils" (running) →
// résumé de l'agent (done). Le résumé done passe en emerald — ancre visuelle
// pour parcourir un plan terminé d'un coup d'œil.
function PlanRow({ index, label, instruction, status, agentData, resultData, toolsCount, isReport }: {
  index: number
  label: string
  instruction?: string
  status: 'pending' | 'running' | 'done'
  agentData?: any
  resultData?: any
  toolsCount?: number
  isReport?: boolean
}) {
  const t = useT()
  let subtitle: string | undefined = instruction
  let subtitleCls = 'text-slate-500 dark:text-slate-400'
  if (status === 'running') {
    if (isReport) {
      subtitle = t('chat.plan.writingReport')
      subtitleCls = 'text-navy-600 dark:text-navy-300'
    } else if ((toolsCount ?? 0) === 0) {
      subtitle = t('chat.plan.starting')
      subtitleCls = 'text-navy-600 dark:text-navy-300'
    } else {
      subtitle = t('chat.plan.tools', { count: toolsCount })
      subtitleCls = 'text-navy-600 dark:text-navy-300'
    }
  } else if (status === 'done') {
    if (isReport) {
      subtitle = t('chat.plan.reportDone')
      subtitleCls = 'text-emerald-700 dark:text-emerald-300'
    } else if (resultData) {
      subtitle = resultData.summary || resultData.reply || agentResultCountLabel(resultData, t) || t('chat.plan.done')
      subtitleCls = 'text-emerald-700 dark:text-emerald-300'
    } else if (agentData?.status === 'done') {
      subtitle = t('chat.plan.done')
      subtitleCls = 'text-emerald-700 dark:text-emerald-300'
    }
  }
  return (
    <li className={`flex items-center gap-2.5 px-3 py-1.5 transition ${
      status === 'running' ? 'bg-navy-50/50 dark:bg-navy-500/10' : ''
    }`}>
      <StepDot index={index} status={status} />
      <div className="min-w-0 flex-1">
        <div className="mono truncate text-[10.5px] font-semibold uppercase tracking-wide text-navy-700 dark:text-navy-200">
          {label}
        </div>
        {subtitle && (
          <div className={`truncate text-[10.5px] leading-tight ${subtitleCls}`}>
            {subtitle}
          </div>
        )}
      </div>
      {status === 'running' && <Spinner size={12} color="currentColor" />}
    </li>
  )
}

// Compteur compact pour le résumé done (sources / images / lieux / zones).
function agentResultCountLabel(d: any, t: (key: string, params?: Record<string, unknown>) => string): string | undefined {
  if (Array.isArray(d?.sources)) return t('chat.sources', { count: d.sources.length })
  if (Array.isArray(d?.images)) return t('chat.images', { count: d.images.length })
  if (Array.isArray(d?.locations)) return t('chat.locations', { count: d.locations.length })
  if (Array.isArray(d?.polygons)) return t('chat.zones', { count: d.polygons.length })
  return undefined
}

// ─── PlanCard : table des matières vivante du tour ──────────────────────────
// 1 ligne par étape, statut dérivé du task_progress / -result correspondants.
// L'étape « Écriture du rapport » est ajoutée optimistiquement quand le toggle
// 📝 Rapport est actif (côté front : la vraie étape backend arrive séparément).
function PlanCard({ data, corrIds, taskByCorr, resultByCorr, reportEnabled, reportStatus, onApprove, onModify, focus }: {
  data: any
  corrIds: string[]
  taskByCorr: Map<string, TimelineItem & { kind: 'widget' }>
  resultByCorr: Map<string, TimelineItem & { kind: 'widget' }>
  reportEnabled: boolean
  reportStatus: 'pending' | 'running' | 'done'
  onApprove: (steps: PlanStep[]) => void
  onModify: (steps: PlanStep[]) => void
  focus: 'approve' | 'modify' | null
}) {
  const t = useT()
  const steps: PlanStep[] = data.steps || []
  const decided = !!data.decided
  // idx (étape du plan) → corr_id, dans l'ordre de dispatch DE CE TOUR (déjà filtré
  // par plan en amont) — fait le pont entre chaque étape du plan et sa tâche.
  const corrByIdx: string[] = corrIds

  // Statut + données par étape.
  const perStep = steps.map((s, i) => {
    const cid = corrByIdx[i]
    const task = cid ? taskByCorr.get(cid)?.data : null
    const result = cid ? resultByCorr.get(cid)?.data : null
    let status: 'pending' | 'running' | 'done' = 'pending'
    if (task?.status === 'done') status = 'done'
    else if (task && task.status !== 'cancelled' && task.status !== 'failed' && task.status !== 'timeout') {
      status = cid ? 'running' : 'pending'
    }
    if (!decided) status = 'pending'
    const toolsCount = (() => {
      if (!cid) return 0
      // Compte unique de tools appelés via les traces (kind === 'tool_call').
      // Approximation : nombre brut de tool_call (sans dédup) — suffit pour signaler "ça avance".
      return 0  // sera surchargé par la version *with traces* de l'AgentCard ; ici on n'a pas accès
    })()
    return { step: s, status, task, result, toolsCount, cid }
  })

  const totalSteps = steps.length + (reportEnabled ? 1 : 0)
  const doneCount = perStep.filter((p) => p.status === 'done').length + (reportEnabled && reportStatus === 'done' ? 1 : 0)
  const allDone = totalSteps > 0 && doneCount === totalSteps

  return (
    <div className="animate-popIn overflow-hidden rounded-xl border border-navy-200 dark:border-ink-600 bg-white dark:bg-ink-800 shadow-xs">
      <div className="flex items-center gap-2 border-b border-navy-100 dark:border-ink-700 bg-navy-50/60 dark:bg-navy-500/10 px-3 py-1.5">
        <IconBolt className="h-3.5 w-3.5 text-navy-600 dark:text-navy-300" />
        <span className="mono text-[10px] font-semibold uppercase tracking-wide text-navy-700 dark:text-navy-200">{t('chat.plan.title')}</span>
        <span className="text-[10px] text-navy-300 dark:text-navy-500">·</span>
        <span className="mono text-[10px] text-navy-500 dark:text-navy-400 whitespace-nowrap">
          {t('chat.plan.steps', { count: totalSteps })}
        </span>
        <span className="ml-auto mono text-[10px] text-navy-400 dark:text-navy-500 whitespace-nowrap">
          {!decided ? t('chat.plan.waiting') : allDone ? t('chat.plan.completed') : `${doneCount}/${totalSteps}`}
        </span>
      </div>

      <ol className="divide-y divide-navy-100 dark:divide-ink-700 pt-1">
        {perStep.map((p, i) => (
          <PlanRow
            key={p.step.plan_step_id ?? i}
            index={i}
            label={agentLabel(p.step.agent_type)}
            instruction={p.step.instruction}
            status={p.status}
            agentData={p.task}
            resultData={p.result}
            toolsCount={p.toolsCount}
          />
        ))}
        {reportEnabled && (
          <PlanRow
            key="__report__"
            index={steps.length}
            label={t('chat.plan.reportLabel')}
            instruction={t('chat.plan.reportInstruction')}
            status={reportStatus}
            isReport
          />
        )}
      </ol>

      {/* Auto-approbation active (AUTO_APPROVE_PLAN) : le plan reste AFFICHÉ
          (parcours de délégation visible par le jury) mais les boutons
          Approuver/Rejeter sont masqués — le gate humain se réactive en
          remettant le flag à false dans lib/hooks.ts. */}
      {!decided && !AUTO_APPROVE_PLAN && (
        <div className="flex gap-2 border-t border-navy-100 dark:border-ink-700 bg-navy-50/40 dark:bg-navy-500/5 p-2">
          <button
            onClick={() => onApprove(steps)}
            className={`flex-1 rounded-lg bg-teal-500 px-3 py-1.5 text-[12px] font-semibold text-white transition hover:bg-teal-600 ${
              focus === 'approve' ? 'animate-softPulse ring-2 ring-teal-300 ring-offset-1 dark:ring-offset-ink-800' : ''
            }`}
          >
            ✓ {t('chat.approve')}
          </button>
          <button
            onClick={() => onModify(steps)}
            className={`flex-1 rounded-lg border border-navy-200 dark:border-ink-600 bg-white dark:bg-ink-800 px-3 py-1.5 text-[12px] font-semibold text-navy-700 dark:text-navy-200 transition hover:bg-navy-50 dark:hover:bg-ink-700 ${
              focus === 'modify' ? 'ring-2 ring-navy-400 ring-offset-1 dark:ring-offset-ink-800' : ''
            }`}
          >
            ✎ {t('chat.modify')}
          </button>
        </div>
      )}
    </div>
  )
}

// ─── ToolsList / ToolRow : appels d'outils proprement listés ───────────────
// Liste mono, 1 ligne par tool. Le dernier appel (en mode live) reçoit un fond
// doux et un indicateur pulsant — l'analyste voit en un coup d'œil ce qui
// tourne « maintenant ».
function ToolsList({ traces, live, liveText }: { traces: Progress[]; live: boolean; liveText?: string }) {
  const t = useT()
  // Construit la timeline d'événements affichables : tool_call (action structurée
  // de l'agent) + thinking discret (chaque nouvelle étape annoncée par l'agent).
  // Chaque entrée = UNE ligne dans la card → l'analyste voit l'agent progresser
  // au lieu de rester sur « initializing… » jusqu'à la réponse finale.
  const entries = useMemo(() => {
    type Entry =
      | { kind: 'tool'; tool: string; detail: string; via?: string; technical?: string }
      | { kind: 'step'; text: string }
    const seenTool = new Set<string>()
    const seenStep = new Set<string>()
    const out: Entry[] = []
    for (const tr of traces) {
      const d = tr.trace_data || {}
      if (tr.kind === 'tool_call') {
        const rawTool = String(d.tool || '')
        if (!rawTool) continue
        const detail = String(d.endpoint || d.detail || '')
        const key = `tool|${rawTool}|${detail}`
        if (seenTool.has(key)) continue
        seenTool.add(key)
        // Libellé humanisé (« Recherche du scrutin… ») + chip « via {mcp} » ;
        // le couple technique tool+endpoint reste dans le title= (transparence).
        const h = traceCallLabel(d)
        out.push({ kind: 'tool', tool: h.label, detail: '', via: h.via, technical: h.technical })
      } else if (tr.kind === 'thinking' && !d.stream_id) {
        // Étape discrète (sans stream_id) émise par le bridge A2A à chaque
        // nouveau status_text / artifact. Les chunks accumulés (avec stream_id)
        // restent dans `liveText`, ils ne polluent pas la liste.
        const text = String(d.text || '').trim()
        if (!text) continue
        const key = `step|${text.slice(0, 120)}`
        if (seenStep.has(key)) continue
        seenStep.add(key)
        out.push({ kind: 'step', text })
      }
    }
    return out
  }, [traces])

  const hasContent = entries.length > 0
  // Texte du résumé en cours de rédaction (streamé token-par-token). Affiché tant que
  // l'agent tourne ; remplacé par le résumé canonique (encadré vert) une fois `done`.
  const draft = (live && liveText) ? liveText.trim() : ''

  if (!hasContent && !draft && live) {
    return (
      <div className="px-3 py-2.5">
        <p className="mono inline-flex items-center gap-1.5 text-[10px] text-navy-400 dark:text-navy-500">
          <ThinkingDots className="text-navy-400 dark:text-navy-500" />
          {t('chat.plan.initializing')}
        </p>
      </div>
    )
  }
  if (!hasContent && !draft) return null

  return (
    <div className="px-3 py-2">
      {hasContent && (
        <>
          <div className="mono mb-1 flex items-center gap-1.5">
            <span className="text-[9px] font-bold uppercase tracking-wider text-navy-400 dark:text-navy-500">{t('chat.agentSteps')}</span>
          </div>
          <ul className="space-y-px">
            {entries.map((e, i) => (
              e.kind === 'tool' ? (
                <ToolRow key={`tool-${i}`} tool={e.tool} detail={e.detail} via={e.via} technical={e.technical} live={live && i === entries.length - 1} />
              ) : (
                <StepRow key={`step-${i}`} text={e.text} live={live && i === entries.length - 1} />
              )
            ))}
          </ul>
        </>
      )}
      {draft && <DraftingBlock text={draft} />}
    </div>
  )
}

// Bloc « rédaction en cours » : le résumé de l'agent qui s'écrit token-par-token.
// Rendu Markdown INCRÉMENTAL (gras/italique/listes appliqués au fil de l'eau, pas
// seulement quand le résumé canonique prend le relais). Le curseur live est rendu
// à la fin du flux ; on signale aussi l'activité par le Spinner de l'en-tête.
function DraftingBlock({ text }: { text: string }) {
  const t = useT()
  return (
    <div className={text ? 'mt-2' : ''}>
      <div className="mono mb-1 flex items-center gap-1.5">
        <span className="text-[9px] font-bold uppercase tracking-wider text-navy-400 dark:text-navy-500">{t('chat.output')}</span>
        <Spinner size={10} color="currentColor" />
      </div>
      <div className="prose prose-sm dark:prose-invert max-w-none break-words text-[11px] leading-relaxed text-slate-600 dark:text-slate-300 [&_*]:my-0.5">
        <Markdown>{text}</Markdown>
        <span className="ml-0.5 inline-block h-3 w-1 animate-pulse bg-navy-400 align-middle dark:bg-navy-500" aria-hidden />
      </div>
    </div>
  )
}

// Ligne « étape » : un message court annoncé par l'agent au fil de son travail
// (status_update A2A). Pas un tool_call structuré → rendu plus humain (italic).
function StepRow({ text, live }: { text: string; live: boolean }) {
  return (
    <li className={`flex items-center gap-2 rounded-md px-1.5 py-1 text-[11px] animate-fadeInUp ${
      live ? 'bg-navy-50 dark:bg-navy-500/15' : ''
    }`}>
      <span className={`shrink-0 ${live ? 'text-navy-500 dark:text-navy-300' : 'text-navy-400 dark:text-navy-500'}`}>›</span>
      <span className="whitespace-pre-wrap break-words text-slate-600 dark:text-slate-300">{text}</span>
      {live && (
        <span className="ml-auto shrink-0 text-navy-500 dark:text-navy-400">
          <Spinner size={11} color="currentColor" />
        </span>
      )}
    </li>
  )
}

function ToolRow({ tool, detail, via, technical, live }: { tool: string; detail: string; via?: string; technical?: string; live: boolean }) {
  return (
    <li className={`flex items-center gap-2 rounded-md px-1.5 py-1 text-[11px] animate-fadeInUp ${
      live ? 'bg-navy-50 dark:bg-navy-500/15' : ''
    }`} title={technical}>
      <span className={`shrink-0 font-medium ${live ? 'text-navy-700 dark:text-navy-200' : 'text-navy-600 dark:text-navy-300'}`}>
        {tool}
      </span>
      {detail && (
        <span className="truncate text-navy-400 dark:text-navy-500">{detail}</span>
      )}
      {/* Chip « via {mcp_server} » bien visible : d'où vient la donnée. */}
      {via && (
        <span className="mono shrink-0 rounded-full border border-teal-200 bg-teal-50 px-1.5 py-px text-[9.5px] font-semibold text-teal-700 dark:border-teal-500/30 dark:bg-teal-500/15 dark:text-teal-300">
          via {via}
        </span>
      )}
      {live && (
        <span className="ml-auto shrink-0 text-navy-500 dark:text-navy-400">
          <Spinner size={11} color="currentColor" />
        </span>
      )}
    </li>
  )
}

// ─── AgentCard : carte étendue (running) / pill (done) ──────────────────────
// L'analyste voit le n° d'étape (ancré sur la PlanCard), le libellé de l'agent,
// les tools utilisés, et un résumé en bas dès que c'est terminé. Cliquer une
// pill done rouvre la carte (relecture).
function AgentCard({ stepIdx, task, result, traces, progress, liveText }: {
  stepIdx: number
  task: any
  result?: any
  traces: Progress[]
  progress?: Progress
  liveText?: string
}) {
  const t = useT()
  const status = task?.status as string | undefined
  const TERMINAL = ['done', 'failed', 'timeout', 'cancelled']
  // `awaiting_input` = l'agent a fini d'écrire et ATTEND une saisie de l'analyste
  // (placement carte, réponse enum…). Ce n'est PAS « en cours » : sans ce cas, la
  // card restait en mode running → Spinner + curseur de rédaction perpétuels
  // (« l'agent n'a jamais fini d'écrire »).
  const isWaiting = status === 'awaiting_input'
  const isRunning = !!status && !TERMINAL.includes(status) && !isWaiting
  const isDone = status === 'done'
  const isFailed = !!status && TERMINAL.includes(status) && !isDone
  const chipState: 'running' | 'done' | 'failed' | 'waiting' =
    isRunning ? 'running' : isWaiting ? 'waiting' : isDone ? 'done' : 'failed'
  const [open, setOpen] = useState(false)
  const agentType = task?.agent_type ? agentLabel(task.agent_type) : t('chat.agentFallback')
  const summary = result?.summary || result?.reply || agentResultCountLabel(result, t) || progress?.note

  // Mode pill (done + replié) — 1 ligne : Step X + nom de l'agent + puce d'état
  if (isDone && !open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="animate-fadeInUp flex w-full items-center gap-2 rounded-xl border border-navy-100 dark:border-ink-600 bg-white dark:bg-ink-800 px-3 py-1.5 text-left shadow-xs transition hover:border-navy-200 dark:hover:border-ink-500 hover:bg-navy-50/40 dark:hover:bg-navy-500/10"
        title={t('chat.expandTitle')}
      >
        <span className="mono text-[9px] font-semibold uppercase tracking-wider text-navy-400 dark:text-navy-500 shrink-0">
          {t('chat.plan.stepShort', { n: stepIdx + 1 })}
        </span>
        <span className="mono text-[10.5px] font-semibold uppercase text-navy-700 dark:text-navy-200 shrink-0 truncate">{agentType}</span>
        <span className="ml-auto flex items-center gap-2 shrink-0">
          <StatusChip state={chipState} />
          <span className="text-[10px] text-navy-300 dark:text-navy-500">▸</span>
        </span>
      </button>
    )
  }

  return (
    <div className={`animate-fadeInUp overflow-hidden rounded-xl border bg-white dark:bg-ink-800 shadow-xs ${
      isRunning ? 'border-navy-300 dark:border-navy-500/40' : 'border-navy-100 dark:border-ink-600'
    }`}>
      <div className={`flex items-center gap-2 px-3 py-2 ${
        isRunning ? 'bg-navy-50/60 dark:bg-navy-500/10' : 'border-b border-navy-100 dark:border-ink-700'
      }`}>
        <span className="mono text-[9px] font-semibold uppercase tracking-wider text-navy-400 dark:text-navy-500 shrink-0">
          {t('chat.plan.stepShort', { n: stepIdx + 1 })}
        </span>
        <span className="mono text-[10.5px] font-semibold uppercase tracking-wide text-navy-700 dark:text-navy-200 truncate">
          {agentType}
        </span>
        <span className="ml-auto flex items-center gap-2 shrink-0">
          <StatusChip state={chipState} />
          {isDone && (
            <button
              onClick={() => setOpen(false)}
              className="shrink-0 whitespace-nowrap text-[11px] text-navy-400 dark:text-navy-500 hover:text-navy-700 dark:hover:text-navy-200"
              title={t('chat.collapseTitle')}
              aria-label={t('chat.collapseTitle')}
            >
              ▴
            </button>
          )}
          {isFailed && status && (
            <span className="mono whitespace-nowrap text-[10px] text-red-600 dark:text-red-400">{status}</span>
          )}
        </span>
      </div>

      <ToolsList traces={traces} live={!!isRunning} liveText={liveText} />

      {!isRunning && summary && (
        <div className="border-t border-emerald-200 dark:border-emerald-500/30 bg-emerald-50/40 dark:bg-emerald-500/10 px-3 py-1.5">
          <div className="mono mb-1 text-[9px] font-bold uppercase tracking-wider text-emerald-700 dark:text-emerald-400">
            {t('chat.output')}
          </div>
          {/* Carte étendue → on rend le markdown complet du résumé (titres, listes,
              gras…). Le mode pill (replié) garde la version 1-ligne tronquée. */}
          <div className="prose prose-sm dark:prose-invert max-w-none text-[11px] leading-relaxed text-emerald-700 dark:text-emerald-300 break-words [&_*]:my-0.5">
            <Markdown>{String(summary)}</Markdown>
          </div>
        </div>
      )}
    </div>
  )
}

// --- Carte « rapport prêt » : message final + bouton de téléchargement PDF ---
// Émise par l'orchestrateur quand `force_report=True` à la fin du tour. Le PDF
// est généré côté client à partir du markdown du widget rapport (event-sourcé),
// via le helper pdfmake partagé (exportReportPdf) → vraie couche texte extractible.
function ReportReadyCard({
  text,
  reportWidgetId,
  filename,
  events,
}: {
  text: string
  reportWidgetId?: string
  filename?: string
  events: UIEvent[]
}) {
  const [busy, setBusy] = useState(false)
  // Retrouve le markdown du rapport canonique dans la timeline event-sourcée.
  // En cas d'absence (race au tout premier rendu), le bouton restera désactivé.
  const reportMarkdown = useMemo(() => {
    if (!reportWidgetId) return ''
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i]
      if (e.type === 'widget' && e.payload?.widget_id === reportWidgetId) {
        return String(e.payload?.data?.text || '')
      }
    }
    return ''
  }, [events, reportWidgetId])

  const downloadPdf = async () => {
    if (!reportMarkdown.trim() || busy) return
    setBusy(true)
    try {
      // Markdown → HTML → PDF via le helper partagé (pdfmake) : même pipeline que
      // le widget éditeur, donc vraie couche texte extractible par les agents NLP.
      // html2pdf.js rasterisait tout en JPEG → rapports illisibles par la CSD.
      const [{ marked }, { exportReportPdf }] = await Promise.all([
        import('marked'),
        import('../lib/exportReportPdf'),
      ])
      marked.setOptions({ gfm: true, breaks: true })
      const html = String(marked.parse(reportMarkdown))
      await exportReportPdf(html, filename || 'rapport-hemicycle.pdf')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex items-start gap-3 rounded-xl border border-teal-200 dark:border-teal-500/30 bg-teal-50/60 dark:bg-teal-500/10 px-3 py-2.5 shadow-xs">
      <span className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-teal-500/15 text-teal-700 dark:text-teal-300">
        📄
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[12.5px] leading-relaxed text-slate-800 dark:text-slate-100">{text}</p>
        <button
          onClick={downloadPdf}
          disabled={!reportMarkdown.trim() || busy}
          className="mt-1.5 inline-flex items-center gap-1.5 rounded-lg bg-teal-500 px-3 py-1.5 text-[12px] font-semibold text-white shadow-sm transition hover:bg-teal-600 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor"
               strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="7 10 12 15 17 10" />
            <line x1="12" y1="15" x2="12" y2="3" />
          </svg>
          {busy ? 'Génération…' : 'Télécharger le PDF'}
        </button>
      </div>
    </div>
  )
}


// --- Carte de compaction : résumé du contexte, repliable + rendu Markdown ---
// Repliée par défaut (le résumé peut être long) ; clic sur le bandeau → déplie.
function CompactionCard({ text }: { text: string }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  return (
    <div className="overflow-hidden rounded-xl border border-navy-200 dark:border-navy-500/30 bg-navy-50/50 dark:bg-navy-500/10 shadow-xs">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition hover:bg-navy-50 dark:hover:bg-navy-500/15"
        aria-expanded={open}
        title={open ? t('chat.collapseTitle') : t('chat.expandTitle')}
      >
        <RecycleIcon className="h-3.5 w-3.5 text-navy-600 dark:text-navy-300" />
        <span className="text-[11px] font-semibold uppercase tracking-wide text-navy-700 dark:text-navy-200">
          {t('chat.compactionCardTitle')}
        </span>
        <span className={`ml-auto shrink-0 text-navy-400 dark:text-navy-300 transition-transform ${open ? 'rotate-90' : ''}`}>▸</span>
      </button>
      {open && (
        <div className="border-t border-navy-100 dark:border-navy-500/20 px-3 py-2">
          <Markdown>{text}</Markdown>
        </div>
      )}
    </div>
  )
}

// --- Progression de compaction : barre indéterminée pendant le résumé LLM ---
export function CompactionProgress() {
  const t = useT()
  return (
    <div className="animate-fadeInUp rounded-xl border border-navy-200 dark:border-navy-500/30 bg-navy-50/60 dark:bg-navy-500/15 p-3">
      <div className="mb-2 flex items-center gap-2">
        <RecycleIcon className="h-3.5 w-3.5 animate-spin text-navy-600 dark:text-navy-300" />
        <span className="text-[11px] font-semibold uppercase tracking-wide text-navy-700 dark:text-navy-200">
          {t('chat.compacting')}
        </span>
      </div>
      <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-navy-100 dark:bg-navy-500/20">
        <div className="absolute inset-y-0 animate-indeterminate rounded-full bg-navy-500 dark:bg-navy-400" />
      </div>
    </div>
  )
}

