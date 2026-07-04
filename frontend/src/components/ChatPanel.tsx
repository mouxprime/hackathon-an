// Panneau de chat de la conversation active : timeline d'événements + barre d'input.
//
// Rendu pur — le parent (DashboardView) gère :
//   - la conv active (via le store global),
//   - la création de conv à la volée si l'analyste écrit sans en avoir,
//   - l'ouverture du panneau historique (overlay slidant par-dessus ce chat).
//
// L'input est toujours actif : envoyer du texte sans conv déclenche la
// création d'une conv dans le parent via `submit(text)`.
//
// Thème : îlot blanc crème (texte navy), bulles user navy / assistant blanches.

import type React from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useT } from '../lib/i18n'
import { useVoiceInput, useSpeaker } from '../lib/voice'
import { authorRoleFor, useContextStore, useConvStore } from '../lib/store'
import { TransferToChannel } from './TransferToChannel'
import type { AgentEnriched, Conversation, ConvRef, PlanStep, Progress, UIEvent } from '../lib/types'
import { AgentSlashMenu } from './AgentSlashMenu'
import { DirectAgentChat } from './DirectAgentChat'
import { Markdown } from './Markdown'
import { RecycleIcon } from './ContextPanel'
import { SlashCommandMenu, filterSlashCommands, type SlashCommandName } from './SlashCommandMenu'
import { useConvMention } from '../lib/useConvMention'
import { ConvMentionPicker } from './ConvMentionPicker'
import { ContextRefChips } from './ContextRefChips'
import { AttachMenu } from './AttachMenu'
import { TokenBadge } from './TokenBadge'
import { InvestigationFeed, buildTimeline, type TimelineItem } from './InvestigationFeed'
import { CompactionProgress } from './InvestigationFeed'

type Attach = { id: string; filename: string }


// Historique de commandes persisté (shell-like). Partagé entre toutes les
// conversations : l'analyste tape souvent les mêmes prompts.
const HISTORY_KEY = 'hemicycle.chat.history.v1'
const HISTORY_MAX = 100

// Badge de tokens de contexte : information technique, masquée par défaut
// pour le public citoyen. Réactivable en un flag.
const SHOW_TOKEN_BADGE = false

function loadHistory(): string[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY)
    if (!raw) return []
    const arr = JSON.parse(raw)
    return Array.isArray(arr) ? arr.filter((x) => typeof x === 'string') : []
  } catch { return [] }
}

function saveHistory(items: string[]) {
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(items.slice(-HISTORY_MAX))) }
  catch { /* quota */ }
}

type Props = {
  events: UIEvent[]
  progress: Record<string, Progress>
  traces: Progress[]
  thinking: Record<string, { text: string; done: boolean }>
  // Synthèses de chat de l'orchestrateur streamées (kind=message_chunk), par stream_id.
  assistantStream: Record<string, { text: string; done: boolean }>
  connected: boolean
  submit: (text: string, opts?: { thinking?: boolean; forceReport?: boolean }) => void
  // Messages envoyés mais pas encore echoés : texte + pièces jointes (badge immédiat).
  pendingSent: { text: string; attachments: Attach[] }[]
  onCancel: () => void
  // Barre d'actions au-dessus de l'input + boucle de plan.
  onOpenWorkflow: () => void
  onAttachFiles: (files: FileList) => void
  attachments: Attach[]
  onRemoveAttachment: (id: string) => void
  // Clic sur le badge 📎 d'une pièce jointe → ouvre le lecteur de fichier.
  onOpenAttachment?: (att: Attach) => void
  selectedWorkflow: { id: string; name: string } | null
  onClearWorkflow: () => void
  onApprovePlan: (steps: PlanStep[]) => void
  onModifyPlan: (steps: PlanStep[]) => void
  // Décision de l'analyste sur un widget memory_proposal (round-trip WS → FSM
  // → widget_update, l'état décidé survit au refresh via l'event-sourcing).
  onMemoryDecision: (
    widgetId: string,
    decisions: { index: number; action: 'confirm' | 'reject'; kind?: string; content?: string }[],
  ) => void
  // Commandes slash + badge tokens.
  contextTokens?: number
  convTitle?: string
  onCommand: (name: SlashCommandName) => void
  // Renommage in-place (la barre d'input devient le champ de titre) + compaction en cours.
  onRename: (title: string) => void
  compacting?: boolean
  // Convs référencées via @ chargées en contexte pour la prochaine requête.
  contextRefs: ConvRef[]
  onContextRefsChange: (refs: ConvRef[]) => void
  // Flèche de repli du chat (← dans le header).
  onCollapse?: () => void
  // Vrai si une modale du dashboard (builder, popups, widget plein écran) est
  // ouverte → on neutralise le double-Esc global (Échap y ferme la modale).
  overlayOpen?: boolean
}

