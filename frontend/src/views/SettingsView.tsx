// Réglages système accessibles depuis l'onglet Monitoring > Réglages.
// Pour l'instant : seuil de contexte (maxTokens) qui pilote la couleur du badge.

import { useT } from '../lib/i18n'
import { useContextStore } from '../lib/store'

export function SettingsView() {
  const t = useT()
  const { maxTokens, setMaxTokens } = useContextStore()

  // Formate un nombre de tokens en libellé lisible (ex. 100 000).
  const fmt = (n: number) =>
    n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : `${Math.round(n / 1000)}k`

  return (
    <div className="max-w-lg space-y-6">
      <div>
        <h2 className="text-base font-bold text-navy-700 dark:text-navy-200">
          {t('monitoring.settings.title')}
        </h2>
        <p className="mt-0.5 text-[11px] text-slate-400 dark:text-slate-500">
          {t('monitoring.settings.description')}
        </p>
      </div>

      {/* Seuil de contexte */}
      <div className="card p-4 space-y-3">
        <div>
          <label className="text-xs font-semibold text-slate-700 dark:text-slate-200">
            {t('monitoring.settings.maxTokens.label')}
          </label>
          <p className="text-[11px] text-slate-400 dark:text-slate-500 mt-0.5">
            {t('monitoring.settings.maxTokens.hint')}
          </p>
        </div>

        {/* Slider */}
        <div className="space-y-1.5">
          <input
            type="range"
            min={10_000}
            max={256_000}
            step={10_000}
            value={maxTokens}
            onChange={(e) => setMaxTokens(Number(e.target.value))}
            className="w-full accent-navy-600"
          />
          <div className="flex justify-between text-[10px] text-slate-400 dark:text-slate-500">
            <span>10k</span>
            <span className="font-semibold text-navy-700 dark:text-navy-200">{fmt(maxTokens)}</span>
            <span>256k</span>
          </div>
        </div>

        {/* Input numérique précis */}
        <div className="flex items-center gap-2">
          <input
            type="number"
            min={1_000}
            max={256_000}
            step={1_000}
            value={maxTokens}
            onChange={(e) => setMaxTokens(Number(e.target.value))}
            className="field w-40 mono text-sm"
          />
          <span className="text-[11px] text-slate-500 dark:text-slate-400">
            {t('monitoring.settings.maxTokens.unit')}
          </span>
        </div>
      </div>
    </div>
  )
}
