// Dashboard : canvas de widgets plein écran (React Flow) + chat flottant par-dessus.
//
//   ┌───────────────────────────────────────────────────────────┐
//   │  canvas pannable/zoomable (fond crème)                      │
//   │   ┌────────┐   ┌────────┐                                   │
//   │   │ Tâches │   │ Carte  │     (widgets = nœuds libres)      │
//   │   └────────┘   └────────┘                                   │
//   │  ┌────────────┐                                             │
//   │  │ Chat       │                                       [ + ] │
//   │  │ flottant   │                                             │
//   │  └────────────┘                                             │
//   └───────────────────────────────────────────────────────────┘
//
// Le chat flotte en bas à gauche (repliable), l'historique slide par-dessus lui.
// Les widgets se déposent depuis la bibliothèque (drag&drop ou clic « + »), se
// déplacent et se redimensionnent ; positions persistées en localStorage.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { ChatPanel } from '../components/ChatPanel'
import { ChannelPanel } from '../components/ChannelPanel'
import { WidgetFrame } from '../components/WidgetFrame'
import { ContextPanel } from '../components/ContextPanel'
import { ConversationSidebar } from '../components/ConversationHistory'
import { WidgetCanvas, type WidgetPos } from '../components/WidgetCanvas'
import { WidgetIcon } from '../components/WidgetIcon'
import { WidgetTaskbar, type TaskbarEntry } from '../components/WidgetTaskbar'
import { WorkflowBuilder } from '../components/WorkflowBuilder'
import { WorkflowPopup } from '../components/WorkflowPopup'
import { api } from '../lib/api'
import { useConversation, usePolling } from '../lib/hooks'
import { useT } from '../lib/i18n'
import { useChannelStore, useContextStore, useConvStore, DASHBOARD_LS_KEY, useReportStreamStore, useTextEditorStore, useFileViewerStore } from '../lib/store'
import { useGeoOverlayStore } from '../lib/geoOverlayStore'
import { useToast } from '../lib/toast'
import type { ConvRef, ExecutionTraceEntry, PlanStep, Progress, UIEvent, Workflow } from '../lib/types'
import { DEFAULT_ACTIVE, WIDGETS, specById, specByInstanceId, makeInstanceId, type WidgetCtx } from '../lib/widgetRegistry'
import type { SlashCommandName } from '../components/SlashCommandMenu'

type Attach = { id: string; filename: string }
type SelectedWorkflow = { id: string; name: string; steps: PlanStep[] }
type EnrichedConvRef = ConvRef & { events: UIEvent[] }
type Pending = {
  text: string; attachments: Attach[]; workflowSteps?: PlanStep[]
  thinking?: boolean; forceReport?: boolean
  contextRefs?: EnrichedConvRef[]   // convs référencées avec leurs événements déjà fetchés
}

type MinimizedWidget = {
  id: string        // instance ID (ex. 'pyramid-1716000000001')
  specId: string    // pour retrouver l'icône (ex. 'pyramid')
  title: string     // nom affiché dans la taskbar
  savedPos: WidgetPos
}

const WIDGET_TITLES_LS_KEY = 'hemicycle.dashboard.titles.v1'

function loadWidgetTitles(): Record<string, string> {
  try {
    const raw = localStorage.getItem(WIDGET_TITLES_LS_KEY)
    return raw ? JSON.parse(raw) : {}
  } catch { return {} }
}

function persistWidgetTitles(titles: Record<string, string>) {
  try { localStorage.setItem(WIDGET_TITLES_LS_KEY, JSON.stringify(titles)) } catch {}
}

const LS_KEY = DASHBOARD_LS_KEY

// Largeur px occultée à gauche par le chat flottant (panneau w-[420px] + marge
// `left-4` + respiration). Sert à centrer un widget dans la zone RÉELLEMENT
// visible du tableau (à droite du chat), pas au centre brut de l'écran.
const CHAT_W = 452

// État complet du tableau persisté en localStorage. Outre les widgets actifs et
// leur géométrie, on persiste désormais l'empilement (zOrder + compteur), les
// widgets minimisés et le widget en plein écran : sans ça, un F5 perdait le
// z-order ET faisait disparaître définitivement tout widget minimisé (un rapport
// minimisé n'étant ni dans `active`, ni re-listé). Champs optionnels = un ancien
// localStorage (sans eux) se recharge sans casse, valeurs par défaut appliquées.
type Persisted = {
  active: string[]
  positions: Record<string, WidgetPos>
  zOrder?: Record<string, number>
  zCounter?: number
  minimized?: MinimizedWidget[]
  fullscreenWidgetId?: string | null
}

// Taille de spawn de la CARTE — règle GLOBALE : peu importe qui la fait apparaître
// (détection auto de points OU ajout manuel via la bibliothèque), elle spawn à
// cette taille. 2.5× un widget standard pour s'afficher en grand et lisible.
const MAP_SPAWN = { w: 1200, h: 950 }
// Taille de spawn du TEXTEDITOR — largeur maximale autorisée par le resize :
// page A4 (794 px) + 20 px de marge externe gauche/droite + 3 px de bordure
// chacun = 840 px. Au-delà, la feuille déborderait — c'est aussi le maxWidth
// du NodeResizeControl (cf. WidgetCanvas), donc on spawn pile à la limite.
const TEXT_EDITOR_SPAWN = { w: 840, h: 1080 }

// Taille pixel par défaut d'un widget, dérivée de son layout grille historique
// (cas spéciaux : carte et texteditor suivent une règle de spawn dédiée).
// Accepte un instanceId (ex. 'map-1716000000001') ou un specId pur ('map').
function defaultSize(idOrSpecId: string): { w: number; h: number } {
  const spec = specByInstanceId(idOrSpecId)
  const specId = spec?.id ?? idOrSpecId
  if (specId === 'map') return { ...MAP_SPAWN }
  if (specId === 'texteditor') return { ...TEXT_EDITOR_SPAWN }
  return {
    w: spec ? Math.max(280, spec.defaultLayout.w * 80) : 360,
    h: spec ? Math.max(180, spec.defaultLayout.h * 46) : 300,
  }
}

// Distance verticale/horizontale d'évitement quand un widget atterrirait sur
// un autre : on shift en cascade jusqu'à trouver une zone libre.
function rectsOverlap(a: { x: number; y: number; w: number; h: number },
                      b: { x: number; y: number; w: number; h: number }): boolean {
  return !(a.x + a.w <= b.x || b.x + b.w <= a.x ||
           a.y + a.h <= b.y || b.y + b.h <= a.y)
}

// Trouve une position libre pour un nouveau widget (taille `size`) sans recouvrir
// les widgets `existing`. Heuristique : on tente la position de départ `start`,
// puis on balaie une grille (pas de 80 px) en spirale rectangulaire jusqu'à un
// trou. Limite de 200 tentatives — au-delà on pose en cascade classique pour ne
// jamais bloquer (mieux qu'un faux NULL).
function findFreePosition(
  size: { w: number; h: number },
  existing: Array<{ x: number; y: number; w: number; h: number }>,
  start: { x: number; y: number },
  chatWidth: number,
): { x: number; y: number } {
  const step = 80
  const PAD = 16  // marge entre widgets (pour ne pas se coller)
  const minX = chatWidth + 16
  const minY = 16
  const fits = (x: number, y: number) =>
    x >= minX && y >= minY &&
    !existing.some((r) => rectsOverlap({ x: x - PAD, y: y - PAD, w: size.w + 2 * PAD, h: size.h + 2 * PAD }, r))
  if (fits(start.x, start.y)) return { x: start.x, y: start.y }
  // Spirale : on s'éloigne progressivement de la position initiale.
  for (let radius = 1; radius <= 14; radius++) {
    // ligne du bas, ligne du haut, colonne droite, colonne gauche du carré.
    for (let dx = -radius; dx <= radius; dx++) {
      for (const dy of [-radius, radius]) {
        const x = Math.max(minX, start.x + dx * step)
        const y = Math.max(minY, start.y + dy * step)
        if (fits(x, y)) return { x, y }
      }
    }
    for (let dy = -radius + 1; dy <= radius - 1; dy++) {
      for (const dx of [-radius, radius]) {
        const x = Math.max(minX, start.x + dx * step)
        const y = Math.max(minY, start.y + dy * step)
        if (fits(x, y)) return { x, y }
      }
    }
  }
  // Repli : cascade ; mieux qu'un blocage UX.
  return { x: start.x + existing.length * 32, y: start.y + existing.length * 32 }
}

