// Constructeur de workflow — canvas type Figma / Excalidraw / NiFi (React Flow).
//
// Deux usages, un seul composant :
//   - mode "save"  : composer un workflow et l'**Enregistrer** (page /workflows → + Créer) ;
//   - mode "apply" : **Modifier** le plan proposé par l'orchestrateur et l'**Appliquer**
//                    (envoie le plan édité → plan_submit).
//
// L'analyste **glisse-dépose** des briques d'agents (palette de gauche) sur le canvas
// — un clic les ajoute aussi (secours). Les briques se relient pour définir l'ordre.
// L'exécution étant séquentielle côté orchestrateur, on dérive du graphe une liste
// **ordonnée** d'étapes (chaîne suivie d'arêtes, puis repli sur la position x).
//
// Le composant remplit son conteneur (h-full) : monté inline dans la page
// /workflows (création) ou dans une modale (modification d'un plan).

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  addEdge, Background, Handle, NodeResizer, Position, ReactFlow,
  ReactFlowProvider, useEdgesState, useNodesState, useReactFlow,
  type Connection, type Edge, type Node, type XYPosition,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import { api } from '../lib/api'
import type { AgentEnriched, PlanStep, Workflow } from '../lib/types'
import { agentEmoji } from '../lib/ui'
import { useToast } from '../lib/toast'
import { useThemeStore } from '../lib/store'
import { useT, useI18n } from '../lib/i18n'

// Taille pixel par défaut d'une brique d'agent (redimensionnable ensuite).
const NODE_SIZE = { width: 280, height: 168 }

// --- Brique d'agent : nœud personnalisé redimensionnable (nom + instruction + handles) ---
function AgentNode({ id, data, selected }: { id: string; data: any; selected?: boolean }) {
  const t = useT()
  return (
    <>
      <NodeResizer
        isVisible={selected}
        minWidth={210} minHeight={120}
        lineClassName="!border-navy-300"
        handleClassName="!h-2.5 !w-2.5 !rounded-sm !border-navy-400 !bg-white"
      />
      <div className="flex h-full w-full flex-col rounded-xl border-2 border-navy-200 bg-white px-3 py-2 shadow-card dark:border-navy-500/40 dark:bg-ink-800 dark:shadow-none">
        <Handle type="target" position={Position.Left} className="!h-2.5 !w-2.5 !border-2 !border-white !bg-navy-500" />
        <div className="mb-1 flex items-center justify-between">
          <span className="mono flex items-center gap-1 text-xs font-bold uppercase text-navy-700 dark:text-navy-200">
            <span className="text-sm leading-none">{agentEmoji(data.agent_type)}</span>
            {data.agent_type}
          </span>
          <button
            onClick={() => data.onDelete(id)}
            className="text-[11px] text-slate-400 transition hover:text-red-600 dark:text-slate-500 dark:hover:text-red-400"
            title={t('workflows.builder.removeNode')}
          >
            ✕
          </button>
        </div>
        <textarea
          value={data.instruction || ''}
          onChange={(e) => data.onChange(id, e.target.value)}
          placeholder={t('workflows.builder.instructionPlaceholder')}
          className="nodrag w-full flex-1 resize-none rounded-lg border border-slate-300 bg-white px-1.5 py-1 text-[11px] text-slate-700 placeholder-slate-400 transition focus:border-navy-500 focus:outline-none focus:ring-2 focus:ring-navy-500/15 dark:border-ink-600 dark:bg-ink-700 dark:text-slate-200 dark:placeholder-slate-500 dark:focus:border-navy-400"
        />
        <Handle type="source" position={Position.Right} className="!h-2.5 !w-2.5 !border-2 !border-white !bg-navy-500" />
      </div>
    </>
  )
}

const NODE_TYPES = { agent: AgentNode }

