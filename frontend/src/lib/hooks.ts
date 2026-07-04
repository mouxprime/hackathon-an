// Hooks React : polling REST périodique + connexion WebSocket par conversation.

import { useEffect, useRef, useState } from 'react'
import { api, WS_BASE } from './api'
import { useI18n } from './i18n'
import { CURRENT_USER, authorRoleFor, useContextStore, useReportStreamStore } from './store'
import type { ChannelMessage, ConvRef, PlanStep, Progress, UIEvent } from './types'

// ── IA de confiance : auto-approbation du plan ──────────────────────────────
// true  → dès qu'un plan est proposé (widget plan_proposal non décidé), le
//         frontend envoie automatiquement le même `plan_submit` que le clic sur
//         « Approuver ». Le plan reste AFFICHÉ dans le fil (parcours de
//         délégation visible), mais sans gate humain.
// false → réactive le gate humain (boutons Approuver / Modifier) en 1 flag.
export const AUTO_APPROVE_PLAN = true

// Options d'un message utilisateur : pièces jointes, workflow pré-sélectionné,
// et bascules thinking (raisonnement live) / rapport (synthèse rédigée).
export type SendOpts = {
  attachments?: { id: string; filename: string }[]
  workflowSteps?: PlanStep[]
  thinking?: boolean
  forceReport?: boolean
  // Conversations référencées via @ — avec leurs événements déjà fetchés.
  contextRefs?: (ConvRef & { events: UIEvent[] })[]
}

// Flux de raisonnement « thinking » accumulé en direct, indexé par stream_id.
export type ThinkingStream = { text: string; done: boolean }

// Rappelle `fn` toutes les `intervalMs` ms et expose le dernier résultat.
export function usePolling<T>(fn: () => Promise<T>, intervalMs: number, deps: any[] = []): T | null {
  const [data, setData] = useState<T | null>(null)
  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const d = await fn()
        if (alive) setData(d)
      } catch {
        /* gateway indisponible : on retentera au prochain tick */
      }
    }
    tick()
    const id = setInterval(tick, intervalMs)
    return () => {
      alive = false
      clearInterval(id)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return data
}

