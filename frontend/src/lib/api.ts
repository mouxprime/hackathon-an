// Client de l'API REST du gateway + base URL du WebSocket.
// Le frontend tourne dans le navigateur de l'hôte → il joint le gateway sur localhost.

import type {
  AgentDetail, AgentEnriched, AgentType, Attachment, Channel, ChannelMessage, Conversation, DlqEntry, ExecutionTraceEntry,
  JetstreamConsumer, JetstreamMessage,
  JetstreamStream, LlmStats, MCPToolDef, MCPToolTestResult, MemoryEntry, PlanStep, ProbeReport, ServiceHealth,
  Task, Tool, ToolTestResult, UIEvent,
  Workflow,
} from './types'

const env = (import.meta as any).env || {}

// Front et API partagent la même origine : Vite proxifie `/api` et `/ws` vers le
// gateway (cf. vite.config.ts). Quand l'app est mountée sous un préfixe (ex:
// "/hemicycle/" derrière APISIX), Vite expose ce préfixe via `import.meta.env.BASE_URL`
// — on le retire du slash final pour le concaténer aux chemins `/api/...` et `/ws/...`.
const BASE: string = (env.BASE_URL || '/').replace(/\/$/, '')

// VITE_GATEWAY_HTTP override l'auto-détection (utile en standalone / port-forward).
const HTTP_BASE: string = env.VITE_GATEWAY_HTTP ?? BASE

