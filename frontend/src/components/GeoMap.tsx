// Widget carte — rend désormais la geo-library (OpenLayers + fond OSM) servie
// sous /geolib/ dans une iframe, et dialogue avec elle par postMessage.
//
// Pourquoi une iframe : la geo-library est une appli Angular 21 (cf.
// hemicycle/_utils/geolib-host). React et Angular ne partagent pas d'arbre de
// rendu → on isole la geo-library dans son propre runtime et on pousse les
// entités / récupère les clics via un pont postMessage. Le contrat de messages
// est défini côté hôte (geolib-host/src/app/bridge.types.ts).
//
// Le contrat de props (events, sendMapPoint, onPick, markers) est INCHANGÉ :
// widgetRegistry, FireSupportWidget et le widget ReleGSim continuent de marcher.

import { useEffect, useMemo, useRef, useState } from 'react'
import type { UIEvent } from '../lib/types'
import { useGeoOverlayStore } from '../lib/geoOverlayStore'
import { useT } from '../lib/i18n'
import { ImageryTimeline, type ImageryItem, type ConstCost } from './ImageryTimeline'

type Loc = { lat: number; lon: number; name: string; note?: string; agent: string;
             sidc?: string; hostility?: string; course?: number; speed?: number }
// `id` / `highlighted` portent l'identité (product_id imagerie) et le surlignage
// jaune au survol d'une ligne de timeline. Le contrat est dupliqué côté hôte
// (geolib-host/bridge.types.ts BridgePoly).
type Poly = { rings: [number, number][][]; name: string; note?: string; agent: string;
              id?: string; highlighted?: boolean }
// Calque raster (imagerie satellite) à afficher : tuiles XYZ servies par
// le proxy de l'agent. Le contrat est dupliqué côté hôte (geolib-host/bridge.types.ts).
type TileLayerSpec = { product_id: string; tile_url: string; name: string; visible: boolean;
                       min_zoom?: number | null; max_zoom?: number | null; bbox?: number[] | null }

// URL de l'hôte geo-library (servi sous /geolib/ derrière APISIX). Surchargeable
// en dev via VITE_GEOLIB_URL (ex: http://localhost:4200/).
const GEOLIB_URL = (import.meta.env.VITE_GEOLIB_URL as string | undefined) ?? '/geolib/'

// On accepte les `locations` de TOUT widget, pas seulement `widget_type==='map'`
// (GEOINT) : la recherche fédérée en publie aussi (entités géolocalisées).
//
// On accumule l'état PAR widget : un event `widget` pose les données initiales, un
// `widget_update` applique son `patch` par-dessus (même sémantique que buildTimeline).
// Sans ça, une RÉ-ÉMISSION de `locations` (ex. l'agent C2 reclassant des détections en
// hostile) n'atteindrait jamais la carte — extractLocations ne voyait que le 1er `widget`.
function extractLocations(events: UIEvent[]): Loc[] {
  const dataByWidget = new Map<string, any>()
  const wtypeByWidget = new Map<string, string | undefined>()
  const order: string[] = []
  for (const ev of events) {
    if (ev.type === 'widget') {
      const wid = ev.payload?.widget_id ?? `w${ev.seq}`
      if (!dataByWidget.has(wid)) order.push(wid)
      dataByWidget.set(wid, { ...(ev.payload?.data ?? {}) })
      wtypeByWidget.set(wid, ev.payload?.widget_type)
    } else if (ev.type === 'widget_update') {
      const wid = ev.payload?.widget_id
      const cur = wid != null ? dataByWidget.get(wid) : undefined
      if (cur && ev.payload?.patch) Object.assign(cur, ev.payload.patch)
    }
  }
  const locs: Loc[] = []
  for (const wid of order) {
    const data = dataByWidget.get(wid)
    if (!Array.isArray(data?.locations)) continue
    const agent = data.agent_type || wtypeByWidget.get(wid) || 'geoint'
    for (const l of data.locations) {
      if (typeof l.lat === 'number' && typeof l.lon === 'number') {
        locs.push({ lat: l.lat, lon: l.lon, name: l.name || '?', note: l.note, agent,
                    sidc: l.sidc, hostility: l.hostility, course: l.course, speed: l.speed })
      }
    }
  }
  return locs
}