// Connexion WebSocket d'une conversation. Trois flux distincts côté frontend :
// - `events`  : UIEvent durables (ordonnés par seq, dédupliqués au reconnect) ;
// - `progress` : derniers Progress reçus par corr_id (overlay live des cards de tâche) ;
// - `traces`  : append-only des Progress avec kind != "progress" (alimente la pyramide).
//
// Reconnexion automatique : backoff exponentiel (500 ms → 30 s) avec ±20 % de
// jitter ; au-delà de MAX_ATTEMPTS tentatives consécutives la socket est déclarée
// en erreur définitive (`wsError = true`). Les messages `user_input` qui ne peuvent
// pas être envoyés (socket non OPEN) sont mis en file (`sendQueueRef`) et renvoyés
// dès que la socket est de nouveau OPEN.
export function useConversation(convId: string | null) {
  const [events, setEvents] = useState<UIEvent[]>([])
  const [progress, setProgress] = useState<Record<string, Progress>>({})
  const [traces, setTraces] = useState<Progress[]>([])
  // Raisonnement « thinking » streamé par l'orchestrateur, accumulé par stream_id.
  // Reçoit AUSSI les `draft_chunk` (résumés d'agents en cours de rédaction), clés `draft-{corr_id}`.
  const [thinking, setThinking] = useState<Record<string, ThinkingStream>>({})
  // Synthèses de chat de l'orchestrateur streamées (kind=message_chunk), par stream_id.
  const [assistantStream, setAssistantStream] = useState<Record<string, ThinkingStream>>({})
  const [connected, setConnected] = useState(false)
  // Erreur définitive : MAX_ATTEMPTS reconnexions échouées sur la conv courante.
  const [wsError, setWsError] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  // File des payloads `user_input` sérialisés en attente d'une socket OPEN.
  const sendQueueRef = useRef<string[]>([])
  // Plans déjà auto-approuvés (par widget_id) — évite un double plan_submit
  // au replay des events (reconnexion WS) ou sur un widget_update du même plan.
  const autoApprovedRef = useRef<Set<string>>(new Set())

  useEffect(() => {
    // Vidage systématique : passer d'une conv à null doit aussi effacer
    // l'historique d'events affiché (sinon la conv précédente persiste à
    // l'écran après un « + Nouvelle investigation »).
    setEvents([])
    setProgress({})
    setTraces([])
    setThinking({})
    setAssistantStream({})
    setConnected(false)
    setWsError(false)
    sendQueueRef.current = []
    autoApprovedRef.current = new Set()
    if (!convId) return

    let alive = true
    let attempts = 0
    let reconnectTimer: number | null = null
    const MAX_ATTEMPTS = 5
    const BASE_MS = 500
    const CAP_MS = 30_000

    const connect = () => {
      const ws = new WebSocket(`${WS_BASE}/ws/${convId}`)
      wsRef.current = ws

      ws.onopen = () => {
        if (!alive) { ws.close(); return }
        attempts = 0  // réinitialise le compteur de tentatives
        setConnected(true)
        // Flush des messages mis en file pendant la déconnexion.
        const queued = sendQueueRef.current.splice(0)
        for (const payload of queued) ws.send(payload)
      }

      ws.onclose = () => {
        if (!alive) return
        setConnected(false)
        // Backoff exponentiel borné (500 ms → 30 s) avec ±20 % de jitter.
        if (attempts >= MAX_ATTEMPTS) {
          setWsError(true)
          return
        }
        const delay = Math.min(CAP_MS, BASE_MS * Math.pow(2, attempts)) * (0.8 + 0.4 * Math.random())
        attempts++
        reconnectTimer = window.setTimeout(connect, delay)
      }

      ws.onmessage = (e) => {
        const { kind, data } = JSON.parse(e.data)
        if (kind === 'event') {
          setEvents((prev) => {
            if (prev.some((x) => x.seq === data.seq)) return prev // dédup par seq
            return [...prev, data].sort((a, b) => a.seq - b.seq)
          })
          // Auto-approbation du plan (AUTO_APPROVE_PLAN) : à réception d'un
          // widget plan_proposal NON décidé, on envoie automatiquement le même
          // `plan_submit` que le clic sur « Approuver » (mêmes champs — cf.
          // sendPlan). Garde par widget_id : un même plan n'est approuvé qu'une
          // fois, y compris au replay des events après reconnexion.
          if (
            AUTO_APPROVE_PLAN &&
            data.type === 'widget' &&
            data.payload?.widget_type === 'plan_proposal' &&
            !data.payload?.data?.decided &&
            !autoApprovedRef.current.has(data.payload?.widget_id)
          ) {
            autoApprovedRef.current.add(data.payload?.widget_id)
            const steps: PlanStep[] = data.payload?.data?.steps || []
            if (ws.readyState === WebSocket.OPEN) {
              ws.send(JSON.stringify({ type: 'plan_submit', steps, lang: useI18n.getState().lang }))
            }
          }
          // Message durable canonique porteur d'un stream_id : il REMPLACE le bubble
          // streamé éphémère. On purge le buffer live correspondant, sinon il survit
          // dans assistantStream et se réaffiche au tour suivant (réponse en double).
          if (data.type === 'message' && data.payload?.stream_id) {
            const sid: string = data.payload.stream_id
            setAssistantStream((prev) => {
              if (!(sid in prev)) return prev
              const next = { ...prev }
              delete next[sid]
              return next
            })
          }
        } else if (kind === 'progress') {
          const prog: Progress = { ...data, kind: data.kind || 'progress' }
          if (prog.kind === 'report_chunk' && prog.trace_data?.stream_id) {
            // Streaming du rapport : on accumule le texte COMPLET par stream_id dans
            // le store dédié (lu par le ReportWidget du chat ET par DashboardView qui
            // le reflète dans le texteditor). On ne pousse plus de delta directement
            // à l'éditeur ici : il pouvait ne pas encore être monté (course) et les
            // tokens étaient perdus. La réconciliation par texte complet (DashboardView
            // → setReportText) est insensible au timing de montage.
            const sid: string = prog.trace_data.stream_id
            const delta: string = prog.trace_data.text || ''
            const done: boolean = !!prog.trace_data.done
            useReportStreamStore.getState().push(sid, delta, done)
          } else if ((prog.kind === 'thinking' || prog.kind === 'draft_chunk') && prog.trace_data?.stream_id) {
            // Raisonnement streamé (orchestrateur) OU résumé d'agent en cours de
            // rédaction (draft_chunk, clé `draft-{corr_id}`) : on concatène par stream_id
            // dans la même map plutôt que de polluer la pyramide de traces.
            const sid: string = prog.trace_data.stream_id
            setThinking((prev) => {
              const cur = prev[sid] || { text: '', done: false }
              return {
                ...prev,
                [sid]: {
                  text: cur.text + (prog.trace_data?.text || ''),
                  done: cur.done || !!prog.trace_data?.done,
                },
              }
            })
          } else if (prog.kind === 'message_chunk' && prog.trace_data?.stream_id) {
            // Synthèse de chat de l'orchestrateur streamée : accumulée par stream_id,
            // rendue comme bubble assistant live (remplacée par le message durable).
            const sid: string = prog.trace_data.stream_id
            setAssistantStream((prev) => {
              const cur = prev[sid] || { text: '', done: false }
              return {
                ...prev,
                [sid]: {
                  text: cur.text + (prog.trace_data?.text || ''),
                  done: cur.done || !!prog.trace_data?.done,
                },
              }
            })
          } else if (prog.kind === 'progress') {
            // Heartbeat lease : on garde uniquement le dernier par corr_id.
            setProgress((prev) => ({ ...prev, [prog.corr_id]: prog }))
          } else {
            // Trace riche (thinking/tool d'un sous-agent) : append-only pour la pyramide.
            setTraces((prev) => [...prev, prog])
          }
        }
      }
    }

    connect()

    return () => {
      alive = false
      if (reconnectTimer !== null) clearTimeout(reconnectTimer)
      wsRef.current?.close()
      wsRef.current = null
      sendQueueRef.current = []
    }
  }, [convId])

  // Envoie un message sur la socket uniquement si elle est OPEN. Protège les
  // actions interactives (sendPlan, cancel, compact…) des DOMException
  // (InvalidStateError) qui surviennent si la socket est CLOSING ou CLOSED.
  // Pour ces actions, un drop silencieux est préférable à une exception non gérée :
  // l'UI est déjà de facto dans un état incohérent si on n'est pas connecté.
  const wsSend = (msg: object) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg))
    }
  }

  // Publie une entrée utilisateur sur le bus (via le gateway). Peut porter des
  // pièces jointes, un workflow pré-sélectionné, des contextes référencés (@)
  // et la limite de tokens configurée dans SettingsView.
  // Si la socket n'est pas OPEN (déconnexion temporaire), le payload est mis en
  // file et renvoyé automatiquement dès la prochaine reconnexion.
  const send = (text: string, opts?: SendOpts) => {
    // Réinitialise le raisonnement de planification du tour précédent : le
    // stream_id "planning" est réutilisé d'un tour à l'autre, sans reset le
    // bandeau de préparation ré-afficherait le thinking cumulé des tours passés.
    setThinking((prev) => {
      if (!prev['planning']) return prev
      const next = { ...prev }
      delete next['planning']
      return next
    })
    const { maxTokens } = useContextStore.getState()
    const payload = JSON.stringify({
      type: 'user_input', text,
      lang: useI18n.getState().lang,
      attachments: opts?.attachments,
      workflow_steps: opts?.workflowSteps,
      thinking: opts?.thinking || undefined,
      force_report: opts?.forceReport || undefined,
      context_refs: opts?.contextRefs?.length ? opts.contextRefs : undefined,
      max_tokens: maxTokens ?? undefined,
    })
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(payload)
    } else {
      // Socket non OPEN : mise en file ; sera renvoyé à la prochaine reconnexion.
      sendQueueRef.current.push(payload)
    }
  }

  // Décision de l'analyste sur le plan proposé (Approuver tel quel / Modifié).
  const sendPlan = (steps: PlanStep[]) => {
    wsSend({ type: 'plan_submit', steps, lang: useI18n.getState().lang })
  }

  // Demande d'annulation (double-Esc UI) — le gateway publie un UserInput
  // intent=cancel et l'orchestrateur tombstone les tâches en cours.
  const cancel = () => {
    wsSend({ type: 'cancel', lang: useI18n.getState().lang })
  }

  // Demande de compaction — résume l'historique de la conversation via la FSM.
  const compact = () => {
    wsSend({ type: 'compact', lang: useI18n.getState().lang })
  }

  // Décision de l'analyste sur une ligne d'un widget memory_proposal (Valider / Rejeter).
  // L'orchestrateur (h_memory_decision) crée la LtmMemory pour `confirm` puis patche le
  // widget via `widget_update` → l'état décidé survit au refresh / replay du conv.
  const sendMemoryDecision = (
    widgetId: string,
    decisions: { index: number; action: 'confirm' | 'reject'; kind?: string; content?: string }[],
  ) => {
    wsSend({ type: 'memory_decision', widget_id: widgetId, decisions, lang: useI18n.getState().lang })
  }

  // Point posé sur la carte (mode placement) : le gateway le reconvertit
  // en user_input → reprise de la tâche en attente avec la coordonnée.
  const sendMapPoint = (lat: number, lon: number, fieldLabel?: string, requestId?: string) => {
    wsSend({
      type: 'map_point_submit',
      lat, lon,
      field_label: fieldLabel,
      request_id: requestId,
      lang: useI18n.getState().lang,
    })
  }

  return { events, progress, traces, thinking, assistantStream, connected, wsError, send, sendPlan, cancel, compact, sendMemoryDecision, sendMapPoint }
}

