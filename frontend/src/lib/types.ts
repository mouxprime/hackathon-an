// Types partagés — miroir des messages Pydantic et des modèles DB côté backend.

export type UIEvent = {
  conv_id: string
  seq: number
  type: 'message' | 'widget' | 'widget_update' | 'status' | 'task_update' | 'spawn_widget'
  payload: any
  ts: string
}

// `kind` discrimine : progress (heartbeat lease) vs traces riches (pyramide).
// message_chunk : synthèse de chat de l'orchestrateur streamée ; draft_chunk : résumé
// d'un sous-agent en cours de rédaction (live, éphémère).
export type ProgressKind = 'progress' | 'thinking' | 'tool_call' | 'tool_result' | 'report_chunk' | 'message_chunk' | 'draft_chunk'

export type Progress = {
  corr_id: string
  conv_id: string
  agent_id: string
  worker_id: string
  pct: number
  note: string
  kind: ProgressKind
  trace_data: any | null
  ts: string
}

// Entrée du journal de trace (depuis Postgres, pour le replay au reconnect).
export type ExecutionTraceEntry = {
  id: number
  corr_id: string
  agent_id: string
  kind: ProgressKind
  data: any
  ts: string
}

export type Conversation = {
  conv_id: string
  user_id: string
  title: string
  fsm_state: string
  version: number
  event_seq: number
  lock_holder: string | null
  lock_expires_at: string | null
  plan: any
  updated_at: string
  context_tokens: number
}

export type Task = {
  corr_id: string
  conv_id: string
  agent_type: string
  plan_step_id: string
  status: string
  worker_id: string | null
  attempt_count: number
  progress_pct: number
  progress_note: string
  instruction: string
  dispatched_at: string
  published_at: string | null
  lease_until: string | null
  completed_at: string | null
  result_payload: any
  error: string | null
}

export type Pod = {
  agent_id: string
  worker_id: string
  status: string
  current_corr: string | null
  ts: string
}

export type Tool = {
  id: number
  agent_id: string
  name: string
  description: string
  input_schema: Record<string, any>
  source: 'native' | 'mcp' | 'a2a'
  enabled: boolean
  paused: boolean
  created_at: string
}

// Résultat renvoyé par POST /api/tools/{id}/test : exécution réelle du tool.
export type ToolTestResult = {
  tool_id: number
  name: string
  ok: boolean
  skipped?: boolean
  args: Record<string, unknown>
  latency_ms: number
  detail: string
}

// Résultat renvoyé par POST /api/agents/{id}/mcp/{server}/{tool}/test.
export type MCPToolTestResult = {
  server_name: string
  tool_name: string
  ok: boolean
  args: Record<string, unknown>
  latency_ms: number
  detail: string
  result?: any
}

// Définition d'un outil MCP retournée par GET /api/agents/{id}/mcp/{server}/tools.
export type MCPToolDef = {
  name: string
  description?: string
  inputSchema?: {
    type?: string
    properties?: Record<string, { type?: string; description?: string; [k: string]: any }>
    required?: string[]
    [k: string]: any
  }
}

export type AgentEnriched = {
  id: string
  discipline: string
  description: string
  when_to_use: string
  cost_hint: string
  latency_p95_s: number
  hard_timeout_s: number
  transport: 'nats' | 'a2a'
  card_url: string | null
  tools: Tool[]
  tools_total: number
  tools_active: number
  mcp_count: number
  status: 'healthy' | 'error' | 'idle'
  pods_live: number
  last_activity: string | null
  tasks_24h: number
}

// Fait de mémoire interne (ltm_memory) exposé par /api/memory.
export type MemoryEntry = {
  id: number
  kind: string
  content: string
  conv_id: string | null
  user_id: string | null
  channel_id: string | null
  created_at: string
}

export type AgentDetail = AgentEnriched & {
  mcp_servers: any[]
  agent_card: Record<string, any> | null   // snapshot servi depuis Postgres
}

// Rapport renvoyé par /api/agents/register et /api/agents/{id}/probe.
export type ProbeReport = {
  base_url: string
  ok: boolean
  agent_card: Record<string, any> | null
  latency_ms: number
  checks: { name: string; status: 'pass' | 'warn' | 'fail'; detail: string; duration_ms: number }[]
  sample_events: any[]
}

