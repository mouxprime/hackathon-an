// Grille des agents — registre vivant unifié. Chaque agent = une carte : icône de
// discipline, statut (Healthy / Error / Idle), pods live, serveurs MCP, outils actifs.

import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { usePolling } from '../lib/hooks'
import { useT } from '../lib/i18n'
import { agentEmoji, agentLabel, STATUS_AGENT_CLS, timeAgo } from '../lib/ui'
import { AgentChatPopup } from '../components/AgentChatPopup'

// Clés i18n des libellés de statut, sélectionnées par l'enum backend (healthy/error/idle).
const STATUS_KEY: Record<string, string> = {
  healthy: 'agents.status.healthy', error: 'agents.status.error', idle: 'agents.status.idle',
}

export function AgentsView() {
  const t = useT()
  const agents = usePolling(api.agents, 3000) || []
  const navigate = useNavigate()
  // Agent ciblé par le pop-up de test (chat A2A direct, sans mémoire/stockage).
  const [chatAgent, setChatAgent] = useState<string | null>(null)
  // Texte libre de recherche (filtre agents/tools/MCP/discipline).
  const [query, setQuery] = useState('')
  // Chips actifs sous forme "disc:<discipline>" ou "status:<status>".
  const [activeChips, setActiveChips] = useState<Set<string>>(new Set())

  // Disciplines uniques extraites des agents live — alimente les chips de facette.
  const disciplines = useMemo(
    () => [...new Set((agents as any[]).map((a) => a.discipline).filter(Boolean))] as string[],
    [agents]
  )

  // Bascule un chip actif/inactif.
  const toggleChip = (key: string) => {
    setActiveChips((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  // Liste filtrée + annotation _matchedTool pour le badge de match.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    const discActive = [...activeChips].filter((c) => c.startsWith('disc:'))
    const statusActive = [...activeChips].filter((c) => c.startsWith('status:'))

    return (agents as any[])
      .filter((a) => {
        if (discActive.length > 0 && !discActive.includes(`disc:${a.discipline}`)) return false
        if (statusActive.length > 0 && !statusActive.includes(`status:${a.status}`)) return false
        if (!q) return true
        const text = [a.id, a.description, a.when_to_use, a.discipline].join(' ').toLowerCase()
        if (text.includes(q)) return true
        return (a.tools || []).some((tool: any) =>
          `${tool.name} ${tool.description} ${tool.source}`.toLowerCase().includes(q)
        )
      })
      .map((a) => {
        if (!q) return { ...a, _matchedTool: null as string | null }
        const matchedTool = (a.tools || []).find((tool: any) =>
          `${tool.name} ${tool.description} ${tool.source}`.toLowerCase().includes(q)
        )
        return { ...a, _matchedTool: (matchedTool?.name ?? null) as string | null }
      })
  }, [agents, query, activeChips])

  // Vrai si au moins un filtre est actif (affiche le compteur filtré/total).
  const hasFilter = query.trim() !== '' || activeChips.size > 0

  return (
    <div className="h-full space-y-6 overflow-y-auto p-6">
      <div>
        <h1 className="text-xl font-bold tracking-tight text-navy-700 dark:text-navy-200">{t('agents.list.title')}</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          {t('agents.list.description')}
        </p>
      </div>

      {/* Barre de recherche — filtre agents/discipline/tools/MCP en temps réel. */}
      <div className="relative">
        <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-slate-400">
          <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-4.35-4.35M17 11A6 6 0 1 1 5 11a6 6 0 0 1 12 0z" />
          </svg>
        </span>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label={t('agents.list.searchPlaceholder')}
          placeholder={t('agents.list.searchPlaceholder')}
          className="h-9 w-full rounded-xl border border-slate-200 bg-white pl-9 pr-8 text-sm text-slate-800 placeholder-slate-400 focus:border-navy-400 focus:outline-none focus:ring-1 focus:ring-navy-400 dark:border-ink-600 dark:bg-ink-800 dark:text-slate-100 dark:placeholder-slate-500 dark:focus:border-navy-500"
        />
        {query && (
          <button
            onClick={() => setQuery('')}
            aria-label="Clear search"
            className="absolute inset-y-0 right-2 flex items-center px-1 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
          >
            ×
          </button>
        )}
      </div>


      <section>
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          {hasFilter
            ? t('agents.list.filteredCount', { filtered: filtered.length, count: agents.length })
            : t('agents.list.registered', { count: agents.length })}
        </h2>
        <div className="grid gap-4"
              style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))' }}>
          {filtered.map((a: any) => (
            <div
              key={a.id}
              role="button"
              tabIndex={0}
              onClick={() => navigate(`/agents/${a.id}`)}
              onKeyDown={(e) => { if (e.key === 'Enter') navigate(`/agents/${a.id}`) }}
              className="card group animate-fadeInUp cursor-pointer p-4 text-left transition hover:-translate-y-0.5 hover:border-navy-200 hover:shadow-md"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex min-w-0 items-center gap-2.5">
                  <span className="text-2xl leading-none">{agentEmoji(a.discipline)}</span>
                  <div className="min-w-0">
                    <span className="block truncate text-lg font-bold text-slate-800 dark:text-slate-100">{agentLabel(a.id)}</span>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  {/* Bulle de discussion : test direct de l'agent (a2a uniquement). */}
                  {a.transport === 'a2a' && (
                    <button
                      onClick={(e) => { e.stopPropagation(); setChatAgent(a.id) }}
                      title={t('agents.list.testAgentTitle')}
                      aria-label={t('agents.list.testAgentAria')}
                      className="flex h-7 w-7 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-500 transition hover:border-navy-300 hover:text-navy-700 dark:border-ink-600 dark:bg-ink-800 dark:text-slate-400 dark:hover:text-navy-200"
                    >
                      💬
                    </button>
                  )}
                  <span className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium ${STATUS_AGENT_CLS[a.status] || STATUS_AGENT_CLS.idle}`}>
                    <span className="h-1.5 w-1.5 rounded-full bg-current" />
                    {t(STATUS_KEY[a.status] || STATUS_KEY.idle)}
                  </span>
                </div>
              </div>

              <p className="mt-2 line-clamp-2 text-xs text-slate-500 dark:text-slate-400">{a.description}</p>

              <div className="mt-4 grid grid-cols-2 gap-2 text-center text-xs">
                <div className="rounded-lg bg-slate-50 dark:bg-ink-700 p-2">
                  <div className="mono text-lg font-semibold text-navy-700 dark:text-navy-200">{a.mcp_count ?? 0}</div>
                  <div className="text-[10px] uppercase text-slate-400 dark:text-slate-500">{t('agents.list.mcp')}</div>
                </div>
                <div className="rounded-lg bg-slate-50 dark:bg-ink-700 p-2">
                  <div className="mono text-lg font-semibold text-violet-600 dark:text-violet-400">
                    {a.tools_active}<span className="text-slate-400 dark:text-slate-500">/{a.tools_total}</span>
                  </div>
                  <div className="text-[10px] uppercase text-slate-400 dark:text-slate-500">{t('agents.list.tools')}</div>
                </div>
              </div>

              {a._matchedTool && (
                <div className="mt-2 truncate text-[10px] text-violet-600 dark:text-violet-400">
                  🔧 {t('agents.list.toolMatch', { name: a._matchedTool.slice(0, 24) })}
                </div>
              )}
              <div className="mt-3 text-right text-[11px] text-slate-400 dark:text-slate-500">
                {t('agents.list.last', { ago: timeAgo(a.last_activity) })}
              </div>
            </div>
          ))}
          {filtered.length === 0 && (
            <div className="col-span-full rounded-xl border border-dashed border-slate-300 dark:border-ink-600 p-6 text-center text-sm text-slate-500 dark:text-slate-400">
              {hasFilter
                ? (query.trim()
                    ? t('agents.list.noResults', { query })
                    : t('agents.list.noResultsFilters'))
                : t('agents.list.empty')}
            </div>
          )}
        </div>
      </section>

      {chatAgent && <AgentChatPopup agentId={chatAgent} onClose={() => setChatAgent(null)} />}
    </div>
  )
}