// Tableau vide canonique — réutilisé au reset et en repli de chargement.
function emptyBoard(): Required<Persisted> {
  return { active: [...DEFAULT_ACTIVE], positions: {}, zOrder: {}, zCounter: 1, minimized: [], fullscreenWidgetId: null }
}

// Charge la persistance v3 (canvas) ou retombe sur un dashboard vide. Les champs
// d'ergonomie (zOrder/minimized/fullscreen) sont tolérants : absents ou malformés
// → on retombe sur leurs valeurs par défaut sans invalider le reste du tableau.
function loadPersisted(): Required<Persisted> {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (!raw) throw new Error('vide')
    const p = JSON.parse(raw)
    if (!Array.isArray(p.active) || typeof p.positions !== 'object') throw new Error('bad')
    return {
      active: p.active,
      positions: p.positions || {},
      zOrder: p.zOrder && typeof p.zOrder === 'object' ? p.zOrder : {},
      zCounter: typeof p.zCounter === 'number' ? p.zCounter : 1,
      minimized: Array.isArray(p.minimized) ? p.minimized : [],
      fullscreenWidgetId: typeof p.fullscreenWidgetId === 'string' ? p.fullscreenWidgetId : null,
    }
  } catch {
    return emptyBoard()
  }
}

function persist(p: Persisted) {
  try { localStorage.setItem(LS_KEY, JSON.stringify(p)) } catch { /* quota */ }
}