export type AgentType = {
  id: string
  discipline: string
  description: string
  when_to_use: string
  tools: string[]
  cost_hint: string
  latency_p95_s: number
  hard_timeout_s: number
}

export type DlqEntry = {
  subject: string
  reason: string
  orig_subject: string
  payload: string
}

export type LlmStats = {
  queue_high?: number
  queue_low?: number
  in_flight?: number
  served_high?: number
  served_low?: number
  slots?: number
  error?: string
}

export type JetstreamStream = {
  name: string
  subjects: string[]
  messages: number
  bytes: number
  first_seq: number
  last_seq: number
  consumer_count: number
  storage: string
  error?: string
}

export type JetstreamConsumer = {
  name: string
  stream: string
  num_pending: number
  num_ack_pending: number
  num_redelivered: number
  num_waiting: number
  delivered_consumer_seq: number | null
  delivered_stream_seq: number | null
  ack_floor_stream_seq: number | null
  filter_subject: string | null
  ack_wait: number | null
  max_deliver: number | null
}

export type JetstreamMessage = {
  seq: number
  subject: string
  ts: string
  payload: any
}

// Statut d'un service de la stack — alimente le panneau du header.
export type ServiceHealth = {
  name: string
  category: 'infra' | 'core' | 'agents'
  status: 'up' | 'down'
  detail?: string
  transport?: 'nats' | 'a2a'   // présent pour les agents (distingue worker interne vs A2A)
  pods?: number                // pods vivants (agents NATS)
}

// Une étape de plan / brique de workflow : un agent + son instruction.
export type PlanStep = {
  plan_step_id?: string
  agent_type: string
  instruction: string
}

// Workflow d'investigation pré-défini, composé sur le canvas.
export type Workflow = {
  id: string
  name: string
  description: string
  user_id: string
  graph: { nodes: any[]; edges: any[] }
  steps: PlanStep[]
  created_at: string
  updated_at: string
}

// Réponse d'upload d'une pièce jointe.
export type Attachment = {
  id: string
  filename: string
  content_type: string
  size_bytes: number
  chars: number
}

// Référence à une conversation passée chargée en contexte via le déclencheur @.
export type ConvRef = {
  conv_id: string
  title: string
}

// Channel = salon de discussion de groupe (type Discord), DISTINCT de l'historique
// de conversation. `kind: 'system'` = salon « hemicycle » (alertes orchestrateur,
// lecture seule) ; `kind: 'staff'` = salon d'état-major G1..G10.
export type Channel = {
  id: string
  name: string
  kind: 'staff' | 'system'
  role: string | null
  description: string
  readonly: boolean
  position: number
}

// Référence vers le message parent d'une RÉPONSE (extrait cité dans la bulle).
export type ChannelReplyTo = { id: number; author_role: string; text: string }
// Origine d'un message TRANSFÉRÉ depuis un autre channel (encart « transféré de »).
export type ChannelTransferFrom = {
  channel_id: string
  channel_name: string
  author_role: string
  text: string
}

// Localisation géoréférencée portée par une alerte (overlay éphémère de symboles
// militaires sur la carte). Miroir du type interne `Loc` de GeoMap.tsx, sans `agent`.
export type GeoLoc = {
  lat: number
  lon: number
  name: string
  note?: string
  sidc?: string
  course?: number
  speed?: number
}

// Emprise géo (polygone) portée par une alerte. Miroir du type interne `Poly`
// de GeoMap.tsx, sans `agent`. Anneaux en [lat, lon].
export type GeoPoly = {
  name: string
  note?: string
  rings: [number, number][][]
}

// Message posté dans un channel — modèle append-only, ordonné par `id`.
// `kind: 'alert'` = message système (orchestrateur dans « hemicycle »).
// `payload.reply_to` / `payload.transfer_from` portent les actions de message.
// Les alertes géolocalisées ajoutent `geo` (overlay éphémère), `severity`,
// `source` et `alert_kind`.
export type ChannelMessage = {
  id: number
  channel_id: string
  author_id: string
  author_role: string
  kind: 'message' | 'alert'
  text: string
  payload: {
    reply_to?: ChannelReplyTo
    transfer_from?: ChannelTransferFrom
    severity?: string
    source?: string
    alert_kind?: string
    geo?: { locations: GeoLoc[]; polygons?: GeoPoly[] }
    [k: string]: any
  }
  created_at: string
}
