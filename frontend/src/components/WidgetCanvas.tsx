// Canvas de widgets du dashboard — même moteur que le builder de workflow (React Flow).
//
// Chaque widget actif = un nœud librement positionnable et redimensionnable sur un
// canvas pannable/zoomable (fond crème). On dépose un widget depuis la bibliothèque
// (drag & drop) ou via le bouton « + ». Les positions/tailles sont persistées par le
// parent (DashboardView → localStorage). Le contenu des widgets reste interactif :
// le drag du nœud ne part que de la poignée `.widget-drag-handle` (en-tête WidgetFrame),
// et `nowheel` laisse le scroll interne fonctionner sans zoomer le canvas.

import { createContext, useCallback, useContext, useEffect, useMemo, useRef } from 'react'
import {
  NodeResizer, NodeResizeControl, ReactFlow, ReactFlowProvider,
  useNodesState, useReactFlow, type Node, type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import { WidgetFrame } from './WidgetFrame'
import { specByInstanceId, type WidgetCtx } from '../lib/widgetRegistry'
import { useT } from '../lib/i18n'

export type WidgetPos = { x: number; y: number; w: number; h: number }

type CanvasCtx = {
  ctx: WidgetCtx
  onRemove: (id: string) => void
  onGeometry: (id: string, geom: Partial<WidgetPos>) => void
  onMinimize: (id: string) => void
  onFullscreen: (id: string) => void
}
const CanvasContext = createContext<CanvasCtx | null>(null)

// Nœud « widget » : poignée de drag (en-tête WidgetFrame) + redimensionnement (NodeResizer).
function WidgetNode({ id }: NodeProps) {
  const cc = useContext(CanvasContext)
  const spec = specByInstanceId(id)
  const t = useT()
  if (!cc || !spec) return null
  // Résout le titre : clé i18n pour la plupart des widgets, chaîne directe pour texteditor.
  const title = spec.title.startsWith('widgets.') ? t(spec.title as any) : spec.title
  // Le texteditor reproduit une page A4 — on l'empêche de dépasser la largeur
  // d'une A4 + 20px de marge gauche/droite (794 + 40 + 6 de bordure = 840),
  // et on le restreint au resize diagonal pour préserver le ratio « feuille ».
  const isTextEditor = spec.id === 'texteditor'
  return (
    <>
      {isTextEditor ? (
        <NodeResizeControl
          position="bottom-right"
          minWidth={500} minHeight={520} maxWidth={840}
          className="widget-resize-control widget-resize-control--diag"
          onResizeEnd={(_, p) => cc.onGeometry(id, { x: p.x, y: p.y, w: p.width, h: p.height })}
        >
          <span className="widget-resize-handle-diag" aria-hidden />
        </NodeResizeControl>
      ) : (
        <NodeResizer
          minWidth={260} minHeight={160}
          lineClassName="widget-resize-line"
          handleClassName="widget-resize-handle"
          onResizeEnd={(_, p) => cc.onGeometry(id, { x: p.x, y: p.y, w: p.width, h: p.height })}
        />
      )}
      <div className="nowheel h-full w-full overflow-hidden rounded-[36px] border-[3px] border-navy-700 bg-white dark:bg-ink-800 shadow-card">
        <WidgetFrame
          onClose={() => cc.onRemove(id)}
          onMinimize={() => cc.onMinimize(id)}
          onFullscreen={() => cc.onFullscreen(id)}
          title={title}
        >
          {spec.render(cc.ctx, id)}
        </WidgetFrame>
      </div>
    </>
  )
}

const NODE_TYPES = { widget: WidgetNode }

// Bornes de zoom du canvas (utilisées par ReactFlow et par la mise au point auto).
const CANVAS_MIN_ZOOM = 0.5
const CANVAS_MAX_ZOOM = 1.5
// Marge intérieure (en px) autour du bounding box des widgets lors du « fit all ».
const FIT_PADDING_PX = 80

type Props = {
  ctx: WidgetCtx
  active: string[]
  positions: Record<string, WidgetPos>
  onRemove: (id: string) => void
  onGeometry: (id: string, geom: Partial<WidgetPos>) => void
  onAdd: (id: string, pos: { x: number; y: number }) => void
  onMinimize: (id: string) => void
  onFullscreen: (id: string) => void
  // z-index par widget (instanceId → ordre d'empilement). Plus la valeur est
  // haute, plus le widget est au premier plan. `onBringToFront` est appelé quand
  // l'utilisateur interagit avec un nœud (clic/drag) pour le remonter au-dessus.
  zOrder?: Record<string, number>
  onBringToFront?: (id: string) => void
  // Centrage caméra sur un widget (ex. la carte au spawn). `chatWidth` = largeur
  // px occultée à gauche par le chat (0 si replié) ; `onFocused` réarme le parent.
  focusId?: string | null
  // Cadrage caméra sur une ZONE (bbox de plusieurs widgets) — ex. vue combinée
  // éditeur de rapport + carte. Prioritaire sur `focusId`. `onFocused` réarme.
  focusBounds?: { x: number; y: number; w: number; h: number } | null
  chatWidth?: number
  onFocused?: () => void
}

function CanvasInner({ ctx, active, positions, onRemove, onGeometry, onAdd, onMinimize, onFullscreen, zOrder, onBringToFront, focusId, focusBounds, chatWidth = 0, onFocused }: Props) {
  const { screenToFlowPosition, setCenter, getViewport } = useReactFlow()
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  // Conteneur du canvas — sert à mesurer la zone visible pour décider si un widget
  // est déjà à l'écran et, sinon, calculer le zoom qui l'y fait tenir.
  const containerRef = useRef<HTMLDivElement>(null)

  // Amène le widget `focusId` dans la vue — SANS perturber le cadrage de
  // l'analyste s'il y est déjà. Ancien comportement : un « fit all » global
  // était imposé à CHAQUE ajout/clic, écrasant le pan/zoom courant et donnant
  // l'impression que « le tableau saute ». Désormais : si le widget ciblé est
  // entièrement visible (zone utile = à droite du chat), on ne touche pas la
  // caméra ; sinon on recentre sur LUI (et non sur le barycentre de tous).
  useEffect(() => {
    if (nodes.length === 0) return // pas encore monté → on retentera quand `nodes` change
    // Cible = zone explicite (vue combinée) en priorité, sinon le widget `focusId`.
    let target: { x: number; y: number; w: number; h: number } | null = null
    if (focusBounds) {
      target = focusBounds
    } else if (focusId) {
      const node = nodes.find((n) => n.id === focusId)
      if (!node) return // le widget ciblé doit être monté avant qu'on cadre dessus
      target = {
        x: node.position.x, y: node.position.y,
        w: Number(node.style?.width) || 380, h: Number(node.style?.height) || 320,
      }
    }
    if (!target) return

    const rect = containerRef.current?.getBoundingClientRect()
    const containerW = rect?.width ?? window.innerWidth
    const containerH = rect?.height ?? window.innerHeight

    const w = target.w
    const h = target.h
    const nx = target.x, ny = target.y

    // Position du widget à l'écran au zoom/pan courant.
    const vp = getViewport()
    const left = nx * vp.zoom + vp.x
    const top = ny * vp.zoom + vp.y
    const right = (nx + w) * vp.zoom + vp.x
    const bottom = (ny + h) * vp.zoom + vp.y
    const PAD = 12
    const alreadyVisible =
      left >= chatWidth + PAD && top >= PAD &&
      right <= containerW - PAD && bottom <= containerH - PAD
    if (alreadyVisible) { onFocused?.(); return }

    // Hors-cadre : on recentre sur ce widget, à un zoom qui le fait tenir dans la
    // zone utile (borné), décalé d'un demi-chat pour ne pas passer sous la sidebar.
    const availW = Math.max(200, containerW - chatWidth - FIT_PADDING_PX * 2)
    const availH = Math.max(200, containerH - FIT_PADDING_PX * 2)
    const zoom = Math.min(
      CANVAS_MAX_ZOOM,
      Math.max(CANVAS_MIN_ZOOM, Math.min(availW / w, availH / h)),
    )
    setCenter(nx + w / 2 - chatWidth / (2 * zoom), ny + h / 2, { zoom, duration: 450 })
    onFocused?.()
  }, [focusId, focusBounds, nodes, chatWidth, setCenter, getViewport, onFocused])

  // Synchronise la liste active avec les nœuds (ajout/suppression) en conservant
  // la position/taille courante des nœuds déjà montés.
  useEffect(() => {
    setNodes((prev) => {
      const byId = new Map(prev.map((n) => [n.id, n]))
      return active.map((id, i) => {
        const z = zOrder?.[id]
        const ex = byId.get(id)
        // Nœud déjà monté : on conserve sa position/taille, mais on réaligne son
        // z-index s'il a changé (clic taskbar / clic sur le nœud → premier plan).
        if (ex) return z != null && ex.zIndex !== z ? { ...ex, zIndex: z } : ex
        const p = positions[id] || { x: 48 + i * 44, y: 48 + i * 44, w: 380, h: 320 }
        return {
          id, type: 'widget', position: { x: p.x, y: p.y },
          style: { width: p.w, height: p.h },
          zIndex: z,
          dragHandle: '.widget-drag-handle', data: {},
        } as Node
      })
    })
  }, [active, positions, zOrder, setNodes])

  const ctxValue = useMemo(
    () => ({ ctx, onRemove, onGeometry, onMinimize, onFullscreen }),
    [ctx, onRemove, onGeometry, onMinimize, onFullscreen]
  )

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
  }, [])
  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    const id = e.dataTransfer.getData('application/widget')
    if (!id) return
    onAdd(id, screenToFlowPosition({ x: e.clientX, y: e.clientY }))
  }, [screenToFlowPosition, onAdd])

  return (
    <CanvasContext.Provider value={ctxValue}>
      <div ref={containerRef} className="h-full w-full bg-slate-200 dark:bg-ink-900" onDragOver={onDragOver} onDrop={onDrop}>
        <ReactFlow
          nodes={nodes}
          nodeTypes={NODE_TYPES}
          onNodesChange={onNodesChange}
          onNodeClick={(_, n) => onBringToFront?.(n.id)}
          onNodeDragStart={(_, n) => onBringToFront?.(n.id)}
          onNodeDragStop={(_, n) => onGeometry(n.id, { x: n.position.x, y: n.position.y })}
          minZoom={CANVAS_MIN_ZOOM}
          maxZoom={CANVAS_MAX_ZOOM}
          defaultViewport={{ x: 0, y: 0, zoom: 0.85 }}
          proOptions={{ hideAttribution: true }}
          deleteKeyCode={null}
          panOnDrag
          zoomOnScroll
        >
          {/* Pas de <Background> : tableau lisse, placement totalement libre
              (pas de grille de fond, pas de snap — cf. retrait de snapToGrid). */}
        </ReactFlow>
      </div>
    </CanvasContext.Provider>
  )
}

// Wrapper : fournit le contexte React Flow (requis par useReactFlow / screenToFlowPosition / setCenter).
export function WidgetCanvas(props: Props) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  )
}
