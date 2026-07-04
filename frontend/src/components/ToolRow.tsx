// Carte d'outil compacte (« toast ») dans la page détail d'un agent : nom + badge
// source, puis badge de santé (test réel du tool) + action pause. Le test est piloté
// par le parent (AgentDetailView) pour que « Test All » puisse rejouer chaque carte.
// Affichée en grille 3 colonnes. Sans description.

import { api } from '../lib/api'
import type { Tool, ToolTestResult } from '../lib/types'
import { useT } from '../lib/i18n'
import { Pill } from '../lib/ui'

const SOURCE_CLS: Record<string, string> = {
  native: 'text-slate-600 dark:text-slate-300 bg-slate-100 dark:bg-ink-600 border-slate-300 dark:border-ink-600',
  mcp: 'text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-500/15 border-amber-200 dark:border-amber-500/30',
  a2a: 'text-violet-700 dark:text-violet-300 bg-violet-50 dark:bg-violet-500/15 border-violet-200 dark:border-violet-500/30',
}

type Props = {
  tool: Tool
  onChanged: () => void
  // Test piloté par le parent (partagé avec « Test All »).
  result?: ToolTestResult
  testing?: boolean
  onTest: () => void
}

const ICON = 'h-3.5 w-3.5'

// Badge de santé cliquable : déclenche le test réel du tool et reflète son verdict.
function HealthBadge({ result, testing, onTest, t }: {
  result?: ToolTestResult; testing?: boolean; onTest: () => void
  t: (k: string, p?: Record<string, unknown>) => string
}) {
  let label = t('agents.tool.test')
  let cls = 'text-slate-500 dark:text-slate-400 bg-white dark:bg-ink-800 border-slate-200 dark:border-ink-600 hover:text-navy-700 dark:hover:text-navy-200 hover:border-navy-300'
  let title = t('agents.tool.testTitle')
  if (testing) {
    label = `… ${t('agents.tool.testing')}`
    cls = 'text-navy-600 dark:text-navy-300 bg-navy-50 dark:bg-navy-500/15 border-navy-200 dark:border-navy-500/30'
  } else if (result) {
    title = result.detail
    if (result.skipped) {
      label = t('agents.tool.na')
      cls = 'text-slate-500 dark:text-slate-400 bg-slate-100 dark:bg-ink-600 border-slate-300 dark:border-ink-600'
    } else if (result.ok) {
      label = `✓ ${t('agents.tool.healthy')}`
      cls = 'text-emerald-700 dark:text-emerald-300 bg-emerald-50 dark:bg-emerald-500/15 border-emerald-200 dark:border-emerald-500/30'
    } else {
      label = `✗ ${t('agents.tool.unhealthy')}`
      cls = 'text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-500/15 border-red-200 dark:border-red-500/30'
    }
  }
  return (
    <button
      onClick={onTest}
      disabled={testing}
      title={title}
      className={`mono inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-semibold transition disabled:opacity-70 ${cls}`}
    >
      {label}
    </button>
  )
}

export function ToolRow({ tool, onChanged, result, testing, onTest }: Props) {
  const t = useT()
  const togglePause = async () => {
    await api.patchTool(tool.id, { paused: !tool.paused })
    onChanged()
  }

  return (
    // Format « toast » compact (s'affiche 3 par ligne) : nom + source en haut,
    // badge de santé + pause en bas. Pas de description — on garde la carte dense.
    <div className="rounded-lg border border-slate-200 dark:border-ink-600 bg-slate-50 dark:bg-ink-700 px-2.5 py-2">
      <div className="flex items-center gap-1.5">
        <span className="mono truncate text-[13px] font-semibold text-slate-800 dark:text-slate-100">{tool.name}</span>
        <Pill label={tool.source} cls={SOURCE_CLS[tool.source]} />
      </div>
      <div className="mt-1.5 flex items-center gap-1.5">
        {tool.paused && <Pill label={t('agents.tool.pause')} cls="text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-500/15 border-amber-200 dark:border-amber-500/30" />}
        <HealthBadge result={result} testing={testing} onTest={onTest} t={t} />

        <div className="ml-auto flex shrink-0 items-center gap-1">
          {/* Pause / Reprendre */}
          <button
            onClick={togglePause}
            title={tool.paused ? t('agents.tool.resume') : t('agents.tool.pauseAction')}
            aria-label={tool.paused ? t('agents.tool.resume') : t('agents.tool.pauseAction')}
            className={`flex h-7 w-7 items-center justify-center rounded-md border transition ${
              tool.paused
                ? 'border-emerald-200 dark:border-emerald-500/30 bg-emerald-50 dark:bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-100'
                : 'border-amber-200 dark:border-amber-500/30 bg-amber-50 dark:bg-amber-500/15 text-amber-700 dark:text-amber-300 hover:bg-amber-100'}`}
          >
            {tool.paused ? (
              <svg viewBox="0 0 24 24" className={ICON} fill="currentColor"><path d="M8 5v14l11-7z" /></svg>
            ) : (
              <svg viewBox="0 0 24 24" className={ICON} fill="currentColor"><path d="M6 4h4v16H6zM14 4h4v16h-4z" /></svg>
            )}
          </button>
        </div>
      </div>
    </div>
  )
}