// Autorité d'une loc à un point donné : une affiliation CLASSIFIÉE (hostile/ami/neutre via
// `hostility`, ou via le caractère d'affiliation du SIDC ≠ inconnu) prime sur un symbole
// « inconnu ». Sert à la dédup par coordonnée : l'observation hostile de l'agent C2 remplace
// le symbole inconnu de l'overlay d'alerte au même point (l'icône passe de jaune à rouge).
function locAuthority(l: Loc): number {
  const h = (l.hostility || '').trim().toLowerCase()
  if (h && h !== 'unknown' && h !== 'inconnu' && h !== 'pending') return 2
  const aff = l.sidc && l.sidc.length > 1 ? l.sidc[1].toUpperCase() : ''
  if (aff && aff !== 'U' && aff !== '*' && aff !== '-') return 1
  return 0
}

// Dédup par coordonnée (5 décimales ≈ 1 m) : à un même point, on garde la loc la plus
// « autoritaire ». À autorité égale, la première rencontrée gagne (les locs d'investigation
// sont passées avant l'overlay éphémère).
function dedupeByCoord(locs: Loc[]): Loc[] {
  const byKey = new Map<string, Loc>()
  for (const l of locs) {
    const key = `${l.lat.toFixed(5)},${l.lon.toFixed(5)}`
    const prev = byKey.get(key)
    if (!prev || locAuthority(l) > locAuthority(prev)) byKey.set(key, l)
  }
  return [...byKey.values()]
}

// Emprises (polygones) poussées par un agent géo (CSD : footprints d'imagerie).
// Anneaux déjà en [lat, lon] côté bridge. Filtre défensif : un anneau < 3 points
// n'est pas une surface.
function extractPolygons(events: UIEvent[]): Poly[] {
  const polys: Poly[] = []
  for (const ev of events) {
    if (ev.type !== 'widget') continue
    const data = ev.payload?.data
    // L'imagerie satellite pilote ses propres emprises (avec product_id) via
    // `imagery_catalog` — on ignore alors le canal `polygons` générique du même
    // widget pour ne pas dessiner l'emprise deux fois.
    if (Array.isArray(data?.imagery_catalog)) continue
    if (!Array.isArray(data?.polygons)) continue
    const agent = data.agent_type || 'csd'
    for (const p of data.polygons) {
      const rings: [number, number][][] = (Array.isArray(p?.rings) ? p.rings : [])
        .map((ring: any) => (Array.isArray(ring) ? ring : [])
          .filter((pt: any) => Array.isArray(pt) && pt.length >= 2
            && typeof pt[0] === 'number' && typeof pt[1] === 'number')
          .map((pt: any) => [pt[0], pt[1]] as [number, number]))
        .filter((ring: [number, number][]) => ring.length >= 3)
      if (rings.length) polys.push({ rings, name: p.name || 'Zone', note: p.note, agent })
    }
  }
  return polys
}

// Calques raster d'imagerie poussés par un agent (agent imagery : add_to_map → tile_layers).
// On accumule PAR product_id à travers les widgets (le dernier état gagne) : un agent
// qui ré-affiche/masque une scène met simplement à jour son entrée.
// Préfixe d'URL de l'app (Vite `base`, ex. "/hemicycle") : l'agent émet des gabarits
// de tuiles absolus `/api/imagery/...` (il ignore le basePath Hémicycle). On
// les préfixe ici pour qu'ils matchent la route APISIX `<base>/api/*` → gateway.
const BASE_PREFIX = (import.meta.env.BASE_URL || '/').replace(/\/$/, '')
function withBase(url: string): string {
  return url.startsWith('/') ? `${BASE_PREFIX}${url}` : url
}

