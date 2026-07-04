// Detail d'un agent : readme + tools CRUD + Agent Card A2A + serveurs MCP.

import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import type { AgentDetail, MCPToolTestResult, ProbeReport, ToolTestResult } from '../lib/types'
import { useT } from '../lib/i18n'
import { agentEmoji, agentLabel } from '../lib/ui'
import { ToolRow } from '../components/ToolRow'
import { McpServerCard } from '../components/McpServerCard'
import { AgentChatPopup } from '../components/AgentChatPopup'

export function AgentDetailView() {
  const t = useT()
  const { agentId } = useParams<{ agentId: string }>()
  const [agent, setAgent] = useState<AgentDetail | null>(null)
  const [showCard, setShowCard] = useState(false)
  const [chatOpen, setChatOpen] = useState(false)
  const [newToolOpen, setNewToolOpen] = useState(false)
  const [newName, setNewName] = useState('')
  const [newDesc, setNewDesc] = useState('')
  const [newSource, setNewSource] = useState<'native' | 'mcp' | 'a2a'>('native')
  const [probing, setProbing] = useState(false)
  const [probeReport, setProbeReport] = useState<ProbeReport | null>(null)
  const [probeOpen, setProbeOpen] = useState(false)  // résultat du probe replié par défaut
  // Tests de tools (réels) pilotés ici pour que « Test All » puisse rejouer chaque carte.
  const [testResults, setTestResults] = useState<Record<number, ToolTestResult>>({})
  const [testingIds, setTestingIds] = useState<Set<number>>(new Set())
  const [testingAll, setTestingAll] = useState(false)
  // Tests MCP : niveau 1 = serverName, niveau 2 = toolName.
  const [mcpTestResults, setMcpTestResults] = useState<Record<string, Record<string, MCPToolTestResult>>>({})
  // Clé = "serverName:toolName" pour les tools MCP en cours de test.
  const [mcpTestingIds, setMcpTestingIds] = useState<Set<string>>(new Set())

  const reload = async () => {
    if (!agentId) return
    const a = await api.agent(agentId)
    setAgent(a)
  }
  useEffect(() => { reload() /* eslint-disable-line */ }, [agentId])

  // Exécute réellement un tool (endpoint /test) et mémorise son verdict.
  const testOne = async (toolId: number) => {
    setTestingIds((s) => new Set(s).add(toolId))
    try {
      const res = await api.testTool(toolId)
      setTestResults((m) => ({ ...m, [toolId]: res }))
    } catch (e: any) {
      setTestResults((m) => ({ ...m, [toolId]: {
        tool_id: toolId, name: '', ok: false, args: {}, latency_ms: 0, detail: String(e),
      } }))
    } finally {
      setTestingIds((s) => { const n = new Set(s); n.delete(toolId); return n })
    }
  }

  // Teste un outil MCP déclaré sur un serveur de l'agent.
  const testMcpTool = async (serverName: string, toolName: string, args: Record<string, unknown>) => {
    const key = `${serverName}:${toolName}`
    setMcpTestingIds((s) => new Set(s).add(key))
    try {
      const res = await api.testMcpTool(agent!.id, serverName, toolName, args)
      setMcpTestResults((m) => ({
        ...m,
        [serverName]: { ...(m[serverName] || {}), [toolName]: res },
      }))
    } catch (e: any) {
      setMcpTestResults((m) => ({
        ...m,
        [serverName]: {
          ...(m[serverName] || {}),
          [toolName]: { server_name: serverName, tool_name: toolName, ok: false, args, latency_ms: 0, detail: String(e) },
        },
      }))
    } finally {
      setMcpTestingIds((s) => { const n = new Set(s); n.delete(key); return n })
    }
  }

  // Teste tous les tools natifs puis tous les tools MCP de chaque serveur en séquence.
  const testAll = async () => {
    if (!agent) return
    setTestingAll(true)
    // Tools natifs (comportement existant)
    for (const tl of agent.tools) await testOne(tl.id)
    // Tools MCP : pour chaque serveur, pour chaque tool → test avec args vides
    // (le backend génère des args sample depuis le schéma via tools/list)
    for (const srv of (agent.mcp_servers || [])) {
      for (const toolName of (srv.tools || [])) {
        await testMcpTool(srv.name, toolName, {})
      }
    }
    setTestingAll(false)
  }

  // Contenu localisé d'un agent (clé i18n par discipline) avec repli sur le texte backend.
  const defText = (key: string, fallback: string) => {
    const v = t(key)
    return v === key ? fallback : v
  }

  // Texte de la card servi par le backend (spécifique à l'agent), avec repli sur
  // le contenu localisé générique par discipline si la card ne le fournit pas.
  const cardText = (backendText: string, defKey: string) => {
    const v = (backendText || '').trim()
    return v || defText(defKey, '')
  }

  // Re-probe : rafraîchit le snapshot Agent Card en base + retourne le rapport.
  const probeNow = async () => {
    if (!agent) return
    setProbing(true)
    try {
      const report = await api.probeAgent(agent.id)
      setProbeReport(report)
      await reload()
    } catch (e: any) {
      alert(t('agents.detail.probeError', { error: e }))
    } finally {
      setProbing(false)
    }
  }

  const createTool = async () => {
    if (!agentId || !newName.trim()) return
    await api.createTool(agentId, { name: newName.trim(), description: newDesc, source: newSource })
    setNewName(''); setNewDesc(''); setNewToolOpen(false)
    await reload()
  }

  if (!agent) {
    return <div className="p-8 text-sm text-slate-500 dark:text-slate-400">{t('agents.detail.loading')}</div>
  }

  return (
    <div className="h-full overflow-y-auto p-6">
      <Link to="/agents" className="text-xs font-medium text-navy-600 dark:text-navy-300 hover:underline">{t('agents.detail.back')}</Link>
      <div className="mt-3 flex items-center gap-3">
        <span className="text-3xl leading-none">{agentEmoji(agent.discipline)}</span>
        <h1 className="text-2xl font-bold tracking-tight text-navy-700 dark:text-navy-200">{agentLabel(agent.id)}</h1>
        {agent.transport === 'a2a' && (
          <button
            onClick={() => setChatOpen(true)}
            className="ml-auto flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-navy-700 transition hover:border-navy-300 hover:bg-slate-50 dark:border-ink-600 dark:bg-ink-800 dark:text-navy-200 dark:hover:bg-ink-700"
            title={t('agents.detail.testAgentTitle')}
          >
            {t('agents.detail.testAgent')}
          </button>
        )}
      </div>
      <p className="mt-2 max-w-2xl text-sm text-slate-500 dark:text-slate-400">
        {cardText(agent.description, `agents.defs.${agent.discipline.toLowerCase()}.description`)}
      </p>

      {/* Readme */}
      <section className="card mt-6 p-4">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">{t('agents.detail.readme')}</h2>
        <p className="text-sm text-slate-700 dark:text-slate-200">
          <span className="font-semibold text-slate-800 dark:text-slate-100">{t('agents.detail.whenToUse')}</span>{' '}
          {cardText(agent.when_to_use, `agents.defs.${agent.discipline.toLowerCase()}.whenToUse`)}
        </p>
      </section>

      {/* Tools */}
      <section className="card mt-4 p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
            {t('agents.detail.tools', { count: agent.tools.length })}
          </h2>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setNewToolOpen((v) => !v)}
              className="rounded-lg bg-navy-700 px-2.5 py-1 text-xs font-semibold text-white transition hover:bg-navy-800"
            >
              {newToolOpen ? t('agents.detail.cancel') : t('agents.detail.addTool')}
            </button>
            <button
              onClick={testAll}
              disabled={testingAll || agent.tools.length === 0}
              className="rounded-lg border border-slate-300 dark:border-ink-600 bg-white dark:bg-ink-800 px-2.5 py-1 text-xs font-semibold text-navy-700 dark:text-navy-200 transition hover:border-navy-300 hover:bg-slate-50 dark:hover:bg-ink-700 disabled:opacity-50"
            >
              {testingAll ? `… ${t('agents.detail.testingAll')}` : t('agents.detail.testAll')}
            </button>
          </div>
        </div>
        {newToolOpen && (
          <div className="mb-3 rounded-xl border border-slate-200 dark:border-ink-600 bg-slate-50 dark:bg-ink-700 p-3">
            <div className="grid grid-cols-2 gap-2">
              <input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder={t('agents.detail.namePlaceholder')}
                className="field"
              />
              <select
                value={newSource}
                onChange={(e) => setNewSource(e.target.value as any)}
                className="field"
              >
                <option value="native">native</option>
                <option value="mcp">mcp</option>
                <option value="a2a">a2a</option>
              </select>
            </div>
            <input
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
              placeholder={t('agents.detail.descriptionPlaceholder')}
              className="field mt-2 w-full"
            />
            <button
              onClick={createTool}
              className="btn-primary mt-2"
            >
              {t('agents.detail.create')}
            </button>
          </div>
        )}
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {agent.tools.map((tl) => (
            <ToolRow
              key={tl.id}
              tool={tl}
              onChanged={reload}
              result={testResults[tl.id]}
              testing={testingIds.has(tl.id)}
              onTest={() => testOne(tl.id)}
            />
          ))}
          {agent.tools.length === 0 && (
            <p className="text-xs text-slate-400 dark:text-slate-500">{t('agents.detail.noTool')}</p>
          )}
        </div>
      </section>

      {/* A2A v1.0 — snapshot Agent Card persisté à l'enrôlement. */}
      <section className="card mt-4 p-4">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">A2A v1.0</h2>
          <span className="mono text-[10px] text-slate-400 dark:text-slate-500">{t('agents.detail.a2aTransport', { transport: agent.transport })}</span>
        </div>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          {agent.transport === 'a2a'
            ? t('agents.detail.a2aEnrolled')
            : t('agents.detail.a2aInternal')}
        </p>
        {agent.card_url && (
          <div className="mono mt-2 break-all rounded-lg bg-slate-50 dark:bg-ink-700 p-2 text-[11px] text-navy-700 dark:text-navy-200">
            {agent.card_url}
          </div>
        )}
        <div className="mt-2 flex flex-wrap gap-2">
          <button
            onClick={() => setShowCard((v) => !v)}
            disabled={!agent.agent_card}
            className="rounded-lg border border-slate-300 dark:border-ink-600 bg-white dark:bg-ink-800 px-3 py-1 text-xs font-semibold text-violet-700 dark:text-violet-300 transition hover:bg-slate-50 dark:hover:bg-ink-700 disabled:opacity-40"
          >
            {showCard ? t('agents.detail.hideJsonCard') : t('agents.detail.showJsonCard')}
          </button>
          {agent.transport === 'a2a' && (
            <button
              onClick={probeNow}
              disabled={probing}
              className="rounded-lg border border-slate-300 dark:border-ink-600 bg-white dark:bg-ink-800 px-3 py-1 text-xs font-semibold text-navy-700 dark:text-navy-200 transition hover:bg-slate-50 dark:hover:bg-ink-700 disabled:opacity-40"
            >
              {probing ? t('agents.detail.probing') : t('agents.detail.reprobe')}
            </button>
          )}
        </div>
        {showCard && agent.agent_card && (
          <pre className="mono mt-2 max-h-72 overflow-auto rounded-lg bg-slate-50 dark:bg-ink-700 p-2 text-[10px] text-slate-700 dark:text-slate-200">
{JSON.stringify(agent.agent_card, null, 2)}
          </pre>
        )}
        {showCard && !agent.agent_card && (
          <p className="mt-2 text-xs text-slate-400 dark:text-slate-500">{t('agents.detail.noCardSnapshot')}</p>
        )}
        {probeReport && (
          <div className="mt-2 rounded-xl border border-slate-200 dark:border-ink-600 bg-slate-50 dark:bg-ink-700 p-2">
            {/* En-tête toujours visible (verdict + latence) ; le détail des checks
                est replié par défaut mais dépliable via ce bouton. */}
            <button
              onClick={() => setProbeOpen((v) => !v)}
              className="flex w-full items-center justify-between text-[11px]"
            >
              <span className="flex items-center gap-1.5">
                <span className={`shrink-0 text-slate-400 dark:text-slate-500 transition-transform ${probeOpen ? 'rotate-90' : ''}`}>▸</span>
                <span className={probeReport.ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}>
                  {probeReport.ok ? t('agents.detail.probeOk') : t('agents.detail.probeFail')}
                </span>
                <span className="text-slate-400 dark:text-slate-500">{t('agents.detail.probeChecks', { count: probeReport.checks.length })}</span>
              </span>
              <span className="mono text-slate-400 dark:text-slate-500">{probeReport.latency_ms.toFixed(0)} ms</span>
            </button>
            {probeOpen && (
              <ul className="mt-1 space-y-0.5 text-[10px]">
                {probeReport.checks.map((c) => (
                  <li key={c.name}
                      className={c.status === 'fail' ? 'text-red-600 dark:text-red-400'
                                  : c.status === 'warn' ? 'text-amber-600 dark:text-amber-400' : 'text-slate-500 dark:text-slate-400'}>
                    [{c.status}] {c.name} — {c.detail}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>

      {/* MCP servers */}
      <section className="card mt-4 p-4">
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          {t('agents.detail.mcpServers', { count: agent.mcp_servers?.length || 0 })}
        </h2>
        {!agent.mcp_servers?.length && (
          <p className="text-xs text-slate-400 dark:text-slate-500">
            {t('agents.detail.noMcpServer')}
          </p>
        )}
        <div className="space-y-3">
          {agent.mcp_servers?.map((s: any, i: number) => (
            <McpServerCard
              key={i}
              agentId={agent.id}
              server={s}
              results={mcpTestResults[s.name] || {}}
              testingTools={new Set([...mcpTestingIds].filter(k => k.startsWith(`${s.name}:`)).map(k => k.slice(s.name.length + 1)))}
              onTest={(toolName, args) => testMcpTool(s.name, toolName, args)}
            />
          ))}
        </div>
      </section>

      {chatOpen && <AgentChatPopup agentId={agent.id} onClose={() => setChatOpen(false)} />}
    </div>
  )
}