// Dérive la liste ordonnée d'étapes à partir du graphe (chaîne d'arêtes puis repli x).
function orderedSteps(nodes: Node[], edges: Edge[]): PlanStep[] {
  const indeg: Record<string, number> = {}
  const next: Record<string, string> = {}
  nodes.forEach((n) => { indeg[n.id] = 0 })
  edges.forEach((e) => {
    if (indeg[e.target] != null) indeg[e.target]++
    next[e.source] = e.target            // chaîne : on suit la 1re sortie
  })
  const starts = nodes
    .filter((n) => indeg[n.id] === 0)
    .sort((a, b) => a.position.x - b.position.x)
  const visited = new Set<string>()
  const order: string[] = []
  for (const s of starts) {
    let cur: string | undefined = s.id
    while (cur && !visited.has(cur)) {
      visited.add(cur)
      order.push(cur)
      cur = next[cur]
    }
  }
  // Briques non reliées : on les ajoute par position horizontale.
  nodes
    .filter((n) => !visited.has(n.id))
    .sort((a, b) => a.position.x - b.position.x)
    .forEach((n) => order.push(n.id))
  return order
    .map((id) => nodes.find((n) => n.id === id))
    .filter((n): n is Node => !!n)
    .map((n) => ({ agent_type: (n.data as any).agent_type, instruction: (n.data as any).instruction || '' }))
}

type Props = {
  mode: 'save' | 'apply'
  initialSteps?: PlanStep[]
  editWorkflow?: Workflow | null          // mode "save" + édition d'un workflow existant
  onClose: () => void
  onSaved?: () => void
  onApply?: (steps: PlanStep[]) => void
}

