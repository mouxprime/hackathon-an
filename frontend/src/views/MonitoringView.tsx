// Monitoring — page unique regroupant l'ancien JetStream + les vues opérationnelles
// (Tâches, FSM, Mémoire, Bus, Settings) en sous-onglets. Fenêtre sur la machinerie
// d'orchestration : streams, états FSM, bus.

import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { useT } from '../lib/i18n'
import { BusView } from './BusView'
import { ConvStateView } from './ConvStateView'
import { JetstreamView } from './JetstreamView'
import { MemoryView } from './MemoryView'
import { SettingsView } from './SettingsView'
import { TasksView } from './TasksView'

const TABS = [
  { to: 'jetstream', labelKey: 'monitoring.tabs.jetstream' },
  { to: 'tasks', labelKey: 'monitoring.tabs.tasks' },
  { to: 'conv', labelKey: 'monitoring.tabs.fsm' },
  { to: 'memory', labelKey: 'monitoring.tabs.memory' },
  { to: 'bus', labelKey: 'monitoring.tabs.bus' },
  { to: 'settings', labelKey: 'monitoring.tabs.settings' },
]

// Enveloppe scrollable + padding pour les vues qui comptaient sur leur conteneur.
function Padded({ children }: { children: React.ReactNode }) {
  return <div className="h-full overflow-y-auto p-4">{children}</div>
}

export function MonitoringView() {
  const t = useT()
  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 border-b border-slate-200 dark:border-ink-600 bg-white dark:bg-ink-800 px-4 pt-3">
        <div>
          <h1 className="text-base font-bold tracking-tight text-navy-700 dark:text-navy-200">{t('monitoring.tabs.title')}</h1>
          <p className="text-[11px] text-slate-400 dark:text-slate-500">
            {t('monitoring.tabs.description')}
          </p>
        </div>
        <nav className="mt-2 flex gap-1">
          {TABS.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              className={({ isActive }) =>
                `rounded-t-lg px-3 py-1.5 text-xs font-semibold transition ${
                  isActive
                    ? 'border-b-2 border-navy-600 bg-slate-50 dark:bg-ink-700 text-navy-700 dark:text-navy-200'
                    : 'border-b-2 border-transparent text-slate-500 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-ink-700 hover:text-navy-700'
                }`
              }
            >
              {t(tab.labelKey)}
            </NavLink>
          ))}
        </nav>
      </div>

      <div className="flex-1 overflow-hidden">
        <Routes>
          <Route path="/" element={<Navigate to="jetstream" replace />} />
          {/* JetStream gère sa propre grille h-full + scrolls internes. */}
          <Route path="jetstream" element={<JetstreamView />} />
          <Route path="tasks" element={<Padded><TasksView /></Padded>} />
          <Route path="conv" element={<Padded><ConvStateView /></Padded>} />
          <Route path="memory" element={<Padded><MemoryView /></Padded>} />
          <Route path="bus" element={<Padded><BusView /></Padded>} />
          <Route path="settings" element={<Padded><SettingsView /></Padded>} />
        </Routes>
      </div>
    </div>
  )
}