function deriveWsBase(): string {
  if (env.VITE_GATEWAY_WS) return env.VITE_GATEWAY_WS
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${window.location.host}${BASE}`
}

export const WS_BASE: string = deriveWsBase()

// Effectue un GET JSON sur le gateway.
async function get<T>(path: string): Promise<T> {
  const resp = await fetch(`${HTTP_BASE}${path}`)
  if (!resp.ok) throw new Error(`${path} → ${resp.status}`)
  return resp.json()
}

async function send<T>(method: string, path: string, body?: any): Promise<T> {
  const resp = await fetch(`${HTTP_BASE}${path}`, {
    method, headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!resp.ok) throw new Error(`${method} ${path} → ${resp.status}`)
  return resp.json()
}

export const api = {
  conversations: () => get<Conversation[]>('/api/conversations'),
  conversation: (id: string) => get<Conversation>(`/api/conversations/${id}`),
  deleteConversation: (id: string) => send<{ deleted: string }>('DELETE', `/api/conversations/${id}`),
  // Renommage d'une conversation — PATCH hors FSM (titre = métadonnée).
  renameConversation: (id: string, title: string) =>
    send<Conversation>('PATCH', `/api/conversations/${id}`, { title }),
  tasks: (convId?: string) =>
    get<Task[]>(`/api/tasks${convId ? `?conv_id=${encodeURIComponent(convId)}` : ''}`),
  events: (id: string) => get<UIEvent[]>(`/api/events/${id}`),
  executionTrace: (id: string) => get<ExecutionTraceEntry[]>(`/api/execution-trace/${id}`),

  // Channels (salons de groupe) — distincts de l'historique de conversation.
  channels: () => get<Channel[]>('/api/channels'),
  channelMessages: (id: string, before?: number) =>
    get<ChannelMessage[]>(`/api/channels/${id}/messages${before ? `?before=${before}` : ''}`),
  // Post REST (reply / transfer) — porte un kind + payload (le post simple passe
  // par le WS du channel). Renvoie le message créé ou {error:'duplicate'}.
  postChannelMessage: (
    id: string,
    body: { text: string; author_id?: string; author_role?: string; kind?: string; payload?: any },
  ) => send<ChannelMessage>('POST', `/api/channels/${id}/messages`, body),
  deleteChannelMessage: (id: string, msgId: number) =>
    send<{ deleted: number }>('DELETE', `/api/channels/${id}/messages/${msgId}`),
  // Édition du prompt système (description) d'un channel d'état-major.
  updateChannel: (id: string, body: { description: string }) =>
    send<Channel>('PATCH', `/api/channels/${id}`, body),

  agentTypes: () => get<AgentType[]>('/api/agent-types'),
  agents: () => get<AgentEnriched[]>('/api/agents'),
  agent: (id: string) => get<AgentDetail>(`/api/agents/${id}`),
  createTool: (agentId: string, payload: Partial<Tool>) =>
    send<Tool>('POST', `/api/agents/${agentId}/tools`, payload),
  patchTool: (id: number, payload: { enabled?: boolean; paused?: boolean; description?: string }) =>
    send<Tool>('PATCH', `/api/tools/${id}`, payload),
  deleteTool: (id: number) => send<any>('DELETE', `/api/tools/${id}`),
  testTool: (id: number) => send<ToolTestResult>('POST', `/api/tools/${id}/test`, {}),
  testMcpTool: (agentId: string, serverName: string, toolName: string, args: Record<string, unknown>) =>
    send<MCPToolTestResult>('POST', `/api/agents/${agentId}/mcp/${serverName}/${toolName}/test`, { args }),
  listMcpTools: (agentId: string, serverName: string) =>
    get<{ server_name: string; tools: MCPToolDef[] }>(`/api/agents/${agentId}/mcp/${serverName}/tools`),

  pods: () => get<any[]>('/api/pods'),
  dlq: () => get<DlqEntry[]>('/api/dlq'),
  bus: () => get<UIEvent[]>('/api/bus'),
  llm: () => get<LlmStats>('/api/llm'),
  healthServices: () => get<ServiceHealth[]>('/api/health/services'),

  streams: () => get<JetstreamStream[]>('/api/jetstream/streams'),
  consumers: (stream: string) => get<JetstreamConsumer[]>(
    `/api/jetstream/streams/${stream}/consumers`),
  streamMessages: (stream: string, limit: number = 20) =>
    get<JetstreamMessage[]>(`/api/jetstream/streams/${stream}/messages?limit=${limit}`),

  // Workflows pré-définis (popup Workflow + canvas React Flow).
  workflows: () => get<Workflow[]>('/api/workflows'),
  workflow: (id: string) => get<Workflow>(`/api/workflows/${id}`),
  createWorkflow: (payload: { name: string; description?: string; graph?: any; steps: PlanStep[] }) =>
    send<Workflow>('POST', '/api/workflows', payload),
  updateWorkflow: (id: string, payload: { name?: string; description?: string; graph?: any; steps?: PlanStep[] }) =>
    send<Workflow>('PATCH', `/api/workflows/${id}`, payload),
  deleteWorkflow: (id: string) => send<{ deleted: string }>('DELETE', `/api/workflows/${id}`),
  // Assistant « Demander à Hémicycle » : génère/amende un workflow depuis du langage naturel.
  draftWorkflow: (payload: { messages: { role: string; text: string }[]; current_steps: PlanStep[]; lang?: string }) =>
    send<{ steps: PlanStep[]; reply: string }>('POST', '/api/workflows/draft', payload),

  // Mémoire interne (ltm_memory) : voir / ajouter / supprimer.
  // `channelId` fourni → mémoire d'un salon (pop-up réglages) ; sinon mémoire globale.
  memory: (channelId?: string) =>
    get<MemoryEntry[]>(`/api/memory${channelId ? `?channel_id=${encodeURIComponent(channelId)}` : ''}`),
  createMemory: (payload: { content: string; kind?: string; user_id?: string; channel_id?: string }) =>
    send<MemoryEntry>('POST', '/api/memory', payload),
  deleteMemory: (id: number) => send<{ deleted: number }>('DELETE', `/api/memory/${id}`),

  // Pièce jointe : upload multipart → renvoie l'id + métadonnées.
  uploadAttachment: async (file: File, convId?: string): Promise<Attachment> => {
    const form = new FormData()
    form.append('file', file)
    const qs = convId ? `?conv_id=${encodeURIComponent(convId)}` : ''
    const resp = await fetch(`${HTTP_BASE}/api/attachments${qs}`, { method: 'POST', body: form })
    if (!resp.ok) throw new Error(`upload → ${resp.status}`)
    return resp.json()
  },

  // URL des octets ORIGINAUX d'une pièce jointe (lecteur de fichier).
  attachmentContentUrl: (id: string): string =>
    `${HTTP_BASE}/api/attachments/${encodeURIComponent(id)}/content`,

  // Enrôlement A2A — point d'entrée unique (cf. /api/agents/register).
  registerAgent: (payload: { name: string; card_url: string }) =>
    send<AgentDetail & { probe: ProbeReport }>('POST', '/api/agents/register', payload),
  probeAgent: (id: string) => send<ProbeReport>('POST', `/api/agents/${id}/probe`, {}),
  invokeAgent: (id: string, text: string, contextId?: string) =>
    send<{ events_count: number; events: any[]; result: any; reply: string }>(
      'POST', `/api/agents/${id}/invoke`, { text, ...(contextId ? { context_id: contextId } : {}) }),
  deleteAgent: (id: string) => send<{ deleted: string }>('DELETE', `/api/agents/${id}`),

  // Traduction live via m2m100 GPU (proxy gateway → m2m100-translation).
  // `source` = trigramme ISO 639-2B (ex. "eng", "fra") ou "" pour auto-detect.
  translate: (text: string, target: string, source = '') =>
    send<{ translated: string; detected_source: string }>(
      'POST', '/api/translate', { text, target, source },
    ),

  // Voix — STT : envoie l'audio (micro ou fichier uploadé) au service
  // transcription, renvoie le texte. `filename` porte l'extension réelle du
  // fichier (mp3/wav/m4a…) pour que le backend détecte le format ; défaut =
  // 'voice.webm' pour la boucle micro live qui n'a pas de nom de fichier.
  transcribe: async (audio: Blob, language = '', filename = 'voice.webm'): Promise<{ text: string; language: string }> => {
    const form = new FormData()
    form.append('audio', audio, filename)
    form.append('language', language)
    const resp = await fetch(`${HTTP_BASE}/api/voice/transcribe`, { method: 'POST', body: form })
    if (!resp.ok) throw new Error(`transcribe → ${resp.status}`)
    return resp.json()
  },

  // Voix — TTS : synthétise le texte (MeloTTS), renvoie le WAV à jouer.
  speak: async (text: string, language = 'FR', speed = 1.0): Promise<Blob> => {
    const resp = await fetch(`${HTTP_BASE}/api/voice/speak`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, language, speed }),
    })
    if (!resp.ok) throw new Error(`speak → ${resp.status}`)
    return resp.blob()
  },
}