export function ChatPanel({ events, progress, traces, thinking, assistantStream, connected, submit, pendingSent, onCancel, onOpenWorkflow, onAttachFiles, attachments, onRemoveAttachment, onOpenAttachment, selectedWorkflow, onClearWorkflow, onApprovePlan, onModifyPlan, onMemoryDecision, contextTokens = 0, convTitle = '', onCommand, onRename, compacting = false, contextRefs, onContextRefsChange, onCollapse, overlayOpen = false }: Props) {
  const t = useT()
  const { convId } = useConvStore()
  const { maxTokens } = useContextStore()
  const [draft, setDraft] = useState('')
  // Mode renommage : la barre d'input devient le champ de titre (pas de modale).
  // Entrée valide, Échap annule. On masque le menu slash pendant ce mode.
  const [renameMode, setRenameMode] = useState(false)
  // Sélection d'agent (/agents) : menu inline ouvert au-dessus de l'input.
  const [agentPicking, setAgentPicking] = useState(false)
  // Mode direct-agent : on parle à un agent A2A hors orchestrateur (panneau dédié).
  const [directAgent, setDirectAgent] = useState<{ id: string; discipline?: string } | null>(null)
  // Bouton focalisé sur un plan proposé (navigation ←/→), Entrée l'exécute.
  const [planFocus, setPlanFocus] = useState<'approve' | 'modify'>('approve')
  // Menu slash : visible si draft commence par '/' sans espace (hors mode rename/picking).
  const slashOpen = !renameMode && !agentPicking && draft.startsWith('/') && !draft.includes(' ')
  // Items filtrés + curseur surligné : gérés ICI (l'input onKeyDown pilote la
  // navigation et la sélection), le menu n'est que présentationnel.
  const slashItems = useMemo(() => (slashOpen ? filterSlashCommands(draft) : []), [slashOpen, draft])
  const [slashCursor, setSlashCursor] = useState(0)
  // Recentre le curseur quand la liste change (nouvelle frappe / ouverture).
  useEffect(() => { setSlashCursor(0) }, [slashItems.length, slashOpen])
  // Bascules de la requête : raisonnement live (thinking) + synthèse rédigée (rapport).
  // `thinkOn` activé par défaut : l'analyste voit le raisonnement de construction
  // du plan se streamer dans le bandeau de préparation (transparence). Désactivable.
  const [thinkOn, setThinkOn] = useState(true)
  const [reportOn, setReportOn] = useState(false)
  // Message du chat privé en cours de transfert vers un salon (popover ouvert).
  const [transferMsg, setTransferMsg] = useState<{ role: string; text: string } | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  // Menu du trombone (Local file / recherche fédérée) ancré sur la barre de saisie.
  const [attachMenuOpen, setAttachMenuOpen] = useState(false)

  // Voix — TTS et état audio déclarés AVANT le callback STT (référencés dedans).
  const speaker = useSpeaker()
  const [ttsOn, setTtsOn] = useState(false)
  // Dernier message déjà lu : évite de relire l'historique à l'activation.
  const lastSpokenSeq = useRef(-1)
  // Ref miroir du timeline courant : donne au callback STT le max seq sans
  // dépendance mutable (useEffect le maintient à jour après chaque render).
  const timelineRef = useRef<{ seq: number }[]>([])

  // Micro (STT) → envoi automatique + activation TTS pour lire la réponse.
  // Pipeline complet mains-libres : parler → agent → écouter.
  const voice = useVoiceInput(useCallback((text: string) => {
    // Marquer tous les messages déjà affichés comme « déjà lus » pour que
    // seule la prochaine réponse de l'agent soit lue à voix haute.
    lastSpokenSeq.current = timelineRef.current.reduce((m, it) => Math.max(m, it.seq), -1)
    setTtsOn(true)
    submit(text, { thinking: thinkOn, forceReport: reportOn })
  }, [submit, thinkOn, reportOn]))

  // Hook @ mention : détecte le trigger dans le draft, filtre les convs.
  const mention = useConvMention(draft, convId)
  const mentionPickerOpen = mention.pickerOpen && !renameMode && !slashOpen

  // Callback de sélection dans le ConvMentionPicker.
  const handleMentionSelect = (conv: Conversation) => {
    const { draft: newDraft, ref } = mention.select(conv)
    setDraft(newDraft)
    if (!contextRefs.some((r) => r.conv_id === ref.conv_id)) {
      onContextRefsChange([...contextRefs, ref])
    }
  }

  // Historique de commandes navigable par ArrowUp/ArrowDown. `cursor` est
  // l'index de l'entrée affichée (-1 = pas de navigation en cours, on est sur
  // le draft « live » de l'analyste). On garde le draft live dans
  // `stashedDraft` pour le restaurer quand on redescend en bas de l'historique.
  const [history, setHistory] = useState<string[]>(loadHistory)
  const [cursor, setCursor] = useState(-1)
  const stashedDraft = useRef<string>('')

  // Double-Esc GLOBAL : un listener `window` (pas seulement l'input du chat)
  // capte Échap quel que soit l'élément focalisé. 1er appui → hint 1.5 s, 2e
  // dans la fenêtre → onCancel(). Neutralisé pendant les sous-modes qui ont
  // leur propre sémantique d'Échap (rename, menus slash/@, agent direct).
  const [escArmed, setEscArmed] = useState(false)
  const escTimerRef = useRef<number | null>(null)

  const timeline = useMemo(() => buildTimeline(events), [events])
  // Garde timelineRef synchronisé pour le callback STT (cf. useVoiceInput ci-dessus).
  useEffect(() => { timelineRef.current = timeline }, [timeline])
  // Dernier plan proposé NON encore décidé (cible de la navigation clavier).
  const pendingPlanSeq = useMemo(() => {
    let seq: number | null = null
    for (const it of timeline) {
      if (it.kind === 'widget' && it.widget_type === 'plan_proposal' && !it.data?.decided) seq = it.seq
    }
    return seq
  }, [timeline])

  // À chaque nouveau plan en attente, le focus repart sur « Approuver » (Entrée approuve).
  useEffect(() => { setPlanFocus('approve') }, [pendingPlanSeq])

  // TTS actif : lit chaque nouveau message de l'assistant une seule fois (les
  // messages arrivent en events durables à texte complet → pas de lecture partielle).
  useEffect(() => {
    if (!ttsOn) return
    for (const it of timeline) {
      if (it.kind !== 'message' || it.role === 'user' || it.msgKind === 'compaction') continue
      if (it.seq <= lastSpokenSeq.current) continue
      lastSpokenSeq.current = it.seq
      if (it.text) speaker.speak(it.text)
    }
  }, [ttsOn, timeline, speaker])

  // Bascule la lecture vocale. À l'activation on part du dernier message affiché
  // (pas de relecture du backlog) ; à la coupure on stoppe l'audio en cours.
  const toggleTts = () => {
    setTtsOn((on) => {
      const next = !on
      if (next) lastSpokenSeq.current = timeline.reduce((m, it) => Math.max(m, it.seq), -1)
      else speaker.cancel()
      return next
    })
  }

  // Auto-scroll « suiveur » : si l'analyste est déjà en bas du chat, on suit le
  // flux (nouveaux messages, étapes d'agent, tool calls qui se streament). Dès
  // qu'il scrolle vers le haut pour relire, on s'arrête — il garde la main et
  // peut revenir en bas naturellement, ce qui rearme le suivi.
  const [followBottom, setFollowBottom] = useState(true)
  // Seuil en px sous lequel on considère que l'utilisateur « est en bas » et
  // veut être suivi automatiquement (laisse une marge pour les arrondis et les
  // micro-scrolls trackpad).
  const FOLLOW_THRESHOLD_PX = 80
  // Détecte la position de l'utilisateur à chaque scroll → met à jour `followBottom`.
  const onScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    setFollowBottom(distFromBottom <= FOLLOW_THRESHOLD_PX)
  }, [])
  // Tick d'auto-scroll : déclenché à chaque update du flux (timeline, in-flight,
  // progress streamés, traces, thinking, plans en attente). Si `followBottom`,
  // on suit ; sinon, on laisse l'analyste tranquille.
  useEffect(() => {
    if (!followBottom) return
    const el = scrollRef.current
    if (!el) return
    // Scroll INSTANTANÉ (pas `behavior:'smooth'`) : pendant le streaming le contenu
    // grandit à chaque token, donc `scrollHeight` change en continu. Une animation
    // smooth redémarrait à chaque update sans jamais se stabiliser → scintillement,
    // et son état intermédiaire (loin du bas) faisait basculer followBottom à false
    // (le suivi se coupait tout seul). Le saut direct au bas reste fluide visuellement
    // car les incréments sont minuscules.
    el.scrollTop = el.scrollHeight
  }, [followBottom, timeline.length, pendingSent.length, progress, traces, thinking, assistantStream])

  const onSubmit = () => {
    const text = draft.trim()
    if (!text) return
    setDraft('')              // clear AU MOMENT de l'envoi (UX)
    submit(text, { thinking: thinkOn, forceReport: reportOn })
    // Append à l'historique (dédoublonnage si répétition immédiate du même).
    setHistory((prev) => {
      const next = prev[prev.length - 1] === text ? prev : [...prev, text]
      saveHistory(next)
      return next
    })
    setCursor(-1)
    stashedDraft.current = ''
  }

  // Exécute la commande sélectionnée depuis le menu slash.
  // Cas spécial /rename : on bascule la barre d'input en champ de titre VIDE
  // (placeholder « Renommez votre conversation » visible) au lieu d'une modale.
  const handleSlashSelect = (name: SlashCommandName) => {
    if (name === 'rename') {
      if (!convId) { setDraft(''); onCommand('rename'); return } // parent gère le toast « pas de conv »
      setRenameMode(true)
      setDraft('')
      inputRef.current?.focus()
      return
    }
    if (name === 'agents') {
      // Sélection inline (pas de modale) : on ouvre le menu d'agents au-dessus de l'input.
      setDraft('')
      setAgentPicking(true)
      return
    }
    setDraft('')
    onCommand(name)
  }

  // Valide le renommage in-place puis quitte le mode.
  const commitRename = () => {
    const v = draft.trim()
    if (v) onRename(v)
    setRenameMode(false)
    setDraft('')
  }
  const cancelRename = () => {
    setRenameMode(false)
    setDraft('')
  }

  // Navigation historique : ArrowUp recule, ArrowDown avance. Quand on est sur
  // le draft live (cursor === -1), Up le « stashe » avant de remonter.
  // Le menu slash est piloté directement ICI (↑/↓/Enter/Tab/Esc) — plus de
  // listener window, donc la sélection part de façon fiable sur Entrée.
  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    // Mode renommage : Entrée valide, Échap annule. On court-circuite toute la
    // logique d'historique/slash — la frappe libre passe par onChange.
    if (renameMode) {
      if (e.key === 'Enter') { e.preventDefault(); commitRename(); return }
      if (e.key === 'Escape') { e.preventDefault(); cancelRename(); return }
      return
    }
    // Sélection d'agent en cours : AgentSlashMenu gère les touches (capture window).
    if (agentPicking) return
    // Plan proposé en attente : ←/→ basculent Approuver/Modifier, Entrée exécute le
    // bouton focalisé. Actif seulement si l'input est vide (sinon Entrée = envoi d'un
    // message, human-in-the-loop ; les flèches déplacent le curseur dans le texte).
    if (pendingPlanSeq !== null && !slashOpen && !draft.trim()) {
      if (e.key === 'ArrowLeft') { e.preventDefault(); setPlanFocus('approve'); return }
      if (e.key === 'ArrowRight') { e.preventDefault(); setPlanFocus('modify'); return }
      if (e.key === 'Enter') {
        e.preventDefault()
        const item = timeline.find((it) => it.seq === pendingPlanSeq) as Extract<TimelineItem, { kind: 'widget' }> | undefined
        const steps: PlanStep[] = item?.data?.steps || []
        if (planFocus === 'approve') onApprovePlan(steps)
        else onModifyPlan(steps)
        return
      }
    }
    // Menu slash ouvert : on gère TOUT ici (plus de listener window). ↑/↓ déplacent
    // le surlignage, Entrée/Tab sélectionnent, Échap ferme. Si la liste est vide
    // (préfixe sans correspondance), Entrée/Échap retombent sur le flux normal.
    if (slashOpen && slashItems.length > 0) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setSlashCursor((c) => Math.min(c + 1, slashItems.length - 1)); return }
      if (e.key === 'ArrowUp') { e.preventDefault(); setSlashCursor((c) => Math.max(c - 1, 0)); return }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault()
        const cmd = slashItems[Math.min(slashCursor, slashItems.length - 1)]
        if (cmd) handleSlashSelect(cmd.name)
        return
      }
      if (e.key === 'Escape') { e.preventDefault(); setDraft(''); return }
    }
    // Même garde que slashOpen : les touches de navigation sont gérées par
    // ConvMentionPicker via window capture — on évite le double-traitement.
    if (mentionPickerOpen) {
      if (['ArrowUp', 'ArrowDown', 'Tab', 'Enter', 'Escape'].includes(e.key)) {
        e.preventDefault()
        return
      }
    }

    if (e.key === 'Enter') {
      onSubmit()
      return
    }
    if (e.key === 'ArrowUp') {
      if (history.length === 0) return
      e.preventDefault()
      if (cursor === -1) stashedDraft.current = draft
      const nextCursor = cursor === -1 ? history.length - 1 : Math.max(0, cursor - 1)
      setCursor(nextCursor)
      setDraft(history[nextCursor])
      return
    }
    if (e.key === 'ArrowDown') {
      if (cursor === -1) return
      e.preventDefault()
      const nextCursor = cursor + 1
      if (nextCursor >= history.length) {
        setCursor(-1)
        setDraft(stashedDraft.current)
      } else {
        setCursor(nextCursor)
        setDraft(history[nextCursor])
      }
      return
    }
    // NB : Échap n'est PLUS traité ici — le double-Esc « stopper la conv » est
    // un listener `window` (cf. plus bas) pour fonctionner même sans focus input.
  }

  // Logique double-Esc partagée : 1er appui arme (+ hint 1.5 s), 2e stoppe.
  const triggerEscStop = () => {
    if (escArmed) {
      if (escTimerRef.current) { clearTimeout(escTimerRef.current); escTimerRef.current = null }
      setEscArmed(false)
      onCancel()
      return
    }
    setEscArmed(true)
    if (escTimerRef.current) clearTimeout(escTimerRef.current)
    escTimerRef.current = window.setTimeout(() => {
      setEscArmed(false)
      escTimerRef.current = null
    }, 1500)
  }

  // Toute frappe libre (lettres, suppr, etc.) sort de la navigation historique.
  const onChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setDraft(e.target.value)
    if (cursor !== -1) setCursor(-1)
  }

  // Listener global du double-Esc : Échap capté au niveau `window` pour stopper
  // la conv même si le focus n'est PAS dans l'input du chat (canvas, bouton,
  // widget…). Neutralisé pendant les sous-modes qui gèrent Échap eux-mêmes.
  useEffect(() => {
    if (renameMode || agentPicking || slashOpen || mentionPickerOpen || directAgent || overlayOpen) return
    const onWindowEsc = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      triggerEscStop()
    }
    window.addEventListener('keydown', onWindowEsc)
    return () => window.removeEventListener('keydown', onWindowEsc)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [renameMode, agentPicking, slashOpen, mentionPickerOpen, directAgent, overlayOpen, escArmed, onCancel])

  // Cleanup du timer Esc au démontage — évite un setEscArmed après unmount.
  useEffect(() => () => {
    if (escTimerRef.current) clearTimeout(escTimerRef.current)
  }, [])

  // Mode direct-agent : on remplace tout le panneau par la discussion A2A dédiée
  // (en-tête au nom de l'agent, hors orchestrateur). Retour au chat via ◀ ou Échap.
  if (directAgent) {
    return (
      <DirectAgentChat
        agentId={directAgent.id}
        discipline={directAgent.discipline}
        onExit={() => setDirectAgent(null)}
      />
    )
  }

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-slate-200 dark:border-ink-600 bg-cream dark:bg-ink-800 shadow-card">
      <div className="flex items-center justify-between gap-2 border-b border-slate-200 dark:border-ink-600 bg-white/60 dark:bg-ink-700/50 px-3 py-2">
        {/* Titre de la conv (ou court uuid en repli). La navigation entre
            conversations se fait désormais via la sidebar persistante. */}
        <span className="truncate text-[11px] text-slate-500 dark:text-slate-400 font-medium" title={convId || ''}>
          {convTitle || (convId ? convId.slice(0, 8) : '')}
        </span>
        {onCollapse && (
          <button
            onClick={onCollapse}
            className="shrink-0 text-slate-400 dark:text-slate-500 transition hover:text-navy-700 dark:hover:text-navy-200"
            title={t('dashboard.collapseChat')}
            aria-label={t('dashboard.collapseChat')}
          >
            ▾
          </button>
        )}
      </div>

      <div ref={scrollRef} onScroll={onScroll} className="flex-1 space-y-3 overflow-y-auto overflow-x-hidden p-3">
        {!convId && (
          <div className="mt-8 flex flex-col items-center px-2 text-center">
            <h2 className="text-[17px] font-bold text-navy-700 dark:text-navy-200">
              Hémicycle — votre assistant citoyen
            </h2>
            <p className="mt-1.5 max-w-[320px] text-[12px] leading-relaxed text-slate-500 dark:text-slate-400">
              Cet assistant cite ses sources officielles et dit quand il ne sait pas.
            </p>
            <div className="mt-4 flex w-full max-w-[340px] flex-col gap-2">
              {[
                'Comment les députés ont-ils voté sur la motion de censure du 8 octobre 2024 ?',
                'Qui est le député de la 1re circonscription de la Gironde ?',
                'Où en est le projet de loi sur la fin de vie ?',
                "C'est quoi l'article 49.3 ?",
              ].map((q) => (
                <button
                  key={q}
                  type="button"
                  onClick={() => { setDraft(q); inputRef.current?.focus() }}
                  className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-left text-[12px] leading-snug text-slate-700 shadow-xs transition hover:border-teal-300 hover:bg-teal-50/50 dark:border-ink-600 dark:bg-ink-800 dark:text-slate-200 dark:hover:border-teal-500/40 dark:hover:bg-teal-500/10"
                >
                  {q}
                </button>
              ))}
            </div>
            <p className="mt-5 text-[10.5px] text-slate-400 dark:text-slate-500">
              Données : Assemblée nationale — Licence Ouverte Etalab
            </p>
          </div>
        )}
        {/* ─── Refonte du déroulé ───
            On agrège la timeline en blocs sémantiques :
            – PlanCard (en haut du tour, contient toutes les étapes + leur statut)
            – AgentCard par sous-agent (étendue running / pill done)
            – Rapport (filé vers le widget texteditor, JAMAIS rendu ici)
            La « pensée » brute n'est plus affichée — trop verbeuse, casse le layout. */}
        <InvestigationFeed
          events={events}
          progress={progress}
          traces={traces}
          thinking={thinking}
          assistantStream={assistantStream}
          reportEnabled={reportOn}
          planFocus={planFocus}
          pendingPlanSeq={pendingPlanSeq}
          onApprovePlan={onApprovePlan}
          onModifyPlan={onModifyPlan}
          onMemoryDecision={onMemoryDecision}
          onTransferToChannel={setTransferMsg}
          onOpenAttachment={onOpenAttachment}
        />
        {/* Messages envoyés mais pas encore echoés par le bus — affichage
            optimiste, opacité réduite pour indiquer « en cours d'envoi ». Le
            badge 📎 est rendu ICI dès l'envoi (pas après le plan). */}
        {pendingSent.map((item, i) => (
          <div key={`pending-${i}-${item.text.slice(0, 16)}`} className="flex justify-end">
            <div className="flex max-w-[85%] flex-col items-end gap-1 rounded-2xl rounded-br-md bg-teal-100/80 px-3.5 py-2 text-sm text-slate-700 dark:bg-teal-500/20 dark:text-slate-200">
              <div className="flex items-center gap-2">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-teal-500" title={t('chat.sending')} />
                <span>{item.text}</span>
              </div>
              {item.attachments.length > 0 && (
                <div className="flex flex-wrap justify-end gap-1">
                  {item.attachments.map((a) => (
                    <button
                      key={a.id}
                      type="button"
                      onClick={() => onOpenAttachment?.(a)}
                      className="mono rounded border border-teal-300/70 bg-white/70 px-1.5 py-0.5 text-[10px] text-teal-800 transition hover:bg-white dark:border-teal-400/40 dark:bg-ink-700 dark:text-teal-200"
                      title={a.filename}
                    >
                      📎 {a.filename}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        {/* Compaction en cours : barre de progression indéterminée (cf. spawn d'agent). */}
        {compacting && <CompactionProgress />}
      </div>

      {/* Hint double-Esc — s'affiche entre le feed et la toolbar pendant la fenêtre de 1.5 s */}
      {escArmed && (
        <div className="flex items-center justify-center gap-1.5 border-t border-slate-200 dark:border-ink-600 bg-slate-50 dark:bg-ink-700 px-3 py-1.5 animate-pulse">
          <kbd className="rounded border border-slate-300 bg-slate-100 dark:border-ink-500 dark:bg-ink-600 px-1 py-0.5 font-mono text-[10px] text-slate-600 dark:text-slate-300">esc</kbd>
          <span className="text-[11px] font-medium text-slate-600 dark:text-slate-300">
            {t('chat.escAgain')}
          </span>
        </div>
      )}

      {/* Barre d'actions secondaire : options discrètes (le trombone et le micro
          sont désormais dans la ligne de saisie principale, cf. maquette). */}
      <div className="flex flex-wrap items-center gap-1.5 border-t border-slate-200 dark:border-ink-600 px-2 pt-2">
        {/* 🧠 Thinking — icône seule */}
        <button
          onClick={() => setThinkOn((v) => !v)}
          aria-pressed={thinkOn}
          className={`rounded-lg border px-2 py-1 text-[13px] font-semibold transition ${
            thinkOn
              ? 'border-teal-500 bg-teal-500 text-white'
              : 'border-slate-300 dark:border-ink-600 bg-white dark:bg-ink-800 text-navy-700 dark:text-navy-200 hover:border-teal-300 hover:bg-slate-50 dark:hover:bg-ink-700'
          }`}
          title={t('chat.thinkingTitle')}
          aria-label={t('chat.thinkingTitle')}
        >
          🧠
        </button>
        {/* 📝 Rapport — texte conservé */}
        <button
          onClick={() => setReportOn((v) => !v)}
          aria-pressed={reportOn}
          className={`rounded-lg border px-2 py-1 text-[11px] font-semibold transition ${
            reportOn
              ? 'border-teal-500 bg-teal-500 text-white'
              : 'border-slate-300 dark:border-ink-600 bg-white dark:bg-ink-800 text-navy-700 dark:text-navy-200 hover:border-teal-300 hover:bg-slate-50 dark:hover:bg-ink-700'
          }`}
          title={t('chat.reportTitle')}
        >
          📝 {t('chat.report')}
        </button>
        {/* ⚙ Workflow — texte conservé */}
        <button
          onClick={onOpenWorkflow}
          className="rounded-lg border border-slate-300 dark:border-ink-600 bg-white dark:bg-ink-800 px-2 py-1 text-[11px] font-semibold text-navy-700 dark:text-navy-200 transition hover:border-teal-300 hover:bg-slate-50 dark:hover:bg-ink-700"
          title={t('chat.workflowTitle')}
        >
          ⚙ {t('chat.workflow')}
        </button>
        {/* 🔊 Lecture vocale (TTS) — lit les réponses de l'assistant à voix haute */}
        <button
          onClick={toggleTts}
          aria-pressed={ttsOn}
          className={`rounded-lg border px-2 py-1 text-[13px] font-semibold transition ${
            ttsOn
              ? 'border-teal-500 bg-teal-500 text-white'
              : speaker.error
                ? 'border-red-300 bg-white dark:bg-ink-800 text-red-500'
                : 'border-slate-300 dark:border-ink-600 bg-white dark:bg-ink-800 text-navy-700 dark:text-navy-200 hover:border-teal-300 hover:bg-slate-50 dark:hover:bg-ink-700'
          }`}
          title={speaker.error ? t('chat.speakError') : ttsOn ? t('chat.speakOffTitle') : t('chat.speakOnTitle')}
          aria-label={ttsOn ? t('chat.speakOffTitle') : t('chat.speakOnTitle')}
        >
          {ttsOn ? '🔊' : '🔈'}
        </button>
        <input
          ref={fileRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => { if (e.target.files?.length) onAttachFiles(e.target.files); e.target.value = '' }}
        />
        {selectedWorkflow && (
          <span className="mono flex items-center gap-1 rounded-full border border-navy-200 dark:border-navy-500/30 bg-navy-50 dark:bg-navy-500/15 px-2 py-0.5 text-[10px] text-navy-700 dark:text-navy-300">
            ⚙ {selectedWorkflow.name}
            <button onClick={onClearWorkflow} className="text-navy-400 hover:text-navy-700 dark:hover:text-navy-200" title={t('chat.removeWorkflowTitle')}>✕</button>
          </span>
        )}
        {/* Badge de tokens — MASQUÉ par défaut (UI citoyenne). Remettre
            SHOW_TOKEN_BADGE à true pour le ré-afficher (coulisses de l'IA). */}
        {SHOW_TOKEN_BADGE && (
          <span className="ml-auto">
            <TokenBadge
              tokens={contextTokens}
              max={maxTokens}
              onCompact={() => onCommand('compact')}
            />
          </span>
        )}
      </div>

      {/* Chips des fichiers attachés — rangée dédiée sous la barre d'actions
          pour qu'un PDF au nom long ne déborde plus à côté des boutons. */}
      {attachments.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 px-2 pt-1.5">
          {attachments.map((a) => (
            <span
              key={a.id}
              className="mono flex max-w-full items-center gap-1 rounded-full border border-navy-200 dark:border-navy-500/30 bg-navy-50 dark:bg-navy-500/10 px-2 py-0.5 text-[10.5px] text-navy-700 dark:text-navy-200"
            >
              <span className="shrink-0">📎</span>
              <span className="truncate" title={a.filename}>{a.filename}</span>
              <button
                onClick={() => onRemoveAttachment(a.id)}
                className="shrink-0 text-navy-400 dark:text-navy-500 hover:text-red-600"
                title={t('chat.removeTitle')}
              >
                ✕
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Zone de contexte @ : chips des convs référencées pour la prochaine requête. */}
      <ContextRefChips
        refs={contextRefs}
        onRemove={(id) => onContextRefsChange(contextRefs.filter((r) => r.conv_id !== id))}
      />

      <div className="relative flex items-center gap-2 p-2">
        {/* Menu slash ancré au-dessus de l'input. */}
        {slashOpen && (
          <SlashCommandMenu
            items={slashItems}
            cursor={slashCursor}
            onSelect={handleSlashSelect}
            onHover={setSlashCursor}
          />
        )}
        {/* Picker @ mention : au-dessus de l'input, même position que SlashCommandMenu. */}
        {mentionPickerOpen && (
          <ConvMentionPicker
            items={mention.filtered}
            onSelect={handleMentionSelect}
            onClose={() => setDraft(mention.close())}
          />
        )}
        {/* Menu inline d'agents (/agents) : même ancrage que le menu slash. */}
        {agentPicking && (
          <AgentSlashMenu
            onSelect={(a: AgentEnriched) => { setAgentPicking(false); setDirectAgent({ id: a.id, discipline: a.discipline }) }}
            onClose={() => setAgentPicking(false)}
          />
        )}
        {/* Trombone : ouvre le menu d'upload (Local file / recherche fédérée). */}
        {!renameMode && (
          <div className="relative shrink-0">
            <button
              onClick={() => setAttachMenuOpen((v) => !v)}
              aria-expanded={attachMenuOpen}
              className="rounded-lg p-2 text-slate-500 transition hover:bg-teal-50 hover:text-teal-600 dark:text-slate-400 dark:hover:bg-ink-700 dark:hover:text-teal-300"
              title={t('chat.attachTitle')}
              aria-label={t('chat.attachTitle')}
            >
              <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round">
                <path d="M21.44 11.05l-9.19 9.19a5 5 0 0 1-7.07-7.07l9.19-9.19a3.5 3.5 0 0 1 4.95 4.95l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
              </svg>
            </button>
            {attachMenuOpen && (
              <AttachMenu
                onLocalFile={() => fileRef.current?.click()}
                onFedSearch={() => inputRef.current?.focus()}
                onClose={() => setAttachMenuOpen(false)}
              />
            )}
          </div>
        )}
        <input
          ref={inputRef}
          value={agentPicking ? '' : draft}
          onChange={onChange}
          onKeyDown={onKeyDown}
          readOnly={agentPicking}
          placeholder={
            agentPicking ? t('chat.pickAgentPlaceholder')
              : renameMode ? t('chat.renamePlaceholder')
                : convId ? t('chat.placeholderAsk')
                  : t('chat.placeholderNew')
          }
          className={`field flex-1 ${renameMode ? 'animate-ringPulse' : ''}`}
        />
        <button
          onClick={renameMode ? commitRename : onSubmit}
          disabled={!draft.trim()}
          className="btn-accent shrink-0 flex items-center justify-center px-3"
          aria-label={renameMode ? t('common.save') : t('chat.send')}
          title={renameMode ? t('common.save') : t('chat.send')}
        >
          {renameMode ? (
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor"
                 strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
              <path d="M20 6 9 17l-5-5" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor"
                 strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
              <path d="M22 2 11 13" />
              <path d="M22 2 15 22l-4-9-9-4 20-7z" />
            </svg>
          )}
        </button>
        {/* Micro (STT) : enregistre, transcrit puis envoie automatiquement. */}
        {!renameMode && (
          <button
            onClick={voice.toggle}
            disabled={voice.state === 'transcribing'}
            aria-pressed={voice.state === 'recording'}
            className={`shrink-0 rounded-lg p-2 transition ${
              voice.state === 'recording'
                ? 'animate-pulse bg-red-500 text-white'
                : voice.state === 'transcribing'
                  ? 'cursor-wait text-slate-400'
                  : voice.error
                    ? 'text-red-500'
                    : 'text-slate-500 hover:bg-teal-50 hover:text-teal-600 dark:text-slate-400 dark:hover:bg-ink-700 dark:hover:text-teal-300'
            }`}
            title={
              voice.error ? t('chat.micError')
                : voice.state === 'recording' ? t('chat.micRecordingTitle')
                  : voice.state === 'transcribing' ? t('chat.micTranscribingTitle')
                    : t('chat.micIdleTitle')
            }
            aria-label={t('chat.micIdleTitle')}
          >
            {voice.state === 'transcribing' ? (
              <span className="text-[15px]">⏳</span>
            ) : (
              <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round">
                <rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
              </svg>
            )}
          </button>
        )}
        {/* Bandeau du mode renommage : rappel des raccourcis. */}
        {renameMode && (
          <div className="absolute -top-7 left-2 right-2 flex items-center justify-center">
            <span className="mono rounded-md border border-navy-300 dark:border-navy-500/30 bg-navy-50 dark:bg-navy-500/15 px-2 py-0.5 text-[10px] text-navy-700 dark:text-navy-300 shadow-pop">
              {t('chat.renameHint')}
            </span>
          </div>
        )}
      </div>

      {/* Popover de transfert d'un message du chat privé vers un salon d'état-major. */}
      {transferMsg && (
        <TransferToChannel
          content={{
            author_role: transferMsg.role === 'user' ? authorRoleFor(null) : 'Hémicycle',
            text: transferMsg.text,
          }}
          sourceLabel={convTitle || 'Hémicycle'}
          onClose={() => setTransferMsg(null)}
        />
      )}
    </div>
  )
}
