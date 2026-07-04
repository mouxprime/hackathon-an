// Carte de serveur MCP avec ses outils testables inline. Chaque tool ouvre un
// panneau d'args sous lui pour permettre d'ajuster les paramètres et re-tester.
// Symétrique à ToolRow pour la densité et la palette, mais gère l'état du panneau
// localement (un seul panneau ouvert à la fois par serveur).
// Les schémas JSON Schema sont chargés au montage via GET /api/agents/{id}/mcp/{server}/tools.

import { useEffect, useState } from 'react'
import type { MCPToolDef, MCPToolTestResult } from '../lib/types'
import { api } from '../lib/api'
import { useT } from '../lib/i18n'
import { Pill } from '../lib/ui'

type Props = {
  agentId: string
  server: { name: string; url: string; tools?: string[] }
  results: Record<string, MCPToolTestResult>   // clé = toolName
  testingTools: Set<string>                     // tools en cours de test
  onTest: (toolName: string, args: Record<string, unknown>) => void
}

// Badge d'état cliquable calqué sur HealthBadge de ToolRow.
function McpTestBadge({ toolName, result, testing, onClick, t }: {
  toolName: string
  result?: MCPToolTestResult
  testing: boolean
  onClick: () => void
  t: (k: string, p?: Record<string, unknown>) => string
}) {
  let label = t('agents.detail.mcpToolTest')
  let cls = 'text-slate-500 dark:text-slate-400 bg-white dark:bg-ink-800 border-slate-200 dark:border-ink-600 hover:text-navy-700 dark:hover:text-navy-200 hover:border-navy-300'

  if (testing) {
    label = t('agents.detail.mcpToolTesting')
    cls = 'text-navy-600 dark:text-navy-300 bg-navy-50 dark:bg-navy-500/15 border-navy-200 dark:border-navy-500/30'
  } else if (result) {
    if (result.ok) {
      label = t('agents.detail.mcpToolOk')
      cls = 'text-emerald-700 dark:text-emerald-300 bg-emerald-50 dark:bg-emerald-500/15 border-emerald-200 dark:border-emerald-500/30'
    } else {
      label = t('agents.detail.mcpToolError')
      cls = 'text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-500/15 border-red-200 dark:border-red-500/30'
    }
  }

  return (
    <button
      onClick={onClick}
      disabled={testing}
      title={result?.detail}
      className={`mono inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-semibold transition disabled:opacity-70 ${cls}`}
    >
      {label}
    </button>
  )
}

// Génère un JSON d'args sample depuis un inputSchema JSON Schema.
function sampleJson(schema?: MCPToolDef['inputSchema']): string {
  if (!schema?.properties) return '{}'
  const required = new Set(schema.required ?? Object.keys(schema.properties))
  const obj: Record<string, unknown> = {}
  for (const [k, spec] of Object.entries(schema.properties)) {
    if (!required.has(k)) continue
    const t = spec?.type ?? 'string'
    if (t === 'integer' || t === 'number') obj[k] = 0
    else if (t === 'boolean') obj[k] = false
    else if (t === 'array') obj[k] = []
    else if (t === 'object') obj[k] = {}
    else obj[k] = ''
  }
  return JSON.stringify(obj, null, 2)
}