// Composant interne : utilise useReactFlow → doit être monté sous ReactFlowProvider.
function BuilderInner({ mode, initialSteps, editWorkflow, onClose, onSaved, onApply }: Props) {
  const t = useT()
  const toast = useToast()
  const { screenToFlowPosition } = useReactFlow()
  const [agents, setAgents] = useState<AgentEnriched[]>([])
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [name, setName] = useState(editWorkflow?.name || '')
  const [saving, setSaving] = useState(false)
  const idRef = useRef(0)
  const editing = !!editWorkflow
  const dark = useThemeStore((s) => s.theme === 'dark')

  const [askMessages, setAskMessages] = useState<{ role: string; text: string }[]>([])
  const [askDraft, setAskDraft] = useState('')
  const [askLoading, setAskLoading] = useState(false)
  // Sidebar agents : flottante, repliable via toggle.
  const [sidebarOpen, setSidebarOpen] = useState(true)

  // Callbacks stables injectés dans chaque brique (édition d'instruction / suppression).
  const updateInstruction = useCallback((id: string, instruction: string) => {
    setNodes((nds) => nds.map((n) => (n.id === id ? { ...n, data: { ...n.data, instruction } } : n)))
  }, [setNodes])
  const deleteNode = useCallback((id: string) => {
    setNodes((nds) => nds.filter((n) => n.id !== id))
    setEdges((eds) => eds.filter((e) => e.source !== id && e.target !== id))
  }, [setNodes, setEdges])

  // (Ré)applique une liste d'étapes au canvas : nœuds en ligne + chaîne d'arêtes.
  const applySteps = useCallback((steps: PlanStep[]) => {
    setNodes(steps.map((st, i) => ({
      id: `n${i}`, type: 'agent', position: { x: 80 + i * 320, y: 140 },
      style: { ...NODE_SIZE },
      data: { agent_type: st.agent_type, instruction: st.instruction || '',
              onChange: updateInstruction, onDelete: deleteNode },
    })))
    setEdges(steps.slice(1).map((_, i) => ({ id: `e${i}`, source: `n${i}`, target: `n${i + 1}`, animated: true })))
    idRef.current = steps.length
  }, [setNodes, setEdges, updateInstruction, deleteNode])

  // Assistant « Demander à Hémicycle » : un tour → (re)génère le flow sur le board via le LLM.
  const askHémicycle = useCallback(async () => {
    const text = askDraft.trim()
    if (!text || askLoading) return
    const nextMsgs = [...askMessages, { role: 'user', text }]
    setAskMessages(nextMsgs)
    setAskDraft('')
    setAskLoading(true)
    try {
      const res = await api.draftWorkflow({
        messages: nextMsgs,
        current_steps: orderedSteps(nodes, edges),
        lang: useI18n.getState().lang,
      })
      if (res.steps?.length) applySteps(res.steps)
      setAskMessages((prev) => [...prev, { role: 'assistant', text: res.reply || t('workflows.builder.askUpdated') }])
    } catch (e: any) {
      setAskMessages((prev) => [...prev, { role: 'assistant', text: t('workflows.builder.askError', { error: e?.message || e }) }])
    } finally {
      setAskLoading(false)
    }
  }, [askDraft, askLoading, askMessages, nodes, edges, applySteps, t])

  // Palette d'agents (Agent Registry).
  useEffect(() => { api.agents().then(setAgents).catch(() => setAgents([])) }, [])

  // Amorçage : édition d'un workflow (depuis son graphe, positions/tailles préservées),
  // sinon plan proposé en mode "apply", sinon canvas vide en création.
  useEffect(() => {
    const graphNodes = editWorkflow?.graph?.nodes
    if (editing && Array.isArray(graphNodes) && graphNodes.length) {
      setNodes(graphNodes.map((gn: any) => ({
        id: gn.id, type: 'agent',
        position: gn.position || { x: 80, y: 140 },
        style: (gn.width && gn.height) ? { width: gn.width, height: gn.height } : { ...NODE_SIZE },
        data: { agent_type: gn.data?.agent_type, instruction: gn.data?.instruction || '',
                onChange: updateInstruction, onDelete: deleteNode },
      })))
      setEdges((editWorkflow!.graph?.edges || []).map((e: any) => ({
        id: e.id, source: e.source, target: e.target, animated: true })))
      // Évite les collisions d'id (nœuds nommés n0, n1, …) lors d'ajouts ultérieurs.
      const maxIdx = graphNodes.reduce((m: number, gn: any) => {
        const v = parseInt(String(gn.id).replace(/\D/g, ''), 10)
        return Number.isFinite(v) ? Math.max(m, v + 1) : m
      }, graphNodes.length)
      idRef.current = maxIdx
      return
    }
    const steps = initialSteps || []
    setNodes(steps.map((st, i) => ({
      id: `n${i}`, type: 'agent', position: { x: 80 + i * 320, y: 140 }, style: { ...NODE_SIZE },
      data: { agent_type: st.agent_type, instruction: st.instruction || '', onChange: updateInstruction, onDelete: deleteNode },
    })))
    setEdges(steps.slice(1).map((_, i) => ({ id: `e${i}`, source: `n${i}`, target: `n${i + 1}`, animated: true })))
    idRef.current = steps.length
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const onConnect = useCallback(
    (params: Connection) => {
      if (params.source === params.target) return     // pas d'auto-lien (boucle sur soi-même)
      setEdges((eds) => addEdge({ ...params, animated: true }, eds))
    },
    [setEdges],
  )

  // Ajoute une brique d'agent à une position donnée (drop) ou en cascade (clic).
  const addAgent = useCallback((agentId: string, position?: XYPosition) => {
    setNodes((nds) => {
      const pos = position ?? { x: 120 + (nds.length % 3) * 60, y: 80 + nds.length * 30 }
      const id = `n${idRef.current++}`
      return [...nds, {
        id, type: 'agent', position: pos, style: { ...NODE_SIZE },
        data: { agent_type: agentId, instruction: '', onChange: updateInstruction, onDelete: deleteNode },
      }]
    })
  }, [setNodes, updateInstruction, deleteNode])

  // Drag & drop : la palette pose l'id de l'agent dans le dataTransfer, le canvas
  // convertit la position écran → coordonnées du graphe et insère la brique.
  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
  }, [])
  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    const agentId = e.dataTransfer.getData('application/agent')
    if (!agentId) return
    const position = screenToFlowPosition({ x: e.clientX, y: e.clientY })
    addAgent(agentId, position)
  }, [screenToFlowPosition, addAgent])

  const nodeTypes = useMemo(() => NODE_TYPES, [])

  const handleSave = async () => {
    const steps = orderedSteps(nodes, edges)
    if (!steps.length) { toast.error(t('workflows.builder.errorNoStep')); return }
    if (!name.trim()) { toast.error(t('workflows.builder.errorNoName')); return }
    setSaving(true)
    try {
      const graph = {
        nodes: nodes.map((n) => {
          const w = (n as any).width ?? (n as any).measured?.width ?? (n.style as any)?.width
          const h = (n as any).height ?? (n as any).measured?.height ?? (n.style as any)?.height
          return {
            id: n.id, type: n.type, position: n.position,
            ...(w && h ? { width: w, height: h } : {}),
            data: { agent_type: (n.data as any).agent_type, instruction: (n.data as any).instruction || '' },
          }
        }),
        edges: edges.map((e) => ({ id: e.id, source: e.source, target: e.target })),
      }
      if (editing) {
        await api.updateWorkflow(editWorkflow!.id, { name: name.trim(), graph, steps })
        toast.success(t('workflows.builder.updated', { name: name.trim() }))
      } else {
        await api.createWorkflow({ name: name.trim(), graph, steps })
        toast.success(t('workflows.builder.saved', { name: name.trim() }))
      }
      onSaved?.()
      onClose()
    } catch (e: any) {
      toast.error(t('workflows.builder.saveError', { error: e?.message || e }))
    } finally {
      setSaving(false)
    }
  }

  const handleApply = () => {
    const steps = orderedSteps(nodes, edges)
    if (!steps.length) { toast.error(t('workflows.builder.errorPlanEmpty')); return }
    onApply?.(steps)
    onClose()
  }

  return (
    <div className="flex h-full flex-col overflow-hidden bg-white dark:bg-ink-800">
      {/* Barre supérieure */}
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 py-2.5 dark:border-ink-600 dark:bg-ink-800">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-bold tracking-tight text-navy-700 dark:text-navy-200">
            {mode === 'save'
              ? (editing ? t('workflows.builder.titleEdit') : t('workflows.builder.titleCreate'))
              : t('workflows.builder.titlePlan')}
          </h2>
          {mode === 'save' && (
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('workflows.builder.namePlaceholder')}
              className="field w-72"
            />
          )}
        </div>
        <div className="flex items-center gap-2">
          <button onClick={onClose} className="btn-ghost">
            {t('workflows.builder.cancel')}
          </button>
          {mode === 'save' ? (
            <button onClick={handleSave} disabled={saving} className="btn-primary">
              {saving ? t('workflows.builder.saving') : (editing ? t('workflows.builder.update') : t('workflows.builder.save'))}
            </button>
          ) : (
            <button onClick={handleApply} className="btn-primary">
              {t('workflows.builder.applyPlan')}
            </button>
          )}
        </div>
      </div>

      {/* Zone principale : canvas + sidebar flottante + barre Ask Hémicycle */}
      <div className="relative flex min-h-0 flex-1">

        {/* Canvas React Flow — prend toute la largeur */}
        <div className="min-w-0 flex-1 bg-cream dark:bg-ink-900" onDragOver={onDragOver} onDrop={onDrop}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            nodeTypes={nodeTypes}
            fitView
            fitViewOptions={{ padding: 0.35, maxZoom: 0.8 }}
            defaultViewport={{ x: 0, y: 0, zoom: 0.7 }}
            minZoom={0.3}
            maxZoom={1.5}
            proOptions={{ hideAttribution: true }}
          >
            <Background color={dark ? '#27365a' : '#e6dcc4'} gap={22} />
          </ReactFlow>
        </div>

        {/* Palette d'agents — flottante (overlay sur le canvas, côté gauche). */}
        <aside
          className={`pointer-events-none absolute left-0 top-0 z-10 flex h-full flex-col transition-all duration-200 ${sidebarOpen ? 'pointer-events-auto w-72' : 'w-0'}`}
        >
          <div className={`flex h-full w-72 flex-col overflow-hidden border-r border-slate-200 bg-slate-50 shadow-lg dark:border-ink-600 dark:bg-ink-900 ${sidebarOpen ? '' : 'invisible'}`}>
            {/* En-tête sidebar */}
            <div className="flex shrink-0 items-center justify-between border-b border-slate-200 px-3 py-2.5 dark:border-ink-600">
              <span className="text-sm font-semibold text-navy-700 dark:text-navy-200">
                {t('workflows.builder.tabAgents')}
              </span>
              <button
                onClick={() => setSidebarOpen(false)}
                className="rounded-md p-1 text-slate-400 transition hover:bg-slate-200 hover:text-slate-700 dark:hover:bg-ink-700 dark:hover:text-slate-200"
                title="Réduire"
              >
                ◀
              </button>
            </div>
            {/* Liste des agents */}
            <div className="flex flex-col gap-2.5 overflow-y-auto p-3">
              {agents.length === 0 && (
                <p className="text-xs text-slate-400 dark:text-slate-500">
                  {t('workflows.builder.noAgents')}
                </p>
              )}
              {agents.map((a) => (
                <button
                  key={a.id}
                  draggable
                  onDragStart={(e) => {
                    e.dataTransfer.setData('application/agent', a.id)
                    e.dataTransfer.effectAllowed = 'move'
                  }}
                  onClick={() => addAgent(a.id)}
                  className="flex cursor-grab items-start gap-2.5 rounded-xl border border-slate-200 bg-white p-2.5 text-left shadow-xs transition hover:border-navy-300 hover:shadow-card active:cursor-grabbing dark:border-ink-600 dark:bg-ink-800 dark:shadow-none dark:hover:border-navy-500"
                >
                  <span className="text-xl leading-none">{agentEmoji(a.discipline)}</span>
                  <span className="min-w-0">
                    <span className="mono block text-sm font-bold uppercase text-navy-700 dark:text-navy-200">{a.id}</span>
                    <span className="mt-0.5 line-clamp-2 block text-xs text-slate-500 dark:text-slate-400">{a.description}</span>
                  </span>
                </button>
              ))}
              <p className="mt-1 text-xs leading-snug text-slate-400 dark:text-slate-500">
                {t('workflows.builder.agentsHint')}
              </p>
            </div>
          </div>
        </aside>

        {/* Bouton toggle pour ouvrir la sidebar (visible uniquement quand fermée). */}
        {!sidebarOpen && (
          <button
            onClick={() => setSidebarOpen(true)}
            className="absolute left-2 top-3 z-20 flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-navy-700 shadow-card transition hover:border-navy-300 hover:bg-slate-50 dark:border-ink-600 dark:bg-ink-800 dark:text-navy-200 dark:hover:border-navy-500 dark:hover:bg-ink-700"
            title="Ouvrir la palette d'agents"
          >
            ▶ {t('workflows.builder.tabAgents')}
          </button>
        )}

        {/* Ask Hémicycle — barre flottante en bas, centrée sur le canvas. */}
        <div className="absolute bottom-5 left-1/2 z-20 w-[560px] max-w-[calc(100%-5rem)] -translate-x-1/2">
          {/* Historique de conversation (visible si des messages existent). */}
          {askMessages.length > 0 && (
            <div className="mb-2 max-h-52 space-y-2 overflow-y-auto rounded-2xl border border-slate-200 bg-white/95 p-3 shadow-xl backdrop-blur-sm dark:border-ink-600 dark:bg-ink-900/95">
              {askMessages.map((m, i) => (
                <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  <div className={`max-w-[88%] rounded-xl px-3 py-1.5 text-sm leading-relaxed ${
                    m.role === 'user'
                      ? 'bg-navy-700 text-white'
                      : 'border border-slate-200 bg-white text-slate-700 dark:border-ink-600 dark:bg-ink-800 dark:text-slate-300'
                  }`}>
                    {m.text}
                  </div>
                </div>
              ))}
              {askLoading && (
                <p className="text-xs italic text-slate-400 dark:text-slate-500">
                  {t('workflows.builder.askLoading')}
                </p>
              )}
            </div>
          )}
          {/* Barre d'input Ask Hémicycle */}
          <div className="flex items-center gap-2.5 rounded-2xl border border-slate-300 bg-white/95 px-4 py-2.5 shadow-xl backdrop-blur-sm dark:border-ink-600 dark:bg-ink-900/95">
            <span className="text-base">🧠</span>
            <input
              value={askDraft}
              onChange={(e) => setAskDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') askHémicycle() }}
              placeholder={t('workflows.builder.askPlaceholder')}
              className="flex-1 bg-transparent text-sm text-slate-700 placeholder-slate-400 focus:outline-none dark:text-slate-200 dark:placeholder-slate-500"
            />
            <button
              onClick={askHémicycle}
              disabled={!askDraft.trim() || askLoading}
              className="btn-primary px-3 py-1 text-sm disabled:opacity-40"
            >
              →
            </button>
          </div>
        </div>

      </div>
    </div>
  )
}

// Wrapper : fournit le contexte React Flow (requis par useReactFlow).
export function WorkflowBuilder(props: Props) {
  return (
    <ReactFlowProvider>
      <BuilderInner {...props} />
    </ReactFlowProvider>
  )
}
