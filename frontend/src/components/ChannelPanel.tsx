// Panneau d'un channel (salon d'état-major) — occupe le même conteneur flottant
// que ChatPanel. Deux fils FUSIONNÉS chronologiquement :
//   1. la discussion humaine multi-auteurs (useChannel) ;
//   2. l'investigation d'Hémicycle quand on l'appelle via « @hemicycle … » dans ce
//      salon — l'orchestrateur déroule le flux COMPLET (plan → accepter/modifier →
//      run) dans une conversation liée `chan-<id>` (useConversation), rendue par
//      InvestigationFeed. Hémicycle « fait partie de la conversation » de la cellule.
//
// Chaque message porte au survol des actions : répondre, transférer (vers un
// autre salon, avec note optionnelle), supprimer (sans confirmation). L'en-tête
// expose le prompt système éditable de la cellule (oriente la planification).

import { useEffect, useMemo, useRef, useState } from 'react'
import { Markdown } from './Markdown'
import { InvestigationFeed } from './InvestigationFeed'
import { WorkflowBuilder } from './WorkflowBuilder'
import { ChannelSettings } from './ChannelSettings'
import { api } from '../lib/api'
import { useChannel, useConversation, usePolling } from '../lib/hooks'
import { useT } from '../lib/i18n'
import { useToast } from '../lib/toast'
import { CURRENT_USER, authorRoleFor } from '../lib/store'
import { timeAgo } from '../lib/ui'
import { useGeoOverlayStore } from '../lib/geoOverlayStore'
import type { Channel, ChannelMessage, PlanStep, UIEvent, GeoLoc, GeoPoly } from '../lib/types'

// Mode de composition d'un salon : « message » poste sans solliciter Hémicycle ;
// « hemicycle » poste ET déclenche l'orchestrateur. Bascule au clavier (Shift+Tab),
// quel que soit le focus. Remplace l'ancien déclencheur « @hemicycle ».
type ComposeMode = 'message' | 'hemicycle'

type Props = {
  channelId: string
  // Ouverture d'un widget dashboard demandée par l'orchestrateur (event `spawn_widget`
  // de la conv DU SALON). Délégué à DashboardView (qui possède addWidget + le canvas) :
  // sans ce pont, l'appui-feu et tout widget server-driven ne s'ouvraient qu'en chat privé.
  onSpawnWidget?: (widgetKey: string) => void
}