// Connexion WebSocket d'un channel (salon de groupe). Plus simple que `useConversation` :
// l'historique est chargé une fois par REST (pas de replay JetStream), puis le WS ne
// pousse que les NOUVEAUX messages. On déduplique par `id` (un même message peut arriver
// par l'historique REST et par le bus si le timing se chevauche).
export function useChannel(channelId: string | null) {
  const [messages, setMessages] = useState<ChannelMessage[]>([])
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    setMessages([])
    setConnected(false)
    if (!channelId) return
    let alive = true

    // 1) Historique initial (Postgres, ordonné par id) avant d'ouvrir le live.
    api.channelMessages(channelId)
      .then((hist) => { if (alive) setMessages(hist) })
      .catch(() => { /* gateway indisponible : le WS rattrapera les nouveaux messages */ })

    // 2) Live : core NATS via le gateway, append + dédup par id.
    const ws = new WebSocket(`${WS_BASE}/ws/channel/${channelId}`)
    wsRef.current = ws
    ws.onopen = () => { if (alive) setConnected(true) }
    ws.onclose = () => { if (alive) setConnected(false) }
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data) as { kind: string; data?: ChannelMessage; id?: number }
      if (msg.kind === 'delete') {
        // Suppression diffusée à tous les clients connectés → on retire par id.
        setMessages((prev) => prev.filter((m) => m.id !== msg.id))
        return
      }
      if (msg.kind !== 'message' || !msg.data) return
      const data = msg.data
      setMessages((prev) => (
        prev.some((m) => m.id === data.id) ? prev : [...prev, data]
      ))
    }
    return () => { alive = false; ws.close() }
  }, [channelId])

  // Poste un message dans le channel. L'écho revient par le bus (onmessage),
  // donc on n'ajoute rien en optimiste ici — l'id fait foi pour la dédup.
  // Un `payload` (reply_to / transfer_from) passe par le REST (le WS ne porte
  // que text/auteur) ; le message revient quand même par le bus → même dédup.
  const post = (text: string, payload?: any) => {
    if (payload && Object.keys(payload).length > 0) {
      api.postChannelMessage(channelId!, {
        text,
        author_id: CURRENT_USER.id,
        author_role: authorRoleFor(channelId),
        payload,
      }).catch(() => { /* gateway indisponible */ })
      return
    }
    wsRef.current?.send(JSON.stringify({
      type: 'post', text,
      author_id: CURRENT_USER.id,
      author_role: authorRoleFor(channelId),
    }))
  }

  // Supprime un message (sans confirmation). Retrait optimiste immédiat ; les
  // autres clients reçoivent l'enveloppe `delete` par le bus.
  const remove = (msgId: number) => {
    if (!channelId) return
    setMessages((prev) => prev.filter((m) => m.id !== msgId))
    api.deleteChannelMessage(channelId, msgId).catch(() => { /* déjà parti / réseau */ })
  }

  return { messages, connected, post, remove }
}
