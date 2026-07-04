// Timeline imagerie satellite (imagerie) — bandeau rétractable au bas du widget
// carte. AXE TEMPOREL HORIZONTAL : une rangée (lane) par constellation, et un
// POINT par image positionné selon sa date d'acquisition sur la période. Survol
// d'un point → l'emprise correspondante passe en jaune sur la carte ; clic →
// fiche détaillée (vignette, nuage, résolution, coût, Acheter / Afficher).
//
// Deux jeux de données nourrissent la MÊME timeline :
//   • `search`      → images DISPONIBLES à l'achat (carte « Acheter »).
//   • `list_orders` → images DÉJÀ ACHETÉES (`purchased`), carte « Afficher ».
//
// Les emprises ne sont dessinées sur la carte QUE pour le point survolé/sélectionné
// (cf. GeoMap) — sinon 50 emprises masqueraient le fond. État carte (exclus,
// survolé, sélectionné) : remonté à GeoMap. État local : repli.

import { useMemo, useState } from 'react'

/** Une image du catalogue (search) ou du patrimoine (list_orders). */
export type ImageryItem = {
  product_id: string
  acquired_at?: string | null
  cloud_cover?: number | null
  constellation?: string | null
  sensor?: string | null
  resolution_m?: number | null
  rings?: [number, number][][]
  bbox?: number[] | null
  preview_url?: string | null
  proxy_tile_url?: string | null
  wmts_ready?: boolean
  // Renseignés quand l'image vient de list_orders (déjà achetée).
  purchased?: boolean
  status?: string | null
  price_amount?: number | null
  price_currency?: string | null
}

type CostEntry = { amount: number | null; currency?: string | null; product_type?: string }
export type ConstCost = Record<string, CostEntry>

const CONST_COLOR: Record<string, string> = {
  PHR: '#f59e0b', SPOT: '#34d399', PNEO: '#a78bfa',
}
function constColor(c?: string | null): string {
  return (c && CONST_COLOR[c]) || '#22d3ee'
}

function ts(iso?: string | null): number | null {
  if (!iso || typeof iso !== 'string') return null
  const t = Date.parse(iso)
  return Number.isFinite(t) ? t : null
}
function fmtDate(iso?: string | null): string {
  return iso && typeof iso === 'string' ? iso.slice(0, 10) : '—'
}
function fmtCloud(c?: number | null): string {
  return typeof c === 'number' && isFinite(c) ? `${Math.round(c)}%` : '—'
}
function fmtRes(r?: number | null): string {
  return typeof r === 'number' && isFinite(r) ? `${Math.round(r * 10) / 10} m` : '—'
}
function currencySymbol(cur?: string | null): string {
  const c = (cur || '').toUpperCase()
  return c === 'EUR' ? '€' : c === 'USD' ? '$' : c === 'GBP' ? '£' : (cur ? ` ${cur}` : '')
}

function costOf(item: ImageryItem, byProduct: ConstCost, byConst: ConstCost): CostEntry | undefined {
  return byProduct[item.product_id] || byConst[item.constellation || '']
}
/** Coût affiché : prix PAYÉ pour une image achetée, sinon estimation /quote. */
function costLabel(item: ImageryItem, byProduct: ConstCost, byConst: ConstCost, loading: boolean): string {
  if (item.purchased && typeof item.price_amount === 'number') {
    return `payé ${Math.round(item.price_amount)}${currencySymbol(item.price_currency)}`
  }
  const c = costOf(item, byProduct, byConst)
  if (c && typeof c.amount === 'number') return `≈ ${Math.round(c.amount)}${currencySymbol(c.currency)}`
  return loading ? '…' : '—'
}