export function McpServerCard({ agentId, server, results, testingTools, onTest }: Props) {
  const t = useT()
  // toolName ouvert dans le panneau args ; null = tous fermés.
  const [openTool, setOpenTool] = useState<string | null>(null)
  // args JSON en édition, par toolName.
  const [argsMap, setArgsMap] = useState<Record<string, string>>({})
  // Schémas JSON Schema récupérés depuis le serveur MCP réel.
  const [toolDefs, setToolDefs] = useState<MCPToolDef[]>([])
  const [loadingDefs, setLoadingDefs] = useState(false)

  // Découverte des schémas MCP au montage du composant (tools/list réel).
  useEffect(() => {
    setLoadingDefs(true)
    api.listMcpTools(agentId, server.name)
      .then(({ tools }) => setToolDefs(tools))
      .catch(() => {/* silencieux si le serveur MCP est injoignable */})
      .finally(() => setLoadingDefs(false))
  }, [agentId, server.name])

  // Ouvre ou ferme le panneau d'un tool, prépopule les args depuis le schéma.
  const togglePanel = (toolName: string) => {
    setOpenTool((prev) => (prev === toolName ? null : toolName))
    setArgsMap((m) => {
      if (m[toolName] !== undefined) return m
      const def = toolDefs.find((d) => d.name === toolName)
      return { ...m, [toolName]: sampleJson(def?.inputSchema) }
    })
  }

  // Soumet le test après validation JSON des args.
  const runTest = (toolName: string) => {
    let args: Record<string, unknown> = {}
    try {
      const parsed = JSON.parse(argsMap[toolName] ?? '{}')
      if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
        args = parsed
      }
    } catch {
      // args reste {}
    }
    onTest(toolName, args)
  }

  return (
    <div className="rounded-lg border border-slate-200 dark:border-ink-600 bg-slate-50 dark:bg-ink-700 p-3">
      {/* En-tête serveur : nom + badge MCP ambré + URL */}
      <div className="flex items-center gap-2">
        <span className="mono text-sm font-bold text-slate-800 dark:text-slate-100">{server.name}</span>
        <Pill
          label="MCP"
          cls="text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-500/15 border-amber-200 dark:border-amber-500/30"
        />
        {loadingDefs && (
          <span className="mono text-[10px] text-slate-400 dark:text-slate-500 animate-pulse">
            chargement schémas…
          </span>
        )}
      </div>
      <div className="mono mt-0.5 text-[11px] text-slate-500 dark:text-slate-400 break-all">{server.url}</div>

      {/* Liste des tools : server.tools peut être absent si le JSONB ne le contient pas */}
      {(server.tools ?? []).length > 0 ? (
        <ul className="mt-2 space-y-1.5">
          {(server.tools ?? []).map((toolName) => {
            const isOpen = openTool === toolName
            const testing = testingTools.has(toolName)
            const result = results[toolName]
            const def = toolDefs.find((d) => d.name === toolName)

            return (
              <li key={toolName}>
                {/* Ligne tool : nom + description abrégée + badge test */}
                <div className="flex items-center gap-2">
                  <span
                    className="mono flex-1 truncate text-[12px] text-slate-700 dark:text-slate-200"
                    title={def?.description}
                  >
                    {toolName}
                  </span>
                  {def?.description && (
                    <span
                      className="truncate max-w-[120px] text-[10px] text-slate-400 dark:text-slate-500"
                      title={def.description}
                    >
                      {def.description}
                    </span>
                  )}
                  <McpTestBadge
                    toolName={toolName}
                    result={result}
                    testing={testing}
                    onClick={() => togglePanel(toolName)}
                    t={t}
                  />
                </div>

                {/* Panneau inline args + résultat (reste ouvert après le test) */}
                {isOpen && (
                  <div className="mt-1.5 rounded-lg border border-slate-200 dark:border-ink-600 bg-white dark:bg-ink-800 p-2">
                    {/* Hint des champs requis si le schéma est disponible */}
                    {def?.inputSchema?.required?.length ? (
                      <p className="mb-1 text-[10px] text-slate-400 dark:text-slate-500">
                        Requis : {def.inputSchema.required.join(', ')}
                      </p>
                    ) : null}
                    <textarea
                      rows={3}
                      value={argsMap[toolName] ?? '{}'}
                      onChange={(e) => setArgsMap((m) => ({ ...m, [toolName]: e.target.value }))}
                      placeholder={t('agents.detail.mcpToolArgsPlaceholder')}
                      spellCheck={false}
                      className="mono w-full resize-none rounded border border-slate-200 dark:border-ink-600 bg-slate-50 dark:bg-ink-700 px-2 py-1 text-[11px] text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-1 focus:ring-navy-400"
                    />
                    <button
                      onClick={() => runTest(toolName)}
                      disabled={testing}
                      className="mt-1.5 rounded-lg bg-navy-700 px-2.5 py-1 text-[11px] font-semibold text-white transition hover:bg-navy-800 disabled:opacity-50"
                    >
                      {testing ? t('agents.detail.mcpToolTesting') : t('agents.detail.mcpToolRun')}
                    </button>

                    {/* Résultat inline après exécution */}
                    {result && (
                      <div className="mt-2 space-y-1">
                        <div className="flex items-center gap-2 text-[11px]">
                          <span className={result.ok
                            ? 'font-semibold text-emerald-700 dark:text-emerald-300'
                            : 'font-semibold text-red-600 dark:text-red-400'}>
                            {result.ok ? t('agents.detail.mcpToolOk') : t('agents.detail.mcpToolError')}
                          </span>
                          <span className="mono text-slate-400 dark:text-slate-500">
                            {t('agents.detail.mcpToolResultMs', { ms: result.latency_ms.toFixed(0) })}
                          </span>
                        </div>
                        {result.detail && (
                          <p className="text-[10px] text-slate-500 dark:text-slate-400">{result.detail}</p>
                        )}
                        {result.result != null && (
                          <pre className="mono max-h-40 overflow-auto rounded bg-slate-50 dark:bg-ink-700 p-1.5 text-[10px] text-slate-700 dark:text-slate-200">
                            {JSON.stringify(result.result, null, 2)}
                          </pre>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      ) : (
        <p className="mt-1.5 text-[10px] text-slate-400 dark:text-slate-500">
          {t('agents.detail.mcpNoTool')}
        </p>
      )}
    </div>
  )
}