export function ChannelPanel({ channelId, onSpawnWidget }: Props) {
  const t = useT()
  const toast = useToast()
  const { messages, post, remove } = useChannel(channelId)
  const [draft, setDraft] = useState('')
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const fileRef = useRef<HTMLInputElement | null>(null)

  // Overlay géo sur la carte du dashboard — action stable du store zustand.
  const showOnMap = useGeoOverlayStore((s) => s.showOnMap)
  // Ids des alertes géo déjà présentées sur la carte (évite les doublons).
  const seenGeoAlertIds = useRef<Set<number>>(new Set())
  // Vrai une fois que le seed initial (montage) a été réalisé.
  const geoAlertsMountSeeded = useRef(false)

  // Métadonnées du channel (nom, readonly, prompt système) — depuis la liste polled.
  const channels = usePolling(api.channels, 10000) || []
  const channel = useMemo(() => channels.find((c) => c.id === channelId), [channels, channelId])
  const readonly = channel?.readonly ?? false
  const isHémicycle = channel?.kind === 'system' || channelId === 'hemicycle'

  // Conversation orchestrateur liée au salon (sauf le salon système « hemicycle »,
  // en lecture seule). conv_id = "chan-<id>" → continuité des tours + rejeu pour
  // tout analyste qui ouvre le salon (DeliverPolicy.ALL).
  const convId = isHémicycle ? null : `chan-${channelId}`
  const inv = useConversation(convId)

  // Actions de message : réponse en cours / transfert en cours.
  const [replyingTo, setReplyingTo] = useState<ChannelMessage | null>(null)
  const [transferFor, setTransferFor] = useState<ChannelMessage | null>(null)
  // Modale « Modifier le plan » (réutilise le builder du dashboard, branché sur la
  // conversation DU SALON → l'approbation part bien vers chan-<id>).
  const [builderSteps, setBuilderSteps] = useState<PlanStep[] | null>(null)
  // Pop-up réglages (prompt système + mémoire de cellule).
  const [settingsOpen, setSettingsOpen] = useState(false)
  // Mode de composition. Défaut « message » : on discute avec l'équipe ; on bascule
  // explicitement en « hemicycle » (Shift+Tab) pour solliciter l'orchestrateur.
  const [mode, setMode] = useState<ComposeMode>('message')
  // Raisonnement live (thinking) lors d'une sollicitation Hémicycle — comme dans le chat.
  // Activé par défaut ; bascule via l'icône 🧠 (visible seulement en mod'Hémicycle).
  const [thinkOn, setThinkOn] = useState(true)
  // Pièces jointes en attente d'envoi (uploadées, jointes au prochain tour Hémicycle).
  const [attachments, setAttachments] = useState<{ id: string; filename: string }[]>([])
  // Double-Esc d'interruption (comme le chat privé) : 1er appui arme (+ hint 1.5 s),
  // 2e dans la fenêtre → inv.cancel() (tombstone des tâches du plan en cours).
  const [escArmed, setEscArmed] = useState(false)
  const escTimerRef = useRef<number | null>(null)

  // Upload des fichiers joints → ajoutés à la liste, envoyés avec le prochain tour Hémicycle.
  const onAttachFiles = async (files: FileList) => {
    for (const file of Array.from(files)) {
      try {
        const a = await api.uploadAttachment(file, convId ?? undefined)
        setAttachments((prev) => [...prev, { id: a.id, filename: a.filename }])
      } catch (e: any) {
        toast.error(file.name, e?.message || String(e))
      }
    }
  }

  // Bascule Message/Hémicycle via Shift+Tab — quel que soit le focus (curseur dans
  // l'input ou non). Inactif sur un salon en lecture seule (« hemicycle ») ou quand
  // la modale réglages est ouverte. On capture Tab pour empêcher le déplacement de
  // focus par défaut du navigateur.
  useEffect(() => {
    if (readonly || settingsOpen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Tab' && e.shiftKey) {
        e.preventDefault()
        setMode((m) => (m === 'hemicycle' ? 'message' : 'hemicycle'))
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [readonly, settingsOpen])

  // Logique double-Esc partagée : 1er appui arme (+ hint 1.5 s), 2e interrompt.
  const triggerEscStop = () => {
    if (escArmed) {
      if (escTimerRef.current) { clearTimeout(escTimerRef.current); escTimerRef.current = null }
      setEscArmed(false)
      inv.cancel()
      return
    }
    setEscArmed(true)
    if (escTimerRef.current) clearTimeout(escTimerRef.current)
    escTimerRef.current = window.setTimeout(() => {
      setEscArmed(false)
      escTimerRef.current = null
    }, 1500)
  }

  // Listener global du double-Esc : Échap capté au niveau `window` pour stopper le
  // plan Hémicycle du salon même si le focus n'est PAS dans l'input. Neutralisé quand
  // une modale gère déjà Échap (réglages, builder, transfert) ou sur un salon en
  // lecture seule. Un Échap qui ferme d'abord le bandeau « en réponse à … » ne doit
  // pas armer l'interruption : on consomme ce cas en priorité.
  useEffect(() => {
    if (readonly || settingsOpen || builderSteps || transferFor) return
    const onWindowEsc = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (replyingTo) { setReplyingTo(null); return }
      e.preventDefault()
      triggerEscStop()
    }
    window.addEventListener('keydown', onWindowEsc)
    return () => window.removeEventListener('keydown', onWindowEsc)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readonly, settingsOpen, builderSteps, transferFor, replyingTo, escArmed])

  // Cleanup du timer Esc au démontage — évite un setEscArmed après unmount.
  useEffect(() => () => {
    if (escTimerRef.current) clearTimeout(escTimerRef.current)
  }, [])

  // Ouverture de widgets pilotée par l'orchestrateur DEPUIS LE SALON : la conv
  // `chan-<id>` émet des events `spawn_widget` (ex. tableau d'appui feu) que SEUL
  // DashboardView traitait, et UNIQUEMENT pour la conv privée active → en salon le
  // widget ne s'ouvrait jamais (l'analyste devait le spawn à la main). On rejoue ici
  // la même garde par seq et on délègue l'ajout au canvas via `onSpawnWidget`.
  // Ref sur le callback : l'effet ne dépend que de `inv.events` (comme côté privé),
  // un ref évite de rater une demande à cause d'une closure périmée.
  const onSpawnWidgetRef = useRef(onSpawnWidget)
  onSpawnWidgetRef.current = onSpawnWidget
  const spawnWidgetSeqRef = useRef(0)
  useEffect(() => { spawnWidgetSeqRef.current = 0 }, [channelId])
  useEffect(() => {
    const reqs = inv.events.filter(
      (e) => e.type === 'spawn_widget' && e.seq > spawnWidgetSeqRef.current,
    )
    if (reqs.length === 0) return
    for (const e of reqs) {
      spawnWidgetSeqRef.current = Math.max(spawnWidgetSeqRef.current, e.seq)
      const key = String(e.payload?.widget || '')
      if (key) onSpawnWidgetRef.current?.(key)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inv.events])

  // Fil fusionné : messages de channel + tours d'investigation, triés par horodatage.
  // On découpe les events de la conv en TOURS aux frontières (message role=user) :
  // chaque tour est rendu par un InvestigationFeed indépendant, ancré au ts de son
  // 1er event. Tie-break : la bulle de channel précède le tour qu'elle a déclenché.
  const turns = useMemo(() => {
    const evs = [...inv.events].sort((a, b) => a.seq - b.seq)
    const out: { ts: string; startSeq: number; events: UIEvent[] }[] = []
    // Tour de CRÉATION de chaque widget. Un `widget_update` (ex. task_progress →
    // « done ») arrive souvent APRÈS un nouveau message user (soumission du
    // formulaire d'appui feu, etc.) et ouvrirait donc un AUTRE tour. Or chaque tour
    // est rendu par un InvestigationFeed isolé qui n'applique le patch que s'il voit
    // aussi l'event `widget` correspondant (buildTimeline → byId[widget_id]). On
    // rattache donc le patch au tour de son widget ; sinon il est perdu et la tâche
    // reste affichée « au démarrage » à vie.
    const turnByWidget = new Map<string, number>()
    for (const e of evs) {
      if (e.type === 'widget_update') {
        const ti = turnByWidget.get(e.payload?.widget_id)
        if (ti !== undefined) { out[ti].events.push(e); continue }
      }
      const isUserMsg = e.type === 'message' && e.payload?.role === 'user'
      if (isUserMsg || out.length === 0) {
        out.push({ ts: e.ts || '', startSeq: e.seq, events: [e] })
      } else {
        out[out.length - 1].events.push(e)
      }
      if (e.type === 'widget' && e.payload?.widget_id) {
        turnByWidget.set(e.payload.widget_id, out.length - 1)
      }
    }
    return out
  }, [inv.events])

  type FeedRow = { ts: string; order: number; key: string; render: () => React.ReactNode }
  const feed: FeedRow[] = useMemo(() => {
    const rows: FeedRow[] = []
    for (const m of messages) {
      rows.push({
        ts: m.created_at, order: 0, key: `chan-${m.id}`,
        render: () => (
          <ChannelBubble
            msg={m}
            onReply={() => { setTransferFor(null); setReplyingTo(m) }}
            onTransfer={() => { setReplyingTo(null); setTransferFor(m) }}
            onDelete={() => remove(m.id)}
            canPost={!readonly}
          />
        ),
      })
    }
    for (const turn of turns) {
      rows.push({
        ts: turn.ts, order: 1, key: `turn-${turn.startSeq}`,
        render: () => (
          <InvestigationFeed
            events={turn.events}
            progress={inv.progress}
            traces={inv.traces}
            thinking={inv.thinking}
            assistantStream={inv.assistantStream}
            reportEnabled={false}
            planFocus="approve"
            pendingPlanSeq={null}
            onApprovePlan={inv.sendPlan}
            onModifyPlan={(steps) => setBuilderSteps(steps)}
            onMemoryDecision={inv.sendMemoryDecision}
            suppressUserMessages
            onTransferToChannel={(m) => {
              setReplyingTo(null)
              // Réponse Hémicycle (tour d'investigation) → on la transfère via le MÊME
              // popover/back-end que les bulles de salon, en fabriquant un ChannelMessage
              // synthétique (le popover ne lit que kind/channel_id/author_role/text/payload).
              setTransferFor({
                id: -1,
                channel_id: channel?.id || '',
                author_id: 'hemicycle',
                author_role: m.role === 'user' ? authorRoleFor(channel?.id) : 'Hémicycle',
                kind: 'message',
                text: m.text,
                payload: {},
                created_at: '',
              })
            }}
          />
        ),
      })
    }
    // ISO comparables ; à ts égal, la bulle (order 0) précède le tour (order 1).
    rows.sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : a.order - b.order))
    return rows
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, turns, inv.progress, inv.traces, inv.thinking, inv.assistantStream, readonly, channel])

  // Auto-scroll « suiveur » : on ne ramène en bas QUE si l'analyste y est déjà
  // (comme le chat privé). Avant, on forçait `scrollTop = scrollHeight` à chaque
  // update → impossible de relire l'historique pendant que Hémicycle streame (retour
  // forcé en bas + scintillement). Le scroll est instantané (pas smooth) pour ne pas
  // redémarrer une animation à chaque token.
  const [followBottom, setFollowBottom] = useState(true)
  const FOLLOW_THRESHOLD_PX = 80
  const onScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    setFollowBottom(distFromBottom <= FOLLOW_THRESHOLD_PX)
  }
  useEffect(() => {
    if (!followBottom) return
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [followBottom, feed.length, inv.events.length, inv.progress, inv.traces, inv.thinking, inv.assistantStream])

  // Auto-affichage de la carte à l'arrivée d'une alerte géolocalisée dans ce salon.
  // Au premier appel (montage du composant), on "graine" les ids existants SANS déclencher
  // la carte : on évite ainsi de flooder le dashboard à l'ouverture d'un salon qui porte
  // déjà un historique d'alertes. Seules les vraies nouvelles alertes arrivées APRÈS le
  // montage déclenchent réellement showOnMap.
  useEffect(() => {
    const geoAlerts = messages.filter(
      (m) => m.kind === 'alert' && (m.payload?.geo?.locations?.length ?? 0) > 0,
    )
    if (!geoAlertsMountSeeded.current) {
      // Premier appel : seed sans affichage.
      geoAlertsMountSeeded.current = true
      for (const m of geoAlerts) seenGeoAlertIds.current.add(m.id)
      return
    }
    const newAlerts = geoAlerts.filter((m) => !seenGeoAlertIds.current.has(m.id))
    if (!newAlerts.length) return
    // On affiche la plus récente (dernière dans l'ordre chronologique du filtre).
    // payload.geo est garanti non-null ici (filtré par .geo?.locations?.length > 0).
    showOnMap(newAlerts[newAlerts.length - 1].payload.geo!)
    for (const m of newAlerts) seenGeoAlertIds.current.add(m.id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages]) // showOnMap est une action zustand stable, omise intentionnellement des deps

  const submit = () => {
    const text = draft.trim()
    if (!text || readonly) return
    // La cellule voit toujours le message (une réponse porte sa référence au parent).
    // `to_hemicycle` : marque le message comme adressé à l'orchestrateur (mod'Hémicycle) →
    // la bulle reçoit la bordure ambre, comme dans le chat orchestrateur privé.
    const msgPayload: any = {}
    if (replyingTo) msgPayload.reply_to = { id: replyingTo.id, author_role: replyingTo.author_role, text: replyingTo.text }
    if (mode === 'hemicycle') msgPayload.to_hemicycle = true
    post(text, Object.keys(msgPayload).length ? msgPayload : undefined)
    // Mod'Hémicycle : le texte part vers la conv du salon → l'orchestrateur planifie
    // (plan → accepter/modifier → run) dans le fil. Mode Message : rien d'autre.
    if (mode === 'hemicycle' && convId) {
      inv.send(text, { thinking: thinkOn, attachments: attachments.length ? attachments : undefined })
      setAttachments([])
    }
    setDraft('')
    setReplyingTo(null)
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    // Shift+Tab est géré par le listener global (bascule de mode) — on laisse passer.
    if (e.key === 'Tab' && e.shiftKey) return
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() }
    if (e.key === 'Escape' && replyingTo) { setReplyingTo(null) }
  }

  const transferTargets = useMemo(
    () => channels.filter((c) => !c.readonly && c.id !== channelId),
    [channels, channelId],
  )

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-slate-200 dark:border-ink-600 bg-cream dark:bg-ink-800 shadow-card">
      {/* En-tête : nom du salon + réglages (⚙). La navigation entre salons se
          fait via la sidebar persistante (onglet « Channels »). */}
      <div className="flex items-center gap-2 border-b border-slate-200 dark:border-ink-600 bg-white/60 dark:bg-ink-700/50 px-3 py-2">
        <span className="flex min-w-0 flex-1 items-center justify-center gap-1.5">
          <span className="text-sm" aria-hidden>{isHémicycle ? '📢' : '#'}</span>
          <span className="truncate text-[11px] font-semibold text-slate-700 dark:text-slate-200">
            {channel?.name || channelId}
          </span>
          {/* Icône réglages — à droite du nom (sauf salon système hemicycle). */}
          {!isHémicycle && channel && (
            <button
              onClick={() => setSettingsOpen(true)}
              className="shrink-0 text-slate-400 transition hover:text-navy-700 dark:text-slate-500 dark:hover:text-navy-200"
              title={t('channels.settings.open')}
              aria-label={t('channels.settings.open')}
            >
              <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="3" />
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
              </svg>
            </button>
          )}
        </span>
        {/* Symétrie visuelle avec le bouton ☰ (en-tête centré). */}
        <span className="w-4 shrink-0" aria-hidden />
      </div>

      {/* Fil fusionné (messages + investigation). */}
      <div ref={scrollRef} onScroll={onScroll} className="flex-1 space-y-3 overflow-y-auto overflow-x-hidden p-3">
        {feed.length === 0 && (
          <p className="mt-12 text-center text-[11px] text-slate-400 dark:text-slate-500">
            {readonly ? t('channels.emptyReadonly') : t('channels.empty')}
          </p>
        )}
        {feed.map((row) => (
          <div key={row.key}>{row.render()}</div>
        ))}
      </div>

      {/* Hint double-Esc — s'affiche entre le fil et le composer pendant 1.5 s. */}
      {escArmed && (
        <div className="flex items-center justify-center gap-1.5 border-t border-slate-200 dark:border-ink-600 bg-slate-50 dark:bg-ink-700 px-3 py-1.5 animate-pulse">
          <kbd className="rounded border border-slate-300 bg-slate-100 dark:border-ink-500 dark:bg-ink-600 px-1 py-0.5 font-mono text-[10px] text-slate-600 dark:text-slate-300">esc</kbd>
          <span className="text-[11px] font-medium text-slate-600 dark:text-slate-300">
            {t('chat.escAgain')}
          </span>
        </div>
      )}

      {/* Composer — désactivé en lecture seule. */}
      <div className="border-t border-slate-200 dark:border-ink-600 p-2">
        {readonly ? (
          <p className="px-1 py-1.5 text-center text-[11px] text-slate-400 dark:text-slate-500">
            🔒 {t('channels.readonlyNote')}
          </p>
        ) : (
          <>
            {/* Bandeau « en réponse à … » au-dessus de l'input. */}
            {replyingTo && (
              <div className="mb-1.5 flex items-center gap-2 rounded-lg border border-navy-200 dark:border-navy-500/30 bg-navy-50/60 dark:bg-navy-500/10 px-2 py-1">
                <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-navy-600 dark:text-navy-300">
                  ↩ {t('channels.replyingTo', { who: replyingTo.author_role })}
                </span>
                <span className="min-w-0 flex-1 truncate text-[11px] text-slate-500 dark:text-slate-400">{replyingTo.text}</span>
                <button onClick={() => setReplyingTo(null)} className="shrink-0 text-slate-400 hover:text-navy-700 dark:hover:text-navy-200" title={t('common.cancel')}>✕</button>
              </div>
            )}
            {/* Barre d'outils AU-DESSUS de l'input — comme dans le chat : pièce jointe,
                et (mod'Hémicycle seulement) la bascule du raisonnement live 🧠. */}
            <div className="mb-1.5 flex items-center gap-1.5">
              <button
                onClick={() => fileRef.current?.click()}
                className="rounded-lg border border-slate-300 dark:border-ink-600 bg-white dark:bg-ink-800 px-2 py-1 text-[13px] font-semibold text-navy-700 dark:text-navy-200 transition hover:border-navy-300 hover:bg-slate-50 dark:hover:bg-ink-700"
                title={t('chat.attachTitle')}
                aria-label={t('chat.attachTitle')}
              >
                📎
              </button>
              {mode === 'hemicycle' && (
                <button
                  onClick={() => setThinkOn((v) => !v)}
                  aria-pressed={thinkOn}
                  className={`rounded-lg border px-2 py-1 text-[13px] font-semibold transition ${
                    thinkOn
                      ? 'border-navy-600 bg-navy-700 text-white'
                      : 'border-slate-300 dark:border-ink-600 bg-white dark:bg-ink-800 text-navy-700 dark:text-navy-200 hover:border-navy-300 hover:bg-slate-50 dark:hover:bg-ink-700'
                  }`}
                  title={t('chat.thinkingTitle')}
                  aria-label={t('chat.thinkingTitle')}
                >
                  🧠
                </button>
              )}
              <input
                ref={fileRef}
                type="file"
                multiple
                className="hidden"
                onChange={(e) => { if (e.target.files?.length) onAttachFiles(e.target.files); e.target.value = '' }}
              />
            </div>
            {/* Chips des fichiers joints en attente. */}
            {attachments.length > 0 && (
              <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
                {attachments.map((a) => (
                  <span
                    key={a.id}
                    className="mono flex max-w-full items-center gap-1 rounded-full border border-navy-200 dark:border-navy-500/30 bg-navy-50 dark:bg-navy-500/10 px-2 py-0.5 text-[10.5px] text-navy-700 dark:text-navy-200"
                  >
                    <span className="shrink-0">📎</span>
                    <span className="truncate" title={a.filename}>{a.filename}</span>
                    <button
                      onClick={() => setAttachments((prev) => prev.filter((x) => x.id !== a.id))}
                      className="shrink-0 text-navy-400 dark:text-navy-500 hover:text-red-600"
                      title={t('chat.removeTitle')}
                    >
                      ✕
                    </button>
                  </span>
                ))}
              </div>
            )}
            <div className="flex gap-2">
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={onKeyDown}
                placeholder={
                  mode === 'hemicycle'
                    ? t('channels.placeholderHémicycle')
                    : t('channels.placeholder', { name: channel?.name || channelId })
                }
                className={`field flex-1 ${mode === 'hemicycle' ? 'ring-1 ring-navy-300 dark:ring-navy-500/40' : ''}`}
              />
              <button
                onClick={submit}
                disabled={!draft.trim()}
                className="btn-primary flex items-center justify-center px-3"
                aria-label={t('channels.send')}
                title={t('channels.send')}
              >
                <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor"
                     strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                  <path d="m22 2-7 20-4-9-9-4Z" />
                  <path d="M22 2 11 13" />
                </svg>
              </button>
            </div>
            {/* Bandeau de mode SOUS l'input — bandeau pleine largeur coloré, cliquable
                pour basculer (équivaut à Shift+Tab, qui reste actif sans indicateur). */}
            <button
              onClick={() => setMode((m) => (m === 'hemicycle' ? 'message' : 'hemicycle'))}
              className={`mt-1 flex w-full items-center justify-center gap-1.5 rounded-md border px-2 py-0.5 text-[10px] font-semibold transition ${
                mode === 'hemicycle'
                  ? 'border-navy-600 bg-navy-700 text-white hover:bg-navy-600'
                  : 'border-slate-300 bg-slate-100 text-slate-600 hover:bg-slate-200 dark:border-ink-600 dark:bg-ink-700 dark:text-slate-300 dark:hover:bg-ink-600'
              }`}
              title={t('channels.modeToggleHint')}
            >
              <span aria-hidden>{mode === 'hemicycle' ? '✦' : '💬'}</span>
              <span className="uppercase tracking-wide">
                {mode === 'hemicycle' ? t('channels.modeHémicycle') : t('channels.modeMessage')}
              </span>
              <span className="font-normal opacity-50">· {t('channels.modeClickHint')}</span>
            </button>
          </>
        )}
      </div>

      {/* Popover de transfert vers un autre salon (avec note optionnelle). */}
      {transferFor && (
        <TransferPopover
          msg={transferFor}
          fromChannel={channel}
          targets={transferTargets}
          onClose={() => setTransferFor(null)}
        />
      )}

      {/* Modale « Modifier le plan » → approbation vers la conversation du salon. */}
      {builderSteps && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-navy-900/40 p-4 backdrop-blur-sm sm:p-8"
          onClick={() => setBuilderSteps(null)}
        >
          <div className="card flex h-[85vh] w-full max-w-6xl animate-popIn flex-col overflow-hidden shadow-pop" onClick={(e) => e.stopPropagation()}>
            <WorkflowBuilder
              mode="apply"
              initialSteps={builderSteps}
              onClose={() => setBuilderSteps(null)}
              onApply={(steps) => { inv.sendPlan(steps); setBuilderSteps(null) }}
            />
          </div>
        </div>
      )}

      {/* Pop-up réglages plein écran : prompt système + mémoire de cellule. */}
      {settingsOpen && channel && (
        <ChannelSettings channel={channel} onClose={() => setSettingsOpen(false)} />
      )}
    </div>
  )
}