function extractTileLayers(events: UIEvent[]): TileLayerSpec[] {
  const byId = new Map<string, TileLayerSpec>()
  for (const ev of events) {
    if (ev.type !== 'widget') continue
    const data = ev.payload?.data
    if (!Array.isArray(data?.tile_layers)) continue
    for (const l of data.tile_layers) {
      if (l && typeof l.product_id === 'string' && typeof l.tile_url === 'string') {
        byId.set(l.product_id, {
          product_id: l.product_id, tile_url: withBase(l.tile_url),
          name: typeof l.name === 'string' ? l.name : l.product_id,
          visible: l.visible !== false,
          min_zoom: l.min_zoom, max_zoom: l.max_zoom, bbox: l.bbox,
        })
      }
    }
  }
  return [...byId.values()]
}

// Catalogue d'imagerie satellite poussé par l'agent `search` (date, nuage,
// constellation, résolution, emprise, vignette…). On accumule PAR product_id à
// travers les widgets (dernier état gagne) : il pilote à la fois les emprises de
// la carte ET la timeline. Cf. ImageryTimeline.tsx pour la forme des champs.
function extractImageryCatalog(events: UIEvent[]): ImageryItem[] {
  const byId = new Map<string, ImageryItem>()
  for (const ev of events) {
    if (ev.type !== 'widget') continue
    const data = ev.payload?.data
    if (!Array.isArray(data?.imagery_catalog)) continue
    for (const it of data.imagery_catalog) {
      if (it && typeof it.product_id === 'string') byId.set(it.product_id, it as ImageryItem)
    }
  }
  return [...byId.values()]
}

// AOI (zone cherchée) renvoyée par l'agent — sert à demander le coût par
// constellation sur cette zone. On prend la DERNIÈRE valide.
function extractAoiBbox(events: UIEvent[]): number[] | null {
  let bbox: number[] | null = null
  for (const ev of events) {
    if (ev.type !== 'widget') continue
    const b = ev.payload?.data?.aoi_bbox
    if (Array.isArray(b) && b.length === 4 && b.every((v: any) => typeof v === 'number')) bbox = b
  }
  return bbox
}

// Directive de placement émise par l'orchestrateur (h_collect_result) : on prend
// la DERNIÈRE et on la considère résolue dès qu'un message `user` est arrivé après.
type Placement = { seq: number; prompt: string; field_label: string; request_id: string }
function activePlacement(events: UIEvent[]): Placement | null {
  let dir: Placement | null = null
  for (const ev of events) {
    if (ev.type !== 'widget') continue
    const d = ev.payload?.data
    if (d?.mode === 'place_point') {
      dir = {
        seq: ev.seq,
        prompt: d.prompt || '',
        field_label: d.field_label || 'position',
        request_id: d.request_id || '',
      }
    }
  }
  if (!dir) return null
  const answered = events.some(
    (e) => e.seq > (dir as Placement).seq && e.type === 'message' && e.payload?.role === 'user',
  )
  return answered ? null : dir
}