export function ImageryTimeline({
  items,
  costByConst,
  costByProduct,
  costLoading,
  excluded,
  highlightId,
  selectedId,
  onHover,
  onSelect,
  onToggleExclude,
  onBuy,
  onDisplay,
  onListOrders,
  resolveUrl,
}: {
  items: ImageryItem[]
  costByConst: ConstCost
  costByProduct: ConstCost
  costLoading: boolean
  excluded: Set<string>
  highlightId: string | null
  selectedId: string | null
  onHover: (productId: string | null) => void
  onSelect: (productId: string | null) => void
  onToggleExclude: (productId: string) => void
  onBuy: (item: ImageryItem) => void
  onDisplay: (item: ImageryItem) => void
  onListOrders: () => void
  resolveUrl: (url: string) => string
}) {
  const [collapsed, setCollapsed] = useState(false)
  const [confirming, setConfirming] = useState<string | null>(null)

  const { lanes, tMin, tMax } = useMemo(() => {
    const times = items.map((i) => ts(i.acquired_at)).filter((t): t is number => t != null)
    const tMin = times.length ? Math.min(...times) : 0
    const tMax = times.length ? Math.max(...times) : 1
    const byConst = new Map<string, ImageryItem[]>()
    for (const it of items) {
      const k = it.constellation || '—'
      if (!byConst.has(k)) byConst.set(k, [])
      byConst.get(k)!.push(it)
    }
    const rank = (k: string) => (k === 'PHR' ? 0 : k === 'SPOT' ? 1 : k === 'PNEO' ? 2 : 3)
    const lanes = [...byConst.entries()].sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]))
    return { lanes, tMin, tMax }
  }, [items])

  const xPct = (it: ImageryItem): number => {
    const t = ts(it.acquired_at)
    if (t == null || tMax <= tMin) return 50
    return ((t - tMin) / (tMax - tMin)) * 100
  }

  const shownCount = items.length - excluded.size
  const selItem = selectedId ? items.find((i) => i.product_id === selectedId) || null : null

  const pick = (pid: string) => {
    const next = selectedId === pid ? null : pid
    onSelect(next)
    setConfirming(null)
  }

  return (
    <div
      className="pointer-events-auto absolute inset-x-0 bottom-0 z-[500] flex flex-col
                 border-t border-cyan-400/40 bg-white/95 text-[11px] text-slate-700
                 shadow-[0_-4px_12px_rgba(0,0,0,0.12)] backdrop-blur
                 dark:border-cyan-400/30 dark:bg-ink-900/92 dark:text-slate-200"
    >
      {/* En-tête : titre + compteur + légende + « Mes achats » + repli. */}
      <div className="flex items-center gap-3 px-3 py-1.5">
        <button
          type="button"
          onClick={() => setCollapsed((v) => !v)}
          className="flex items-center gap-2 font-semibold text-slate-600 transition hover:text-cyan-600 dark:text-slate-200"
        >
          <span className="text-cyan-500">🛰</span>
          <span>Timeline imagerie</span>
          <span className="rounded bg-cyan-500/15 px-1.5 py-0.5 text-[10px] font-medium text-cyan-700 dark:text-cyan-300">
            {shownCount}/{items.length}
          </span>
        </button>
        <div className="flex items-center gap-2 text-[10px] text-slate-400">
          {lanes.map(([k]) => (
            <span key={k} className="flex items-center gap-1">
              <span className="inline-block h-2 w-2 rounded-full" style={{ background: constColor(k) }} />
              {k}
            </span>
          ))}
        </div>
        <button
          type="button"
          onClick={onListOrders}
          title="Lister les images que j'ai achetées"
          className="rounded border border-cyan-500/50 px-2 py-0.5 text-[10px] font-medium text-cyan-700 transition hover:bg-cyan-500/10 dark:text-cyan-300"
        >
          📥 Mes achats
        </button>
        <button
          type="button"
          onClick={() => setCollapsed((v) => !v)}
          className="ml-auto text-slate-400 transition hover:text-cyan-600"
        >
          {collapsed ? '▴ déplier' : '▾ réduire'}
        </button>
      </div>

      {!collapsed && (
        <div className="relative px-3 pb-2">
          <div className="flex flex-col gap-1.5 py-1">
            {lanes.map(([k, laneItems]) => (
              <div key={k} className="flex items-center gap-2">
                <span className="w-9 shrink-0 text-right text-[10px] font-medium text-slate-400">{k}</span>
                <div className="relative h-5 flex-1 rounded bg-slate-200/50 dark:bg-white/5">
                  <div className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-slate-300/60 dark:bg-white/10" />
                  {laneItems.map((it) => {
                    const isExcluded = excluded.has(it.product_id)
                    const isHot = it.product_id === highlightId
                    const isSel = it.product_id === selectedId
                    return (
                      <button
                        type="button"
                        key={it.product_id}
                        onMouseEnter={() => onHover(it.product_id)}
                        onMouseLeave={() => onHover(selectedId)}
                        onClick={() => pick(it.product_id)}
                        title={`${fmtDate(it.acquired_at)} · ${k} · ☁ ${fmtCloud(it.cloud_cover)}${it.purchased ? ' · acheté' : ''}`}
                        className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 transition"
                        style={{
                          left: `${xPct(it)}%`,
                          width: isSel || isHot ? 14 : 10,
                          height: isSel || isHot ? 14 : 10,
                          // Achetée = carré (losange) ; disponible = rond.
                          borderRadius: it.purchased ? 2 : 9999,
                          background: isExcluded ? 'transparent' : (isHot ? '#facc15' : constColor(k)),
                          border: `${isSel ? 2 : 1}px solid ${isHot ? '#eab308' : it.purchased ? '#ffffff' : (isExcluded ? '#94a3b8' : '#ffffff')}`,
                          opacity: isExcluded ? 0.5 : 1,
                          boxShadow: isSel ? '0 0 0 2px rgba(34,211,238,0.6)' : undefined,
                          zIndex: isHot || isSel ? 2 : 1,
                        }}
                      />
                    )
                  })}
                </div>
              </div>
            ))}
          </div>

          <div className="ml-11 flex justify-between border-t border-slate-200/60 pt-0.5 text-[9px] text-slate-400 dark:border-white/5">
            <span>{fmtDate(new Date(tMin).toISOString())}</span>
            <span>{fmtDate(new Date(tMax).toISOString())}</span>
          </div>

          {selItem && (
            <div className="mt-1 flex items-start gap-3 rounded-md border border-cyan-400/40 bg-white/95 p-2 shadow-sm dark:border-cyan-400/30 dark:bg-ink-900/95">
              {selItem.preview_url ? (
                <img
                  src={resolveUrl(selItem.preview_url)}
                  alt=""
                  loading="lazy"
                  onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none' }}
                  className="h-16 w-16 shrink-0 rounded border border-slate-300/60 object-cover dark:border-white/10"
                />
              ) : null}
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="inline-block h-2 w-2 rounded-full" style={{ background: constColor(selItem.constellation) }} />
                  <span className="font-semibold text-slate-800 dark:text-slate-100">
                    {selItem.constellation || '—'}
                    {selItem.sensor && selItem.sensor !== selItem.constellation ? ` · ${selItem.sensor}` : ''}
                  </span>
                  <span className="font-mono text-slate-500">{fmtDate(selItem.acquired_at)}</span>
                  {selItem.purchased && (
                    <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[9px] font-medium text-emerald-700 dark:text-emerald-300">
                      {(selItem.status || 'acheté')}
                    </span>
                  )}
                </div>
                <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-slate-500 dark:text-slate-400">
                  <span title="Couverture nuageuse">☁ {fmtCloud(selItem.cloud_cover)}</span>
                  <span title="Résolution">⊹ {fmtRes(selItem.resolution_m)}</span>
                  <span className="font-semibold text-cyan-700 dark:text-cyan-300" title="Coût">
                    {costLabel(selItem, costByProduct, costByConst, costLoading)}
                  </span>
                </div>
                <div className="mt-1.5 flex flex-wrap items-center gap-2">
                  {/* Achetée → Afficher ; disponible → Acheter (2 clics) + Afficher (aperçu). */}
                  {selItem.purchased ? (
                    <button
                      type="button"
                      onClick={() => onDisplay(selItem)}
                      className="rounded border border-cyan-500/70 bg-cyan-500/90 px-2 py-0.5 font-semibold text-white transition hover:bg-cyan-500"
                    >
                      🛰 Afficher sur la carte
                    </button>
                  ) : confirming === selItem.product_id ? (
                    <button
                      type="button"
                      onClick={() => { onBuy(selItem); setConfirming(null); pick(selItem.product_id) }}
                      className="rounded border border-red-500/70 bg-red-500/90 px-2 py-0.5 font-semibold text-white transition hover:bg-red-500"
                    >
                      ⚠ Confirmer l'achat réel {costLabel(selItem, costByProduct, costByConst, costLoading)}
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setConfirming(selItem.product_id)}
                      className="rounded border border-emerald-500/70 bg-emerald-500/90 px-2 py-0.5 font-semibold text-white transition hover:bg-emerald-500"
                    >
                      Acheter
                    </button>
                  )}
                  {!selItem.purchased && (
                    <button
                      type="button"
                      onClick={() => onDisplay(selItem)}
                      title="Aperçu raster (WMTS) de la scène sur la carte"
                      className="rounded border border-cyan-500/50 px-2 py-0.5 text-cyan-700 transition hover:bg-cyan-500/10 dark:text-cyan-300"
                    >
                      🛰 Afficher
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => onToggleExclude(selItem.product_id)}
                    className="rounded border border-slate-300/70 px-2 py-0.5 text-slate-600 transition hover:bg-slate-500/10 dark:border-white/15 dark:text-slate-300"
                  >
                    {excluded.has(selItem.product_id) ? 'Réafficher' : 'Ignorer'}
                  </button>
                  <button
                    type="button"
                    onClick={() => { onSelect(null); setConfirming(null) }}
                    className="ml-auto text-slate-400 transition hover:text-slate-600"
                    title="Fermer"
                  >
                    ✕
                  </button>
                </div>
              </div>
            </div>
          )}
          {!selItem && (
            <div className="ml-11 pt-0.5 text-[9px] italic text-slate-400">
              Survol = surligner l'emprise · clic sur un point = détails + acheter/afficher · « Mes achats » = images déjà commandées.
            </div>
          )}
        </div>
      )}
    </div>
  )
}