// ─── Popover de transfert ───────────────────────────────────────────────────
function TransferPopover({
  msg, fromChannel, targets, onClose,
}: {
  msg: ChannelMessage
  fromChannel?: Channel
  targets: Channel[]
  onClose: () => void
}) {
  const t = useT()
  const [target, setTarget] = useState<string>(targets[0]?.id || '')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  const doTransfer = async () => {
    if (!target || busy) return
    setBusy(true)
    try {
      const isAlert = msg.kind === 'alert'
      const transferFrom = {
        channel_id: msg.channel_id,
        channel_name: fromChannel?.name || msg.channel_id,
        author_role: msg.author_role,
        // Alerte : le corps (msg.text) est re-posté tel quel → `text` porte ici la note
        // de transfert optionnelle. Message normal : on cite l'original.
        text: isAlert ? note.trim() : msg.text,
      }
      await api.postChannelMessage(
        target,
        isAlert
          ? {
              // Alerte transférée : on conserve kind + payload (geo → bouton carte, source,
              // title, alert_kind) et le corps d'origine, pour que le salon cible affiche
              // l'encadré complet avec ses boutons, et non du texte brut.
              text: msg.text,
              author_id: CURRENT_USER.id,
              author_role: msg.author_role,
              kind: 'alert',
              payload: { ...msg.payload, transfer_from: transferFrom },
            }
          : {
              text: note.trim(),
              author_id: CURRENT_USER.id,
              author_role: authorRoleFor(target),
              payload: { transfer_from: transferFrom },
            },
      )
      onClose()
    } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-navy-900/30 p-4 backdrop-blur-sm" onClick={onClose}>
      <div className="card w-full max-w-xs animate-popIn p-3 shadow-pop" onClick={(e) => e.stopPropagation()}>
        <div className="mb-2 flex items-center justify-between">
          <p className="text-[12px] font-semibold text-navy-700 dark:text-navy-200">⤳ {t('channels.transferTitle')}</p>
          <button onClick={onClose} className="text-slate-400 hover:text-navy-700 dark:hover:text-navy-200">✕</button>
        </div>
        <div className="mb-2 rounded-md border border-slate-200 dark:border-ink-600 bg-slate-50 dark:bg-ink-700 px-2 py-1 text-[11px] text-slate-500 dark:text-slate-400">
          <span className="mono text-[9px] uppercase tracking-wide text-slate-400">{msg.author_role}</span>
          <p className="truncate">{msg.text}</p>
        </div>
        <label className="block text-[10px] font-semibold uppercase tracking-wide text-slate-400">{t('channels.transferTo')}</label>
        <select value={target} onChange={(e) => setTarget(e.target.value)} className="field mt-1 w-full text-[12px]">
          {targets.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder={t('channels.addNote')}
          className="field mt-2 w-full text-[12px]"
        />
        <button
          onClick={doTransfer}
          disabled={!target || busy}
          className="btn-primary mt-2 w-full py-1.5 text-[12px] disabled:opacity-50"
        >
          {busy ? '…' : t('channels.transfer')}
        </button>
      </div>
    </div>
  )
}

// ─── Registre extensible des actions contextuelles d'alerte ─────────────────
// Règle unique aujourd'hui : alerte géolocalisée → bouton « Voir sur la map ».
// Ajouter un nouveau type d'alerte = une entrée supplémentaire dans le tableau.
function alertActions(
  msg: ChannelMessage,
  t: (key: string, params?: Record<string, unknown>) => string,
  showOnMap: (geo: { locations: GeoLoc[]; polygons?: GeoPoly[] }) => void,
): { key: string; label: string; icon: string; onClick: () => void }[] {
  const result: { key: string; label: string; icon: string; onClick: () => void }[] = []
  // Alerte géolocalisée : alert_kind=site_monitoring OU payload.geo avec des localisations.
  if (msg.payload?.geo?.locations?.length) {
    result.push({
      key: 'map',
      icon: '🗺',
      label: t('channels.viewOnMap'),
      // payload.geo garanti non-null : on a vérifié .geo?.locations?.length ci-dessus.
      onClick: () => showOnMap(msg.payload.geo!),
    })
  }
  return result
}

// ─── Bulle de message de channel (+ actions au survol) ──────────────────────
function ChannelBubble({
  msg, onReply, onTransfer, onDelete, canPost,
}: {
  msg: ChannelMessage
  onReply: () => void
  onTransfer: () => void
  onDelete: () => void
  canPost: boolean
}) {
  const t = useT()
  const showOnMap = useGeoOverlayStore((s) => s.showOnMap)
  const isMine = msg.author_id === CURRENT_USER.id
  const isAlert = msg.kind === 'alert'
  const reply = msg.payload?.reply_to
  const transfer = msg.payload?.transfer_from
  // Message adressé à Hémicycle (mode @hemicycle) → bordure ambre sur la bulle, en
  // miroir de la réponse de l'orchestrateur et du chat orchestrateur privé.
  const toHémicycle = !!msg.payload?.to_hemicycle

  // Permissions : on peut RÉPONDRE à n'importe quel message (si on peut poster ici)
  // et le TRANSFÉRER (toujours — y compris une alerte Hémicycle ou un salon en lecture
  // seule, puisque le transfert poste dans un AUTRE salon). On ne peut SUPPRIMER que
  // SES PROPRES messages.
  const showReply = canPost
  const showTransfer = true
  const showDelete = canPost && isMine
  const actions = (showReply || showTransfer || showDelete) && (
    <div className={`mt-0.5 flex items-center gap-1 opacity-0 transition group-hover:opacity-100 ${isMine ? 'justify-end' : 'justify-start'}`}>
      {showReply && <ActionBtn label="↩" title="reply" onClick={onReply} />}
      {showTransfer && <ActionBtn label="⤳" title="transfer" onClick={onTransfer} />}
      {showDelete && <ActionBtn label="🗑" title="delete" onClick={onDelete} danger />}
    </div>
  )

  // Alerte système (orchestrateur) : encadré pleine largeur distinct.
  if (isAlert) {
    const alertBtns = alertActions(msg, t, showOnMap)
    // Provenance : source déclarée dans le payload (ex. "STAM") ou author_id en repli.
    const source = msg.payload?.source || msg.author_id
    return (
      <div className="group">
        <div className="animate-fadeInUp rounded-xl border border-navy-300 dark:border-navy-500/40 bg-navy-50 dark:bg-navy-500/10 px-3.5 py-2 text-[13px] leading-relaxed text-slate-800 dark:text-slate-100">
          <div className="mb-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-navy-600 dark:text-navy-300">
            <span aria-hidden>📢</span>
            <span>{msg.author_role}</span>
            {/* Étiquette de provenance (source déclarée dans le payload ou author_id). */}
            <span
              className="rounded bg-navy-100 dark:bg-navy-500/20 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-navy-700 dark:text-navy-300"
              title={t('channels.source')}
            >
              {source}
            </span>
            <span className="mono ml-auto font-normal normal-case text-slate-400 dark:text-slate-500" title={msg.created_at}>{timeAgo(msg.created_at)}</span>
          </div>
          {/* Provenance si l'alerte a été transférée depuis un autre salon (+ note). */}
          {msg.payload?.transfer_from && (
            <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-navy-600 dark:text-navy-300">
              ⤳ {msg.payload.transfer_from.channel_name}
              {msg.payload.transfer_from.text && (
                <span className="ml-1 font-normal normal-case text-slate-500 dark:text-slate-400">— {msg.payload.transfer_from.text}</span>
              )}
            </div>
          )}
          <Markdown>{msg.text}</Markdown>
          {/* Boutons d'action contextuels propres au type de cette alerte. */}
          {alertBtns.length > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              {alertBtns.map((btn) => (
                <button
                  key={btn.key}
                  onClick={btn.onClick}
                  className="flex items-center gap-1 rounded-lg border border-navy-300 dark:border-navy-500/40 bg-white dark:bg-ink-800 px-2 py-1 text-[11px] font-medium text-navy-700 dark:text-navy-200 transition hover:bg-navy-50 dark:hover:bg-navy-500/10 hover:border-navy-400 dark:hover:border-navy-400/60"
                >
                  <span aria-hidden>{btn.icon}</span>
                  <span>{btn.label}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        {actions}
      </div>
    )
  }

  return (
    <div className="group flex animate-fadeInUp flex-col">
      <div className={`flex ${isMine ? 'items-end self-end' : 'items-start self-start'} max-w-[85%] flex-col`}>
        {/* En-tête auteur (rôle + heure). */}
        <div className={`mb-0.5 flex items-center gap-1.5 px-1 text-[9px] text-slate-400 dark:text-slate-500 ${isMine ? 'justify-end' : 'justify-start'}`}>
          <span className="font-medium text-slate-500 dark:text-slate-400">{msg.author_role}</span>
          <span className="mono" title={msg.created_at}>{timeAgo(msg.created_at)}</span>
        </div>
        <div
          className={`break-words rounded-2xl px-3.5 py-2 text-[13px] leading-relaxed ${
            isMine
              // Mon message : bulle teal clair à droite. `toHémicycle` (adressé à
              // l'orchestrateur) signalé par un anneau teal plutôt qu'une bordure ambre.
              ? `rounded-br-md bg-teal-100 text-slate-800 shadow-xs dark:bg-teal-500/20 dark:text-slate-100${toHémicycle ? ' ring-1 ring-teal-400/70 dark:ring-teal-400/50' : ''}`
              : 'rounded-bl-md border border-slate-200 bg-white text-slate-800 shadow-card dark:border-ink-600 dark:bg-ink-800 dark:text-slate-100'
          }`}
        >
          {/* Encart « transféré de … ». */}
          {transfer && (
            <div className={`mb-1.5 rounded-lg border-l-2 px-2 py-1 text-[11px] ${isMine ? 'border-teal-400/50 bg-white/50 dark:bg-ink-700/40' : 'border-navy-300 bg-navy-50/60 dark:border-navy-500/40 dark:bg-navy-500/10'}`}>
              <div className={`mb-0.5 text-[9px] font-semibold uppercase tracking-wide ${isMine ? 'text-teal-700 dark:text-teal-300' : 'text-navy-600 dark:text-navy-300'}`}>
                ⤳ {transfer.channel_name} · {transfer.author_role}
              </div>
              <div className={isMine ? 'text-slate-700 dark:text-slate-200' : 'text-slate-600 dark:text-slate-300'}>{transfer.text}</div>
            </div>
          )}
          {/* Citation du message auquel on répond. */}
          {reply && (
            <div className={`mb-1.5 rounded-lg border-l-2 px-2 py-1 text-[11px] ${isMine ? 'border-teal-400/50 bg-white/50 dark:bg-ink-700/40' : 'border-slate-300 bg-slate-50 dark:border-ink-500 dark:bg-ink-700'}`}>
              <div className={`mb-0.5 text-[9px] font-semibold uppercase tracking-wide ${isMine ? 'text-slate-500 dark:text-slate-400' : 'text-slate-500 dark:text-slate-400'}`}>↩ {reply.author_role}</div>
              <div className={`truncate ${isMine ? 'text-slate-600 dark:text-slate-300' : 'text-slate-600 dark:text-slate-300'}`}>{reply.text}</div>
            </div>
          )}
          {msg.text && (isMine ? <span>{msg.text}</span> : <Markdown>{msg.text}</Markdown>)}
        </div>
      </div>
      {actions}
    </div>
  )
}

function ActionBtn({ label, title, onClick, danger }: { label: string; title: string; onClick: () => void; danger?: boolean }) {
  const t = useT()
  return (
    <button
      onClick={onClick}
      title={t(`channels.${title}` as any)}
      aria-label={t(`channels.${title}` as any)}
      className={`rounded px-1 py-0.5 text-[11px] leading-none transition hover:bg-slate-100 dark:hover:bg-ink-700 ${
        danger ? 'text-slate-400 hover:text-red-600' : 'text-slate-400 hover:text-navy-700 dark:hover:text-navy-200'
      }`}
    >
      {label}
    </button>
  )
}