export function GeoMap({ events, sendMapPoint, onPick, markers, send }: {
  events: UIEvent[]
  sendMapPoint?: (lat: number, lon: number, fieldLabel?: string, requestId?: string) => void
  // Mode « appui feu » : `onPick` rend la carte cliquable (clic → ajoute une cible) et
  // `markers` liste les cibles numérotées à dessiner par-dessus la situation tactique.
  onPick?: (lat: number, lon: number) => void
  markers?: { lat: number; lon: number; n: number }[]
  // Envoi d'un message utilisateur à l'orchestrateur (chat) — sert au bouton
  // « Acheter » de la timeline imagerie (déclenche un place_order HITL côté agent).
  send?: (text: string) => void
}) {
  const t = useT()
  const locs = useMemo(() => extractLocations(events), [events])
  const polys = useMemo(() => extractPolygons(events), [events])
  const tileLayers = useMemo(() => extractTileLayers(events), [events])
  const imageryCatalog = useMemo(() => extractImageryCatalog(events), [events])
  const aoiBbox = useMemo(() => extractAoiBbox(events), [events])

  // État timeline imagerie : images ignorées (retirées de la carte), image
  // survolée (emprise jaune), et coût par constellation (devis /quote).
  const [excluded, setExcluded] = useState<Set<string>>(new Set())
  const [highlightId, setHighlightId] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [costByConst, setCostByConst] = useState<ConstCost>({})
  const [costByProduct, setCostByProduct] = useState<ConstCost>({})
  const [costLoading, setCostLoading] = useState(false)
  const costSigRef = useRef<string>('')

  const visibleImagery = useMemo(
    () => imageryCatalog.filter((it) => !excluded.has(it.product_id)),
    [imageryCatalog, excluded],
  )
  // Emprises d'imagerie : on NE dessine QUE l'image survolée et/ou sélectionnée
  // (pas les 50 d'un coup — sinon les emprises masquent tout le fond). Survolée =
  // jaune ; sélectionnée = cyan. Rien tant qu'on n'interagit pas avec la timeline.
  const imageryPolys = useMemo<Poly[]>(() => {
    const wanted = new Set<string>()
    if (highlightId) wanted.add(highlightId)
    if (selectedId) wanted.add(selectedId)
    if (!wanted.size) return []
    return visibleImagery
      .filter((it) => wanted.has(it.product_id) && Array.isArray(it.rings) && it.rings.length)
      .map((it) => ({
        rings: it.rings as [number, number][][],
        name: `${it.constellation || 'Image'} ${(it.acquired_at || '').slice(0, 10)}`.trim(),
        note: it.sensor || undefined,
        agent: 'imagery',
        id: it.product_id,
        highlighted: it.product_id === highlightId,
      }))
  }, [visibleImagery, highlightId, selectedId])

  // Overlay géo éphémère (alertes canaux) — fusionné avec les locs/polys de l'investigation.
  const overlayLocs = useGeoOverlayStore((s) => s.locations)
  const overlayPolys = useGeoOverlayStore((s) => s.polygons)

  const allLocs = useMemo<Loc[]>(
    // Investigation D'ABORD, overlay ensuite : à coordonnée égale, dédup garde la loc la
    // plus autoritaire (l'observation classifiée de l'agent prime sur l'overlay « inconnu »).
    () => dedupeByCoord([...locs, ...overlayLocs.map((l) => ({ ...l, agent: 'overlay' }))]),
    [locs, overlayLocs],
  )
  const allPolys = useMemo<Poly[]>(
    () => [...polys, ...imageryPolys, ...overlayPolys.map((p) => ({ ...p, agent: 'overlay' }))],
    [polys, imageryPolys, overlayPolys],
  )
  // Les calques raster suivent l'exclusion : ignorer une image masque aussi sa
  // tuile (le cas échéant — une scène de catalogue n'en a pas avant commande).
  const visibleTileLayers = useMemo(
    () => tileLayers.filter((l) => !excluded.has(l.product_id)),
    [tileLayers, excluded],
  )

  // Coût par constellation sur la zone cherchée : un devis par
  // constellation représentée (≤ ~3 appels), via le proxy /quote. Re-déclenché
  // seulement quand la zone OU l'ensemble des constellations change.
  useEffect(() => {
    if (!imageryCatalog.length || !aoiBbox) return
    const reps = new Map<string, string>()
    for (const it of imageryCatalog) {
      const c = it.constellation || 'UNKNOWN'
      if (!reps.has(c)) reps.set(c, it.product_id)
    }
    const sig = `${aoiBbox.join(',')}|${[...reps.keys()].sort().join(',')}`
    if (sig === costSigRef.current) return
    costSigRef.current = sig
    const items = [...reps.entries()].map(([c, pid]) => `${pid}:${c}`).join(',')
    const url = withBase(
      `/api/imagery/quote?bbox=${aoiBbox.join(',')}&items=${encodeURIComponent(items)}`,
    )
    let cancelled = false
    setCostLoading(true)
    fetch(url)
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => {
        if (cancelled || !j) return
        if (j.by_constellation) setCostByConst(j.by_constellation)
        if (j.by_product) setCostByProduct(j.by_product)
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setCostLoading(false) })
    return () => { cancelled = true }
  }, [imageryCatalog, aoiBbox])

  const toggleExclude = (pid: string) => {
    setExcluded((prev) => {
      const next = new Set(prev)
      if (next.has(pid)) next.delete(pid)
      else next.add(pid)
      return next
    })
  }

  // Bouton « Acheter » d'une image : envoie une demande de commande à
  // l'orchestrateur (chat) → routée vers imagery-agent place_order, avec la
  // garde HITL coût côté agent. L'opérateur a déjà confirmé dans la timeline
  // (double-clic) ; on inclut product_id + zone + constellation verbatim pour
  // que l'agent commande la BONNE image sur la BONNE emprise (la zone cherchée,
  // pas la scène entière). Sans `send` (ex. salon en lecture seule), no-op.
  const buyImage = (it: ImageryItem) => {
    if (!send) return
    const bb = (Array.isArray(aoiBbox) && aoiBbox.length === 4)
      ? aoiBbox
      : (Array.isArray(it.bbox) && it.bbox.length === 4 ? it.bbox : null)
    const bboxStr = bb ? `[${bb.join(',')}]` : ''
    const cst = it.constellation ? ` constellation=${it.constellation}` : ''
    const date = (it.acquired_at || '').slice(0, 10)
    send(
      `Commande (place_order) l'image satellite product_id=${it.product_id}` +
      `${cst} bbox=${bboxStr} (acquise le ${date}). Achat confirmé par l'opérateur ` +
      `sur la zone affichée — passe la commande.`,
    )
  }

  // Bouton « Afficher sur la carte » : monte la scène en overlay raster (WMTS) via
  // add_to_map → l'agent renvoie tile_layers → la geo-library peint les tuiles.
  // Marche pour une image achetée (pleine résolution) comme pour un aperçu catalogue.
  const displayImage = (it: ImageryItem) => {
    if (!send) return
    send(`Affiche l'image satellite product_id=${it.product_id} sur la carte (add_to_map).`)
  }

  // Bouton « Mes achats » : recharge la timeline avec les images déjà commandées
  // (list_orders → imagery_catalog `purchased`).
  const listOrders = () => {
    if (!send) return
    send("Liste mes images satellites achetées / commandées avec leur statut (list_orders).")
  }

  // Mode placement : directive active + point provisoire (reset quand on change de point).
  const placement = useMemo(() => activePlacement(events), [events])
  const [pending, setPending] = useState<[number, number] | null>(null)
  useEffect(() => { setPending(null) }, [placement?.request_id])

  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const [ready, setReady] = useState(false)

  // La carte est cliquable si on attend un point (placement orchestrateur) OU en appui feu.
  const pickMode = !!placement || !!onPick

  // Locs envoyées = investigation + overlay (+ le point provisoire en mode placement,
  // pour le matérialiser sur la carte avant confirmation).
  const payloadLocs = useMemo(() => {
    const base = allLocs.map((l) => ({
      lat: l.lat, lon: l.lon, name: l.name, note: l.note, agent: l.agent,
      sidc: l.sidc, hostility: l.hostility, course: l.course, speed: l.speed,
    }))
    if (placement && pending) {
      base.push({ lat: pending[0], lon: pending[1], name: '•', note: undefined, agent: 'geoint',
                  sidc: undefined, hostility: undefined, course: undefined, speed: undefined })
    }
    return base
  }, [allLocs, placement, pending])

  // Pousse l'état complet vers l'iframe à chaque changement (une fois prête).
  useEffect(() => {
    if (!ready) 
      return
    iframeRef.current?.contentWindow?.postMessage(
      { type: 'geolib:set-data', locs: payloadLocs, polys: allPolys, markers: markers ?? [], pickMode },
      '*',
    )
  }, [ready, payloadLocs, allPolys, markers, pickMode])

  // Pousse les calques raster (imagerie satellite) vers l'iframe — message distinct
  // de set-data : la geo-library les monte en TileLayer(XYZ) au-dessus du fond OSM.
  useEffect(() => {
    if (!ready) return
    iframeRef.current?.contentWindow?.postMessage(
      { type: 'geolib:set-tile-layers', layers: visibleTileLayers },
      '*',
    )
  }, [ready, visibleTileLayers])

  // Réception des messages de l'iframe. IMPORTANT : on filtre sur `e.source` car
  // plusieurs widgets carte (donc plusieurs iframes) peuvent coexister et le bus
  // `message` est global → sans ce filtre, un clic dans une carte déclencherait le
  // handler d'une autre.
  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      if (e.source !== iframeRef.current?.contentWindow) return
      const m = e.data
      if (!m || typeof m.type !== 'string') return
      if (m.type === 'geolib:ready') {
        setReady(true)
      } else if (m.type === 'geolib:pick') {
        if (onPick) onPick(m.lat, m.lon)
        else if (placement) setPending([m.lat, m.lon])
      }
    }
    window.addEventListener('message', onMsg)
    return () => window.removeEventListener('message', onMsg)
  }, [onPick, placement])

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="relative flex-1">
        <iframe
          ref={iframeRef}
          src={GEOLIB_URL}
          title="GeoLib"
          className="h-full w-full border-0"
          // La geo-library a besoin de scripts ; même origine (APISIX) → pas de sandbox restrictive.
          allow="fullscreen"
        />
        {/* Bannière mode placement : invite + confirmation du point posé. */}
        {placement && (
          <div className="absolute left-2 top-2 z-[500] flex max-w-[80%] items-center gap-2
                          rounded-md border border-teal-400/70 bg-white/95 px-2.5 py-1.5
                          text-[11px] font-medium text-slate-700 shadow-sm backdrop-blur
                          dark:border-teal-400/50 dark:bg-ink-900/85 dark:text-slate-200">
            <span>📍 {placement.prompt || `Cliquez pour placer : ${placement.field_label}`}</span>
            <button
              type="button"
              disabled={!pending}
              onClick={() => {
                if (pending) {
                  sendMapPoint?.(pending[0], pending[1], placement.field_label, placement.request_id)
                  setPending(null)
                }
              }}
              className="shrink-0 rounded border border-emerald-500/70 bg-emerald-500/90 px-2 py-0.5
                         font-semibold text-white transition hover:bg-emerald-500
                         disabled:cursor-not-allowed disabled:opacity-40"
            >
              {t('widgets.geomap.confirmPoint')}
            </button>
          </div>
        )}

        {/* Timeline imagerie satellite (imagerie) — bandeau rétractable en bas. */}
        {imageryCatalog.length > 0 && (
          <ImageryTimeline
            items={imageryCatalog}
            costByConst={costByConst}
            costByProduct={costByProduct}
            costLoading={costLoading}
            excluded={excluded}
            highlightId={highlightId}
            selectedId={selectedId}
            onHover={setHighlightId}
            onSelect={setSelectedId}
            onToggleExclude={toggleExclude}
            onBuy={buyImage}
            onDisplay={displayImage}
            onListOrders={listOrders}
            resolveUrl={withBase}
          />
        )}
      </div>
    </div>
  )
}