export function DashboardView() {
  const t = useT()
  const { convId, setConvId, resetBoard } = useConvStore()
  const boardResetSeq = useConvStore((s) => s.boardResetSeq)
  // Channel actif : quand non-null, le panneau de chat affiche le salon (ChannelPanel).
  const channelId = useChannelStore((s) => s.channelId)
  const { events, progress, traces, thinking, assistantStream, connected, wsError, send, sendPlan, cancel, compact, sendMemoryDecision, sendMapPoint } = useConversation(convId)
  // Conversation DU SALON actif (chan-<id>), souscrite UNIQUEMENT pour donner le bon
  // contexte aux widgets du canvas : un widget server-driven ouvert depuis un salon
  // doit lire les events ET soumettre sur
  // la conv du salon, pas sur la conv privée. ChannelPanel garde sa propre souscription
  // pour le rendu du fil. `useConversation(null)` = no-op (pas de WS) hors salon.
  const channelConv = useConversation(channelId ? `chan-${channelId}` : null)
  const toast = useToast()
  const navigate = useNavigate()

  // Polling de la conversation active pour les métadonnées live (tokens, titre).
  const convData = usePolling(
    () => (convId ? api.conversation(convId) : Promise.resolve(null)),
    3000,
    [convId],
  )
  const contextTokens = convData?.context_tokens ?? 0
  const convTitle = convData?.title ?? ''
  const { maxTokens } = useContextStore()
  const setReportTextToEditor = useTextEditorStore((s) => s.setReportText)
  const textEditorActiveId = useTextEditorStore((s) => s.activeEditorId)
  // Buffers de rapport streamés (par stream_id), alimentés dans hooks.ts. On les
  // reflète en live dans le texteditor (cf. effet plus bas).
  const reportBuffers = useReportStreamStore((s) => s.buffers)

  // Modales / état des commandes slash.
  const [contextPopupOpen, setContextPopupOpen] = useState(false)
  // Compaction en cours : barre de progression dans le chat jusqu'au message résumé.
  const [compacting, setCompacting] = useState(false)
  const compactBaselineRef = useRef(0)        // seq max au déclenchement (détection de fin)
  const compactTimerRef = useRef<number | null>(null)
  const autoCompactArmed = useRef(true)       // anti-rafale de l'auto-compaction au seuil

  // Lance une compaction : mémorise la baseline de seq, affiche la barre de
  // progression et publie l'intent. Repli de sécurité à 60 s si aucun résumé n'arrive.
  const runCompact = () => {
    if (!convId) { toast.warn(t('chat.cmd.noConv')); return }
    if (compacting) return
    compactBaselineRef.current = events.reduce((m, e) => Math.max(m, e.seq), 0)
    setCompacting(true)
    compact()
    if (compactTimerRef.current) clearTimeout(compactTimerRef.current)
    compactTimerRef.current = window.setTimeout(() => setCompacting(false), 60000)
  }

  // Renommage in-place déclenché par /rename (la barre d'input devient le champ titre).
  const handleRename = async (title: string) => {
    if (!convId || !title.trim()) return
    try {
      await api.renameConversation(convId, title.trim())
      toast.success(t('chat.cmd.rename.done'))
    } catch (e: any) {
      toast.error(t('chat.cmd.rename.failed'), String(e?.message || e))
    }
  }

  // Boot unique : on lit le localStorage une seule fois, puis on hydrate chaque
  // morceau d'état (canvas, minimisés, fullscreen, z-order) à partir de lui.
  // MAIS le tableau est lié à une conversation : sans conversation active au
  // démarrage (refresh / rollout du pod — `convId` n'est jamais persisté), on
  // boote sur un tableau VIDE et on ignore le persisté (sinon des widgets
  // fantômes réapparaissent hors de toute conversation). Cf. effet `convId`.
  const [boot] = useState(() => (useConvStore.getState().convId ? loadPersisted() : emptyBoard()))
  const [persisted, setPersisted] = useState<Persisted>(() => ({ active: boot.active, positions: boot.positions }))
  const [minimizedWidgets, setMinimizedWidgets] = useState<MinimizedWidget[]>(() => boot.minimized)
  const [fullscreenWidgetId, setFullscreenWidgetId] = useState<string | null>(() => boot.fullscreenWidgetId)
  const [widgetTitles, setWidgetTitles] = useState<Record<string, string>>(loadWidgetTitles)
  const [libraryOpen, setLibraryOpen] = useState(false)
  const [librarySearch, setLibrarySearch] = useState('')
  const [chatOpen, setChatOpen] = useState(true)
  // Widget sur lequel recentrer/zoomer le canvas (ex. la carte au spawn auto).
  const [focusId, setFocusId] = useState<string | null>(null)
  // Cadrage caméra sur une zone (bbox) — vue combinée éditeur de rapport + carte.
  const [focusBounds, setFocusBounds] = useState<{ x: number; y: number; w: number; h: number } | null>(null)
  // Ordre d'empilement (z-index) des widgets : instanceId → entier monotone
  // croissant. Cliquer une fenêtre (taskbar ou nœud) la remonte au premier plan.
  const [zOrder, setZOrder] = useState<Record<string, number>>(() => boot.zOrder)
  const zCounterRef = useRef(boot.zCounter)

  // Remonte un widget au premier plan : lui attribue le prochain z-index le plus haut.
  const bringToFront = (id: string) => {
    zCounterRef.current += 1
    const next = zCounterRef.current
    setZOrder((z) => ({ ...z, [id]: next }))
  }

  // Pièces jointes + workflow pré-sélectionné pour la prochaine requête.
  const [attachments, setAttachments] = useState<Attach[]>([])
  const [selectedWorkflow, setSelectedWorkflow] = useState<SelectedWorkflow | null>(null)
  // Convs référencées via @ pour la prochaine requête (state vidé au submit et au changement de conv).
  const [contextRefs, setContextRefs] = useState<ConvRef[]>([])
  // Modales workflow : popup (liste) + builder (canvas React Flow, mode "apply").
  const [workflowPopupOpen, setWorkflowPopupOpen] = useState(false)
  const [builder, setBuilder] = useState<{ mode: 'save' | 'apply'; initialSteps: PlanStep[] } | null>(null)

  // Texte en attente quand l'analyste envoie sans avoir de conv active.
  const [pending, setPending] = useState<Pending | null>(null)

  // Bulles « optimistes » : textes envoyés mais pas encore echoés par le bus.
  // Affichage optimiste des messages envoyés mais pas encore echoés par le bus.
  // Porte AUSSI les pièces jointes pour que le badge 📎 s'affiche IMMÉDIATEMENT
  // (et pas seulement quand l'orchestrateur echoe le message après son plan).
  const [optimistic, setOptimistic] = useState<{ text: string; attachments: Attach[] }[]>([])
  // Traces historiques hydratées depuis Postgres (replay au refresh/reconnect).
  const [restTraces, setRestTraces] = useState<Progress[]>([])
  // Ref pour distinguer « création de nouvelle conv » de « navigation vers une conv existante ».
  const prevConvIdRef = useRef<string | null | undefined>(undefined)

  // Overlay géo éphémère (alertes canaux) : nonce incrémenté à chaque `showOnMap`.
  const geoOverlayNonce = useGeoOverlayStore((s) => s.nonce)
  const geoOverlayLen = useGeoOverlayStore((s) => s.locations.length)

  // Garde « carte auto » : on n'auto-affiche la carte qu'une fois par conversation
  // (sinon on la ré-impose en boucle si l'analyste la ferme volontairement).
  const autoMapDone = useRef(false)
  // Garde du nettoyage de tableau : ignore le run de montage (sinon on viderait
  // le tableau persisté au simple chargement / changement de route).
  const boardResetInit = useRef(true)
  // Nonce de l'overlay géo déjà traité : évite de re-spawner la carte en boucle.
  const lastGeoNonceRef = useRef(0)

  useEffect(() => {
    const prevId = prevConvIdRef.current
    prevConvIdRef.current = convId
    // Lors de la création d'une nouvelle conv (null → UUID depuis un submit),
    // ne pas effacer l'optimistic ni les refs : ils ont déjà été capturés par submitChat().
    const creatingNewConv = prevId === null && convId !== null
    if (!creatingNewConv) {
      setOptimistic([])
      setContextRefs([])
    }
    autoMapDone.current = false
    lastReportSeqRef.current = 0
    setCompacting(false); autoCompactArmed.current = true
  }, [convId])

  // Hydrate les traces d'exécution depuis la persistance Postgres au changement de conv.
  // Compense la perte des traces WS éphémères au refresh ou à la reconnexion.
  useEffect(() => {
    if (!convId) { setRestTraces([]); return }
    api.executionTrace(convId)
      .then((entries: ExecutionTraceEntry[]) =>
        setRestTraces(entries.map((e): Progress => ({
          corr_id: e.corr_id,
          conv_id: convId,
          agent_id: e.agent_id,
          worker_id: '',
          pct: 0,
          note: '',
          kind: e.kind,
          trace_data: e.data,
          ts: e.ts,
        })))
      )
      .catch(() => { /* trace non critique — pyramide silencieusement incomplète */ })
  }, [convId])

  // Fin de compaction : un nouveau message assistant après la baseline = résumé posé.
  useEffect(() => {
    if (!compacting) return
    const done = events.some(
      (e) => e.type === 'message' && e.payload?.role === 'assistant' && e.seq > compactBaselineRef.current,
    )
    if (done) {
      setCompacting(false)
      if (compactTimerRef.current) { clearTimeout(compactTimerRef.current); compactTimerRef.current = null }
    }
  }, [events, compacting])

  // Auto-compaction : dès que le contexte atteint le seuil max, on lance un compact
  // (une seule fois par franchissement — réarmé quand on repasse sous le seuil).
  useEffect(() => {
    if (!convId || maxTokens <= 0) return
    const over = contextTokens >= maxTokens
    if (over && autoCompactArmed.current && !compacting) {
      autoCompactArmed.current = false
      runCompact()
    } else if (!over) {
      autoCompactArmed.current = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contextTokens, maxTokens, convId, compacting])

  // Réconcilie l'optimiste avec le vrai (match par texte des messages user reçus).
  useEffect(() => {
    if (!optimistic.length) return
    const userTexts: string[] = events
      .filter((e) => e.type === 'message' && e.payload?.role === 'user')
      .map((e) => e.payload.text)
    if (!userTexts.length) return
    setOptimistic((prev) => {
      const remaining = [...prev]
      for (const t of userTexts) {
        const i = remaining.findIndex((o) => o.text === t)
        if (i >= 0) remaining.splice(i, 1)
      }
      return remaining.length === prev.length ? prev : remaining
    })
  }, [events, optimistic.length])

  // Persiste l'état COMPLET du tableau dès qu'une de ses facettes bouge (widgets,
  // géométrie, empilement, minimisés, fullscreen). zCounter est lu sur la ref :
  // il est incrémenté en même temps que zOrder, donc capturé à jour ici.
  useEffect(() => {
    persist({
      active: persisted.active,
      positions: persisted.positions,
      zOrder,
      zCounter: zCounterRef.current,
      minimized: minimizedWidgets,
      fullscreenWidgetId,
    })
  }, [persisted, zOrder, minimizedWidgets, fullscreenWidgetId])

  // Carte auto : dès qu'un résultat porte des points géo (SEARCH/GEOINT) OU des
  // emprises (CSD : footprints), on fait apparaître le widget carte sur le tableau
  // s'il n'y est pas déjà — l'analyste n'a pas à l'ajouter à la main pour voir le
  // marqueur ou la zone (cf. message « N point(s)/zone(s) sur la carte »).
  useEffect(() => {
    if (autoMapDone.current) return
    const hasGeo = events.some(
      (e) => e.type === 'widget'
        && ((Array.isArray(e.payload?.data?.locations) && e.payload.data.locations.length > 0)
          || (Array.isArray(e.payload?.data?.polygons) && e.payload.data.polygons.length > 0)),
    )
    if (!hasGeo) return
    autoMapDone.current = true
    // Une carte est-elle déjà sur le tableau ? On teste par TYPE (specId), pas par
    // ID brut : l'ancien garde `includes('map')` ratait les instances créées à la
    // main (`map-<ts>-<n>`) → on pouvait se retrouver avec deux cartes. Si elle
    // existe déjà, on se contente de la recadrer.
    const existingMap = persisted.active.find((id) => specByInstanceId(id)?.id === 'map')
    if (existingMap) { setFocusId(existingMap); return }
    // Sinon on spawn une vraie instance unique (jamais l'ID brut 'map').
    const mapInstanceId = makeInstanceId('map')
    setPersisted((p) => {
      // Taille de spawn globale (cf. MAP_SPAWN) ; la caméra se recentre dessus
      // juste après via `focusId`. Position : emplacement libre, pas une cascade
      // qui risquerait de recouvrir un widget déjà posé par l'analyste.
      const { w, h } = defaultSize('map')
      const existing = Object.values(p.positions)
      const start = { x: (chatOpen ? CHAT_W : 0) + 32, y: 80 }
      const at = findFreePosition({ w, h }, existing, start, chatOpen ? CHAT_W : 0)
      return {
        active: [...p.active, mapInstanceId],
        positions: { ...p.positions, [mapInstanceId]: { x: at.x, y: at.y, w, h } },
      }
    })
    setFocusId(mapInstanceId) // recentre le canvas sur la carte (zone à droite du chat)
  }, [events])

  // Nettoyage du tableau sur signal `resetBoard` (logo / nouvelle
  // investigation). On saute le run de montage : le persisté du localStorage a
  // déjà été pris en compte (et effacé en amont par resetBoard si besoin).
  useEffect(() => {
    if (boardResetInit.current) { boardResetInit.current = false; return }
    clearBoardState()
    autoMapDone.current = false
  }, [boardResetSeq])

  // Tableau lié à la conversation : aucune conversation active ⇒ tableau blanc.
  // Couvre le refresh/rollout (convId repart à null) ET la suppression de la conv
  // active depuis l'historique (setConvId(null) sans resetBoard). `clearBoardState`
  // est idempotent : sur un tableau déjà vide, ce no-op ne perturbe rien.
  useEffect(() => {
    if (convId) return
    clearBoardState()
    autoMapDone.current = false
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [convId])

  // Overlay géo (alertes canal) → spawn-ou-focus la carte.
  // Si une instance carte est déjà posée on la recadre (GeoMap lit le store
  // directement → les symboles apparaissent sans aucun remount). Sinon on en
  // crée une. Un ref évite de re-traiter le même nonce plusieurs fois.
  useEffect(() => {
    if (geoOverlayNonce === 0) return                          // état initial
    if (geoOverlayNonce === lastGeoNonceRef.current) return    // déjà traité
    lastGeoNonceRef.current = geoOverlayNonce
    const existingMap = persisted.active.find((id) => specByInstanceId(id)?.id === 'map')
    if (existingMap) {
      setFocusId(existingMap)
    } else {
      addWidget('map')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [geoOverlayNonce, geoOverlayLen])

  // Ouverture de widgets pilotée par l'ORCHESTRATEUR : il émet des événements
  // `spawn_widget` (« ouvre / affiche le widget X ») → on les transforme en
  // addWidget. Si une instance du widget est déjà posée, on la recadre plutôt que
  // d'empiler un doublon. Garde par seq, réinitialisée au changement de conv
  // (event_seq est par-conversation) → chaque demande n'est traitée qu'une fois.
  const spawnWidgetSeqRef = useRef(0)
  useEffect(() => { spawnWidgetSeqRef.current = 0 }, [convId])
  useEffect(() => {
    const reqs = events.filter(
      (e) => e.type === 'spawn_widget' && e.seq > spawnWidgetSeqRef.current,
    )
    if (reqs.length === 0) return
    for (const e of reqs) {
      spawnWidgetSeqRef.current = Math.max(spawnWidgetSeqRef.current, e.seq)
      const key = String(e.payload?.widget || '')
      if (!specById(key)) continue // clé inconnue → ignorée
      const existing = persisted.active.find((id) => specByInstanceId(id)?.id === key)
      if (existing) setFocusId(existing)
      else addWidget(key)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events])

  // Streaming live du rapport → texteditor. À chaque mise à jour d'un buffer
  // (chunk reçu dans hooks.ts), on reflète le texte COMPLET accumulé du rapport
  // en cours dans l'éditeur actif, par réconciliation idempotente. Insensible à
  // la course de montage : si l'éditeur apparaît en cours de rédaction, la
  // prochaine mise à jour du buffer lui pousse d'un coup tout le texte déjà
  // streamé (l'ancien chemin par deltas perdait ces tokens).
  useEffect(() => {
    if (!textEditorActiveId) return
    const active = Object.values(reportBuffers).find((b) => b.text && !b.done)
    if (active) setReportTextToEditor(active.text)
  }, [reportBuffers, textEditorActiveId, setReportTextToEditor])

  // Filet canonique : à l'arrivée du widget rapport final (texte complet,
  // event-sourcé), on l'écrit dans l'éditeur (idempotent : si le live l'a déjà
  // affiché, c'est un no-op côté widget) et on libère le buffer de streaming.
  // S'il n'y a pas encore d'éditeur, on en fait apparaître un et on attend son
  // mount (lastReportSeqRef non incrémenté → l'effet rejouera) : rien n'est perdu.
  const lastReportSeqRef = useRef(0)
  useEffect(() => {
    const reportEvents = events.filter(
      (e) =>
        e.type === 'widget' &&
        e.payload?.widget_type === 'report' &&
        e.seq > lastReportSeqRef.current,
    )
    if (reportEvents.length === 0) return
    if (!textEditorActiveId) {
      const hasTextEditor = persisted.active.some(
        (id) => specByInstanceId(id)?.id === 'texteditor',
      )
      if (!hasTextEditor) spawnReportEditor()
      return  // attend le mount du widget
    }
    for (const evt of reportEvents) {
      const widgetId = evt.payload?.widget_id as string | undefined
      const text = evt.payload?.data?.text ?? evt.payload?.data?.content ?? ''
      if (text) setReportTextToEditor(text)
      // Le widget canonique est désormais la source de vérité — on libère le buffer.
      if (widgetId) useReportStreamStore.getState().clear(widgetId)
      lastReportSeqRef.current = Math.max(lastReportSeqRef.current, evt.seq)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events, textEditorActiveId, setReportTextToEditor, persisted.active])

  // Fait apparaître l'éditeur dès le PREMIER report_chunk de la rédaction.
  // L'orchestrateur émet un chunk « start » vide avant le 1er token (cf.
  // emit_report_chunk start) : on s'accroche au flux ÉPHÉMÈRE du rapport lui-même,
  // PAS à l'événement de statut durable WRITING_REPORT. Ce dernier transite par un
  // canal séparé (JetStream) et pouvait arriver APRÈS la fin de la rédaction
  // éphémère → l'éditeur spawnait vide puis se remplissait d'un coup (aucun
  // streaming visible). Ici l'éditeur est monté pile au début du stream, puis
  // l'effet de réconciliation ci-dessus y déverse chaque token. Une rédaction =
  // un éditeur (garde via textEditorActiveId + présence d'un texteditor).
  useEffect(() => {
    const streaming = Object.values(reportBuffers).some((b) => !b.done)
    if (!streaming || textEditorActiveId) return
    const hasTextEditor = persisted.active.some(
      (id) => specByInstanceId(id)?.id === 'texteditor',
    )
    if (!hasTextEditor) spawnReportEditor()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reportBuffers, textEditorActiveId, persisted.active])

  // Flush du message en attente dès qu'on est connecté à la nouvelle conv ou après
  // une reconnexion réussie (la reconnexion dans hooks.ts rend `connected` true et
  // flush aussi la sendQueue interne — le pending de DashboardView couvre le cas
  // où la conv elle-même n'existait pas encore au moment de l'envoi).
  useEffect(() => {
    if (pending && convId && connected) {
      send(pending.text, {
        attachments: pending.attachments, workflowSteps: pending.workflowSteps,
        thinking: pending.thinking, forceReport: pending.forceReport,
        contextRefs: pending.contextRefs,
      })
      setPending(null)
    }
  }, [pending, convId, connected, send])

  // Échec permanent de reconnexion (MAX_ATTEMPTS épuisées dans hooks.ts) :
  // toast d'erreur visible + abandon du pending (ne peut plus être délivré).
  useEffect(() => {
    if (!wsError) return
    toast.error('Connection lost', 'Message could not be delivered — please retry.')
    setPending(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wsError])

  // Soumission du chat : crée une conv au besoin, fetche les events des convs
  // référencées (@mention), et met le tout en attente jusqu'à connexion WS.
  const submitChat = async (text: string, opts?: { thinking?: boolean; forceReport?: boolean }) => {
    // Capture les pièces jointes courantes dans l'entrée optimiste (le state
    // `attachments` est vidé juste après) → badge visible dès l'envoi.
    setOptimistic((prev) => [...prev, { text, attachments: [...attachments] }])
    if (!convId) setConvId(crypto.randomUUID())

    // Note : le widget texteditor n'est PAS créé ici. Il apparaît au passage en
    // WRITING_REPORT (cf. effet ci-dessus) pour ne pas rester vide pendant toute
    // la phase d'investigation.

    // Fetch lazy : on appelle l'API seulement au moment de l'envoi.
    const enrichedRefs: EnrichedConvRef[] = []
    for (const ref of contextRefs) {
      try {
        const events = await api.events(ref.conv_id)
        // On ne garde que les messages substantiels (type=message), 200 derniers max.
        // Inclut les messages ET les widgets porteurs de contenu : `text` (rapport),
        // `reply` (réponse directe) ET `summary` (synthèse des résultats SEARCH/IMINT/
        // GEOINT) — sans `summary`, la RÉPONSE d'une investigation serait exclue du
        // contexte et l'orchestrateur ré-investiguerait une question déjà répondue.
        const msgEvents = events.filter((e) =>
          e.type === 'message' ||
          (e.type === 'widget' && (e.payload?.data?.text || e.payload?.data?.reply || e.payload?.data?.summary))
        ).slice(-200)
        enrichedRefs.push({ ...ref, events: msgEvents })
      } catch {
        toast.warn(t('chat.contextRefFailed', { title: ref.title }))
      }
    }

    setPending({
      text, attachments, workflowSteps: selectedWorkflow?.steps,
      thinking: opts?.thinking, forceReport: opts?.forceReport,
      contextRefs: enrichedRefs.length > 0 ? enrichedRefs : undefined,
    })
    setAttachments([])
    setSelectedWorkflow(null)
    // Contexte @ « collant » : on NE vide PAS contextRefs après envoi — la/les
    // conv(s) référencée(s) restent attachées aux tours suivants jusqu'au retrait
    // manuel (chip ✕). Le reset se fait au changement de conversation (cf. effet convId).
  }

  const onAttachFiles = async (files: FileList) => {
    for (const file of Array.from(files)) {
      try {
        const a = await api.uploadAttachment(file, convId ?? undefined)
        setAttachments((prev) => [...prev, { id: a.id, filename: a.filename }])
      } catch (e: any) {
        toast.error(t('dashboard.attachmentRejected', { name: file.name }), e?.message || String(e))
      }
    }
  }

  const onApprovePlan = (steps: PlanStep[]) => sendPlan(steps)
  const onModifyPlan = (steps: PlanStep[]) => setBuilder({ mode: 'apply', initialSteps: steps })

  // Exécution des commandes slash : chaque branche est indépendante.
  const onCommand = async (name: SlashCommandName) => {
    switch (name) {
      // 'agents' est géré inline par ChatPanel (menu + chat direct) — pas de modale ici.
      case 'clear': {
        // Suppression directe (pas de confirmation : geste explicite via /clear).
        if (!convId) { toast.warn(t('chat.cmd.noConv')); break }
        try {
          await api.deleteConversation(convId)
          setConvId(null)
          resetBoard()
          toast.success(t('chat.cmd.clear.done'))
        } catch (e: any) {
          // 409 = tâche en cours
          if (String(e?.message).includes('409')) {
            toast.warn(t('chat.cmd.clear.taskRunning'))
          } else {
            toast.error(t('chat.cmd.clear.failed'), String(e?.message || e))
          }
        }
        break
      }
      case 'compact':
        runCompact()
        break
      case 'context':
        setContextPopupOpen(true)
        break
      case 'new':
        setConvId(null)
        resetBoard()
        break
      case 'rename':
        // Le renommage in-place est géré dans ChatPanel quand une conv est active ;
        // ici on n'arrive que sans conv active → on le signale.
        toast.warn(t('chat.cmd.noConv'))
        break
      case 'resume':
        // La sidebar des conversations est désormais affichée en permanence :
        // « /resume » n'a plus de panneau à ouvrir, l'utilisateur y pioche directement.
        break
    }
  }

  // Fusion REST + live : dédup par clé naturelle (corr_id|kind|ts, garantie idempotente par N7).
  // Les entrées REST couvrent les tours passés ; les entrées live couvrent le tour en cours.
  // Le live a priorité : on filtre d'abord les doublons REST avant de concaténer.
  const mergedTraces = useMemo(() => {
    const liveKeys = new Set(traces.map((t) => `${t.corr_id}|${t.kind}|${t.ts}`))
    const historical = restTraces.filter((t) => !liveKeys.has(`${t.corr_id}|${t.kind}|${t.ts}`))
    return [...historical, ...traces].sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0))
  }, [restTraces, traces])

  // Contexte des widgets du canvas. Quand un salon est ouvert, les widgets opèrent
  // dans le CONTEXTE du salon (events + envois routés vers chan-<id>) — sinon dans
  // celui de la conv privée. Indispensable pour que l'appui feu spawné en salon
  // préremplisse depuis la bonne directive et soumette au bon orchestrateur.
  // Wrapper stable vers `removeWidget` (défini plus bas → TDZ si référencé direct
  // dans le useMemo du ctx). Permet à un widget de se retirer lui-même du canvas
  // (ex. le tableau d'appui feu se referme après envoi des demandes).
  const removeWidgetRef = useRef<(id: string) => void>(() => {})
  const removeWidgetStable = useCallback((id: string) => removeWidgetRef.current(id), [])

  const ctx: WidgetCtx = useMemo(
    () => (channelId
      ? { events: channelConv.events, progress: channelConv.progress, traces: channelConv.traces, connected: channelConv.connected, send: channelConv.send, sendMapPoint: channelConv.sendMapPoint, removeWidget: removeWidgetStable }
      : { events, progress, traces: mergedTraces, connected, send, sendMapPoint, removeWidget: removeWidgetStable }),
    [channelId, channelConv.events, channelConv.progress, channelConv.traces, channelConv.connected, channelConv.send, channelConv.sendMapPoint, events, progress, mergedTraces, connected, send, sendMapPoint, removeWidgetStable],
  )

  // Libellé affichable d'un widget : titre custom s'il existe, sinon le titre du
  // registry (clé i18n traduite, ou chaîne directe pour les widgets sans i18n),
  // sinon l'ID brut en ultime repli. Évite que la taskbar affiche l'instanceId.
  const titleOf = (id: string): string => {
    if (widgetTitles[id]) return widgetTitles[id]
    const spec = specByInstanceId(id)
    if (!spec) return id
    return spec.title.startsWith('widgets.') ? t(spec.title as any) : spec.title
  }

  // --- Gestion des widgets sur le canvas ---
  // Génère un instanceId unique pour permettre les doublons du même type.
  // `pos` (drop) n'est plus posé tel quel : il sert de point de départ à
  // `findFreePosition`, qui le respecte s'il est libre et le décale sinon — un
  // drop ne peut donc plus recouvrir un widget existant ni atterrir hors zone.
  const addWidget = (specId: string, pos?: { x: number; y: number }) => {
    const instanceId = makeInstanceId(specId)
    setPersisted((p) => {
      const { w, h } = defaultSize(specId)
      const existing = Object.values(p.positions)
      const start = pos ?? { x: (chatOpen ? CHAT_W : 0) + 32, y: 32 }
      const at = findFreePosition({ w, h }, existing, start, chatOpen ? CHAT_W : 0)
      return {
        active: [...p.active, instanceId],
        positions: { ...p.positions, [instanceId]: { x: at.x, y: at.y, w, h } },
      }
    })
    setLibraryOpen(false)
    setLibrarySearch('')
    bringToFront(instanceId) // un widget fraîchement posé arrive au premier plan
    // On amène le widget dans la vue s'il atterrit hors-cadre ; s'il est déjà
    // visible, la caméra de l'analyste n'est PAS perturbée (cf. WidgetCanvas).
    setFocusId(instanceId)
  }

  // Redimensionne un widget déjà posé (utilisé par le lecteur de fichier pour
  // s'ajuster à l'aspect de l'image une fois mesurée). Borné pour rester maniable.
  const resizeWidget = (instanceId: string, w: number, h: number) => {
    setPersisted((p) => {
      const cur = p.positions[instanceId]
      if (!cur) return p
      const nw = Math.max(280, Math.min(1400, Math.round(w)))
      const nh = Math.max(200, Math.min(1100, Math.round(h)))
      if (cur.w === nw && cur.h === nh) return p
      return { ...p, positions: { ...p.positions, [instanceId]: { ...cur, w: nw, h: nh } } }
    })
  }

  // Enregistre le canal de resize pour que le ViewFileWidget puisse s'ajuster.
  useEffect(() => {
    useFileViewerStore.getState().setResizer(resizeWidget)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Spawn d'un lecteur de fichier pré-chargé sur une pièce jointe (clic sur le
  // badge 📎). Taille de spawn devinée par l'extension ; pour une image, l'aspect
  // exact est appliqué au chargement (cf. onImageMeasured du widget).
  const spawnFileViewer = (att: Attach) => {
    const instanceId = makeInstanceId('viewfile')
    const ext = (att.filename.split('.').pop() || '').toLowerCase()
    let size = { w: 760, h: 680 } // texte/code/markdown/csv… par défaut
    if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'avif'].includes(ext)) size = { w: 640, h: 500 }
    else if (ext === 'pdf') size = { w: 820, h: 1060 } // portrait A4
    else if (['mp4', 'webm', 'mov', 'mkv', 'avi'].includes(ext)) size = { w: 880, h: 540 }
    else if (['mp3', 'wav', 'ogg', 'flac', 'm4a'].includes(ext)) size = { w: 460, h: 220 }
    useFileViewerStore.getState().setFile(instanceId, { attachmentId: att.id, filename: att.filename })
    const chatW = chatOpen ? CHAT_W : 0
    const start = { x: chatW + 32, y: 32 }
    const at = findFreePosition(size, Object.values(persisted.positions), start, chatW)
    setPersisted((p) => ({
      ...p,
      active: [...p.active, instanceId],
      positions: { ...p.positions, [instanceId]: { x: at.x, y: at.y, w: size.w, h: size.h } },
    }))
    bringToFront(instanceId)
    setFocusId(instanceId)
  }

  // Fait apparaître l'éditeur de rapport dans un emplacement LIBRE, sans jamais
  // déplacer un widget existant (le canvas conserve la position des nœuds montés).
  // On l'ancre À DROITE de la carte si elle est là (vue côte à côte), sinon en haut
  // à droite du chat ; `findFreePosition` garantit l'absence de chevauchement.
  // Calcul sur l'état FRAIS (le `p` de l'updater) → pas de course de timing. Garde
  // synchrone `reportEditorSpawnedRef` contre un double-spawn (les report_chunk
  // arrivent en rafale et peuvent redéclencher l'effet avant le commit de `active`).
  const reportEditorSpawnedRef = useRef(false)
  const spawnReportEditor = () => {
    if (reportEditorSpawnedRef.current) return
    reportEditorSpawnedRef.current = true
    const instanceId = makeInstanceId('texteditor')
    const { w, h } = defaultSize('texteditor')
    const chatW = chatOpen ? CHAT_W : 0
    // État frais via la closure : la carte est committée bien avant le rapport.
    const mapId = persisted.active.find((id) => specByInstanceId(id)?.id === 'map')
    const map = mapId ? persisted.positions[mapId] : undefined
    const start = map ? { x: map.x + map.w + 24, y: map.y } : { x: chatW + 32, y: 32 }
    const at = findFreePosition({ w, h }, Object.values(persisted.positions), start, chatW)
    setPersisted((p) => ({
      active: [...p.active, instanceId],
      positions: { ...p.positions, [instanceId]: { x: at.x, y: at.y, w, h } },
    }))
    bringToFront(instanceId)
    // Cadre l'éditeur + la carte (vue d'ensemble) ; sinon l'éditeur seul.
    if (map) {
      const minX = Math.min(map.x, at.x), minY = Math.min(map.y, at.y)
      const maxX = Math.max(map.x + map.w, at.x + w), maxY = Math.max(map.y + map.h, at.y + h)
      setFocusBounds({ x: minX, y: minY, w: maxX - minX, h: maxY - minY })
    } else {
      setFocusId(instanceId)
    }
  }

  // Réarme le garde quand plus aucun texteditor n'est présent (rapport suivant /
  // nouvelle conversation) : l'éditeur est réutilisé tant qu'il existe.
  useEffect(() => {
    if (!persisted.active.some((id) => specByInstanceId(id)?.id === 'texteditor')) {
      reportEditorSpawnedRef.current = false
    }
  }, [persisted.active])

  const removeWidget = (id: string) => {
    setPersisted((p) => {
      const positions = { ...p.positions }
      delete positions[id]
      return { active: p.active.filter((x) => x !== id), positions }
    })
    // Nettoyage des états annexes pour ne pas fuiter des entrées mortes.
    setZOrder((z) => { if (!(id in z)) return z; const n = { ...z }; delete n[id]; return n })
    setWidgetTitles((tt) => {
      if (!(id in tt)) return tt
      const n = { ...tt }; delete n[id]; persistWidgetTitles(n); return n
    })
    // Stockage par instance : post-it (localStorage) et snapshot d'éditeur (store).
    const specId = specByInstanceId(id)?.id
    if (specId === 'notes') { try { localStorage.removeItem('hemicycle.notes.' + id) } catch { /* quota */ } }
    if (specId === 'texteditor') useTextEditorStore.getState().clearContent(id)
    if (specId === 'viewfile') useFileViewerStore.getState().clearFile(id)
  }
  // Tient la ref à jour pour le wrapper stable consommé par le ctx des widgets.
  removeWidgetRef.current = removeWidget

  const setGeometry = (id: string, partial: Partial<WidgetPos>) => {
    setPersisted((p) => {
      const cur = p.positions[id] || { x: 60, y: 60, ...defaultSize(id) }
      return { ...p, positions: { ...p.positions, [id]: { ...cur, ...partial } } }
    })
  }

  // Minimise un widget : le retire du canvas et le met dans la taskbar.
  const minimizeWidget = (id: string) => {
    const pos = persisted.positions[id] || { x: 60, y: 60, ...defaultSize(id) }
    const spec = specByInstanceId(id)
    setMinimizedWidgets((prev) => [...prev, { id, specId: spec?.id ?? id, title: titleOf(id), savedPos: pos }])
    setPersisted((p) => ({
      active: p.active.filter((x) => x !== id),
      positions: p.positions,
    }))
    if (fullscreenWidgetId === id) setFullscreenWidgetId(null)
  }

  // Restore un widget minimisé vers le canvas. La `savedPos` peut désormais être
  // occupée (un widget posé entre-temps) → on repasse par `findFreePosition` pour
  // éviter une superposition, puis on l'amène dans la vue.
  const restoreWidget = (id: string) => {
    const mw = minimizedWidgets.find((w) => w.id === id)
    if (!mw) return
    setMinimizedWidgets((prev) => prev.filter((w) => w.id !== id))
    setPersisted((p) => {
      const existing = Object.entries(p.positions).filter(([k]) => k !== id).map(([, v]) => v)
      const at = findFreePosition(
        { w: mw.savedPos.w, h: mw.savedPos.h },
        existing,
        { x: mw.savedPos.x, y: mw.savedPos.y },
        chatOpen ? CHAT_W : 0,
      )
      return {
        active: [...p.active, id],
        positions: { ...p.positions, [id]: { ...mw.savedPos, x: at.x, y: at.y } },
      }
    })
    bringToFront(id) // restauré depuis la taskbar → au premier plan
    setFocusId(id)   // amène le widget dans la vue s'il est hors-cadre
  }

  // Supprime définitivement un widget depuis la taskbar.
  const removeMinimizedWidget = (id: string) => {
    setMinimizedWidgets((prev) => prev.filter((w) => w.id !== id))
  }

  // Bascule le fullscreen d'un widget.
  const toggleFullscreen = (id: string) => {
    setFullscreenWidgetId((prev) => (prev === id ? null : id))
  }

  // Remet le tableau à zéro sur TOUTES ses facettes (widgets, géométrie,
  // empilement, minimisés, fullscreen) — sinon un widget minimisé survivrait au
  // reset, fantôme dans la taskbar.
  const clearBoardState = () => {
    setPersisted({ active: [...DEFAULT_ACTIVE], positions: {} })
    setMinimizedWidgets([])
    setZOrder({})
    zCounterRef.current = 1
    setFullscreenWidgetId(null)
  }

  const reset = () => {
    if (!confirm(t('dashboard.resetConfirm'))) return
    clearBoardState()
  }

  // Tous les widgets sont disponibles (doublons permis via instanceIds uniques),
  // filtrés par la barre de recherche de la bibliothèque (match sur le titre traduit).
  const available = useMemo(() => {
    const q = librarySearch.trim().toLowerCase()
    if (!q) return WIDGETS
    return WIDGETS.filter((w) => {
      const title = w.title.startsWith('widgets.') ? t(w.title as any) : w.title
      return title.toLowerCase().includes(q)
    })
  }, [librarySearch, t])

  return (
    <div className="flex h-full">
      {/* Barre latérale persistante : conversations + salons. */}
      <ConversationSidebar />

      {/* Zone canvas : tout l'existant flottant (chat, widgets, taskbar, overlays). */}
      <div className="relative min-w-0 flex-1 overflow-hidden">
      {/* Canvas plein écran : widgets = nœuds libres (pan / zoom / drop). */}
      <WidgetCanvas
        ctx={ctx}
        active={persisted.active.filter((id) => id !== fullscreenWidgetId)}
        positions={persisted.positions}
        onRemove={removeWidget}
        onGeometry={setGeometry}
        onAdd={addWidget}
        onMinimize={minimizeWidget}
        onFullscreen={toggleFullscreen}
        zOrder={zOrder}
        onBringToFront={bringToFront}
        focusId={focusId}
        focusBounds={focusBounds}
        chatWidth={chatOpen ? CHAT_W : 0}
        onFocused={() => { setFocusId(null); setFocusBounds(null) }}
      />

      {persisted.active.length === 0 && (
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-2 text-slate-500 dark:text-slate-400">
          <p className="text-sm">{t('dashboard.emptyBoardTitle')}</p>
          <p className="text-[11px] text-slate-400 dark:text-slate-500">
            {t('dashboard.emptyBoardHint')}
          </p>
        </div>
      )}

      {/* Taskbar : toutes les fenêtres (ouvertes + minimisées), overlay en bas du canvas. */}
      {(() => {
        const taskbarWidgets: TaskbarEntry[] = [
          // Fenêtres actives sur le canvas (ou en fullscreen)
          ...persisted.active.map((id) => ({
            id,
            specId: specByInstanceId(id)?.id ?? id,
            title: titleOf(id),
            status: (fullscreenWidgetId === id ? 'fullscreen' : 'open') as TaskbarEntry['status'],
          })),
          // Fenêtres minimisées
          ...minimizedWidgets.map((w) => ({
            id: w.id,
            specId: w.specId,
            title: titleOf(w.id),
            status: 'minimized' as const,
          })),
        ]
        return (
          <WidgetTaskbar
            widgets={taskbarWidgets}
            onFocus={(id) => { bringToFront(id); setFocusId(id) }}
            onRestore={restoreWidget}
            onRemove={(id) => {
              if (minimizedWidgets.some((w) => w.id === id)) removeMinimizedWidget(id)
              else removeWidget(id)
            }}
            chatWidth={chatOpen ? CHAT_W : 0}
          />
        )
      })()}

      {/* Overlay fullscreen : widget en plein canvas, hors zone chat et panel droit. */}
      {fullscreenWidgetId && (() => {
        const fsSpec = specByInstanceId(fullscreenWidgetId)
        if (!fsSpec) return null
        // taskbarH = h-9 (36 px, bottom-0) — toujours visible désormais.
        const taskbarH = 36
        const rightOffset = 8
        return (
          <div
            className="absolute z-20 overflow-hidden rounded-xl border-[3px] border-navy-700 bg-white dark:bg-ink-800 shadow-pop"
            style={{
              top: 8,
              left: chatOpen ? CHAT_W + 8 : 8,
              right: rightOffset,
              bottom: taskbarH + 8,
            }}
          >
            <WidgetFrame
              onClose={() => { removeWidget(fullscreenWidgetId); setFullscreenWidgetId(null) }}
              onMinimize={() => { minimizeWidget(fullscreenWidgetId); setFullscreenWidgetId(null) }}
              onFullscreen={() => setFullscreenWidgetId(null)}
              isFullscreen
              title={widgetTitles[fullscreenWidgetId] || (fsSpec.title.startsWith('widgets.') ? t(fsSpec.title as any) : fsSpec.title)}
            >
              {fsSpec.render(ctx, fullscreenWidgetId)}
            </WidgetFrame>
          </div>
        )
      })()}

      {/* Chat flottant (repliable). L'historique slide par-dessus. */}
      {chatOpen ? (
        <div className="absolute bottom-4 left-4 top-4 z-30 w-[420px] max-w-[88vw]">
          <div className="relative h-full">
            {channelId ? (
              <ChannelPanel
                channelId={channelId}
                onSpawnWidget={(key) => {
                  // Même logique que la garde spawn_widget de la conv privée : focus si
                  // déjà posé, sinon addWidget. Pont depuis la conv `chan-<id>` du salon.
                  if (!specById(key)) return
                  const existing = persisted.active.find((id) => specByInstanceId(id)?.id === key)
                  if (existing) setFocusId(existing)
                  else addWidget(key)
                }}
              />
            ) : (
            <ChatPanel
              events={events}
              progress={progress}
              traces={mergedTraces}
              thinking={thinking}
              assistantStream={assistantStream}
              connected={connected}
              submit={submitChat}
              pendingSent={optimistic}
              onCancel={cancel}
              onOpenWorkflow={() => setWorkflowPopupOpen(true)}
              onAttachFiles={onAttachFiles}
              attachments={attachments}
              onRemoveAttachment={(id) => setAttachments((prev) => prev.filter((a) => a.id !== id))}
              onOpenAttachment={spawnFileViewer}
              selectedWorkflow={selectedWorkflow}
              onClearWorkflow={() => setSelectedWorkflow(null)}
              onApprovePlan={onApprovePlan}
              onModifyPlan={onModifyPlan}
              onMemoryDecision={sendMemoryDecision}
              contextTokens={contextTokens}
              convTitle={convTitle}
              onCommand={onCommand}
              onRename={handleRename}
              compacting={compacting}
              contextRefs={contextRefs}
              onContextRefsChange={setContextRefs}
              onCollapse={() => setChatOpen(false)}
              overlayOpen={contextPopupOpen || workflowPopupOpen || !!builder || !!fullscreenWidgetId}
            />
            )}
          </div>
        </div>
      ) : (
        <button
          onClick={() => setChatOpen(true)}
          className="absolute bottom-14 left-4 z-30 flex items-center gap-1.5 rounded-full bg-navy-700 px-4 py-2.5 text-sm font-semibold text-white shadow-pop transition hover:bg-navy-600"
          title={t('dashboard.openChat')}
        >
          {t('dashboard.chatBtn')}
        </button>
      )}

      {/* Bouton "+" en haut à droite — ouvre/ferme la bibliothèque de widgets */}
      <button
        onClick={() => setLibraryOpen((v) => !v)}
        className="absolute right-4 top-4 z-20 flex h-10 w-10 items-center justify-center rounded-full bg-navy-700 text-white shadow-pop transition hover:scale-105 hover:bg-navy-600"
        title={t('dashboard.addWidget')}
        aria-label={t('dashboard.addWidget')}
      >
        <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor"
             strokeWidth={2.5} strokeLinecap="round">
          {libraryOpen
            ? <path d="M18 6L6 18M6 6l12 12" />
            : <path d="M12 5v14M5 12h14" />}
        </svg>
      </button>

      {/* Bibliothèque de widgets (drawer droite) — compacte : recherche + grille
          icône/titre. Description et hint « glissez ou cliquez » retirés. */}
      {libraryOpen && (() => {
        const closeLibrary = () => { setLibraryOpen(false); setLibrarySearch('') }
        return (
        <div className="absolute inset-0 z-40 flex">
          <div className="flex-1 bg-navy-900/20 backdrop-blur-sm" onClick={closeLibrary} />
          <aside className="flex w-72 animate-slideInRight flex-col overflow-hidden border-l border-slate-200 dark:border-ink-600 bg-white dark:bg-ink-800 shadow-pop">
            <div className="flex shrink-0 items-center justify-between px-3 pt-3 pb-2">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                {t('dashboard.library')}
              </h2>
              <button onClick={closeLibrary} className="text-sm text-slate-400 dark:text-slate-500 hover:text-navy-700">
                ✕
              </button>
            </div>
            {/* Barre de recherche */}
            <div className="shrink-0 px-3 pb-2">
              <div className="relative">
                <svg viewBox="0 0 24 24" className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
                     fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
                  <circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" />
                </svg>
                <input
                  autoFocus
                  value={librarySearch}
                  onChange={(e) => setLibrarySearch(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Escape') closeLibrary() }}
                  placeholder={t('dashboard.searchWidget')}
                  className="w-full rounded-lg border border-slate-200 dark:border-ink-600 bg-slate-50 dark:bg-ink-700 py-1.5 pl-8 pr-2 text-[12px] text-slate-700 dark:text-slate-100 outline-none placeholder:text-slate-400 focus:border-navy-400 focus:bg-white dark:focus:bg-ink-600"
                />
              </div>
            </div>
            {/* Grille compacte : icône + titre uniquement */}
            <div className="flex-1 overflow-y-auto px-3 pb-3">
              {available.length === 0 ? (
                <p className="px-1 py-3 text-[11px] text-slate-400 dark:text-slate-500">
                  {librarySearch.trim() ? t('dashboard.noWidgetMatch') : t('dashboard.allWidgetsOnBoard')}
                </p>
              ) : (
                <div className="grid grid-cols-2 gap-2">
                  {available.map((w) => {
                    const label = w.title.startsWith('widgets.') ? t(w.title as any) : w.title
                    return (
                      <button
                        key={w.id}
                        draggable
                        onDragStart={(e) => {
                          e.dataTransfer.setData('application/widget', w.id)
                          e.dataTransfer.effectAllowed = 'copy'
                        }}
                        onClick={() => addWidget(w.id)}
                        title={label}
                        className="flex cursor-grab flex-col items-center gap-1.5 rounded-lg border border-slate-200 dark:border-ink-600 bg-slate-50 dark:bg-ink-700 px-2 py-2.5 text-center transition hover:border-navy-300 hover:bg-white dark:hover:bg-ink-600 hover:shadow-card active:cursor-grabbing"
                      >
                        <WidgetIcon specId={w.id} className="h-6 w-6 text-navy-700 dark:text-navy-200" />
                        <span className="w-full truncate text-[11px] font-medium text-slate-700 dark:text-slate-100">{label}</span>
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
            <div className="shrink-0 border-t border-slate-200 dark:border-ink-600 px-3 py-2">
              <button onClick={reset} className="text-[11px] text-slate-400 dark:text-slate-500 hover:text-red-600" title={t('dashboard.clearBoard')}>
                {t('dashboard.resetDashboard')}
              </button>
            </div>
          </aside>
        </div>
        )
      })()}

      {/* Popup context info (/context) — identique au clic sur le badge de tokens :
          barre de progression + bouton Compacter. */}
      {contextPopupOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-navy-900/40 p-4 backdrop-blur-sm"
          onClick={() => setContextPopupOpen(false)}
        >
          <div
            className="card w-full max-w-xs animate-popIn p-4 shadow-pop"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-3">
              <p className="text-sm font-semibold text-navy-700 dark:text-navy-200">
                {t('chat.cmd.context.title')}
              </p>
              <button onClick={() => setContextPopupOpen(false)} className="text-slate-400 hover:text-navy-700 dark:hover:text-navy-200">✕</button>
            </div>
            <ContextPanel
              tokens={contextTokens}
              max={maxTokens}
              onCompact={() => { setContextPopupOpen(false); runCompact() }}
            />
          </div>
        </div>
      )}

      {/* Popup Workflow : liste des workflows pré-définis + accès au constructeur. */}
      {workflowPopupOpen && (
        <WorkflowPopup
          onClose={() => setWorkflowPopupOpen(false)}
          onUse={(wf: Workflow) =>
            setSelectedWorkflow({ id: wf.id, name: wf.name, steps: wf.steps })}
          onCreateNew={() => navigate('/workflows')}
        />
      )}


      {/* Modifier le plan proposé : constructeur en modale. */}
      {builder && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-navy-900/40 p-4 backdrop-blur-sm sm:p-8"
          onClick={() => setBuilder(null)}
        >
          <div
            className="card flex h-[85vh] w-full max-w-6xl animate-popIn flex-col overflow-hidden shadow-pop"
            onClick={(e) => e.stopPropagation()}
          >
            <WorkflowBuilder
              mode={builder.mode}
              initialSteps={builder.initialSteps}
              onClose={() => setBuilder(null)}
              onApply={(steps) => sendPlan(steps)}
            />
          </div>
        </div>
      )}
      </div>
    </div>
  )
}
