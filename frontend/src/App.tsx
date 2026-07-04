// Console « Hémicycle » — coquille de l'application : header + sous-bandeau + router.
//
// Charte Assemblée nationale :
//   - HEADER bleu Assemblée #223061 : logo hémicycle + wordmark « Hémicycle » à
//     gauche, compte utilisateur + drapeau + état des services à droite.
//   - SOUS-BANDEAU or #8a6420 : titre de la section active à gauche, les 4
//     icônes de navigation à droite (pas de rail vertical → toute la largeur
//     est rendue au contenu).
//   - Contenu sur canvas clair / bleu-nuit en mode sombre.

import { useEffect, type ReactNode } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { AgentDetailView } from './views/AgentDetailView'
import { AgentsView } from './views/AgentsView'
import { DashboardView } from './views/DashboardView'
import { MonitoringView } from './views/MonitoringView'
import { WorkflowsView } from './views/WorkflowsView'
import { HeaderStatus } from './components/HeaderStatus'
import { FlagToggle } from './components/FlagToggle'
import { UserAccount } from './components/UserAccount'
import { useConvStore } from './lib/store'
import { Toaster } from './lib/toast'
import { useI18n, useT } from './lib/i18n'

// Onglets de navigation : `key` pilote le libellé (tooltip), `section` le
// titre du sous-bandeau, `icon` le pictogramme. Les 4 sont TOUJOURS visibles,
// rendus à droite du sous-bandeau or (le rail vertical a été supprimé).
const NAV: { to: string; key: string; section: string; icon: ReactNode }[] = [
  { to: '/dashboard', key: 'nav.dashboard', section: 'header.sectionDashboard', icon: <HemicycleIcon /> },
  { to: '/agents', key: 'nav.agents', section: 'header.sectionAgents', icon: <AgentsRailIcon /> },
  { to: '/monitoring', key: 'nav.monitoring', section: 'header.sectionMonitoring', icon: <PulseIcon /> },
  { to: '/workflows', key: 'nav.workflows', section: 'header.sectionWorkflows', icon: <FlowIcon /> },
]

export function App() {
  const navigate = useNavigate()
  const t = useT()
  const lang = useI18n((s) => s.lang)
  const resetBoard = useConvStore((s) => s.resetBoard)
  // Reflète la langue courante sur <html lang> (accessibilité + sélection navigateur).
  useEffect(() => {
    document.documentElement.lang = lang
  }, [lang])
  // Logo hémicycle = bouton « accueil » : ramène au dashboard avec un tableau propre.
  const goHome = () => {
    resetBoard()
    navigate('/dashboard')
  }
  return (
    <div className="flex h-full flex-col">
      {/* Header global bleu Assemblée : logo + wordmark gauche · compte/drapeau/status droite */}
      <header className="flex shrink-0 items-center gap-3 bg-navy-700 px-4 py-2.5 shadow-md">
        <button
          type="button"
          onClick={goHome}
          className="flex items-center gap-2.5 rounded-lg text-left transition hover:opacity-90 focus:outline-none focus-visible:ring-2 focus-visible:ring-white/40"
          title={t('header.home')}
        >
          <HemicycleLogo className="h-6 w-auto text-white" />
          <span className="hidden border-l border-white/20 pl-2.5 text-sm font-semibold tracking-wide text-white sm:block">
            {t('header.appName')}
          </span>
        </button>

        <div className="ml-auto flex items-center gap-3">
          <UserAccount />
          <span className="h-4 w-px bg-white/20" />
          <FlagToggle />
          <HeaderStatus />
        </div>
      </header>

      {/* Sous-bandeau or : titre de la section + icônes de navigation */}
      <SubHeader />

      {/* Vue active (router) — pleine largeur, le rail vertical a été supprimé */}
      <main className="min-h-0 min-w-0 flex-1 overflow-hidden bg-[var(--bg)] dark:bg-ink-900">
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<DashboardView />} />
          <Route path="/agents" element={<AgentsView />} />
          <Route path="/agents/:agentId" element={<AgentDetailView />} />
          <Route path="/monitoring/*" element={<MonitoringView />} />
          <Route path="/workflows/*" element={<WorkflowsView />} />
          {/* Anciennes routes → Monitoring (rétro-compat des liens). */}
          <Route path="/jetstream" element={<Navigate to="/monitoring/jetstream" replace />} />
          <Route path="/operations/*" element={<Navigate to="/monitoring" replace />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </main>

      {/* Toasts globaux — montés une fois, accessibles via useToast() partout. */}
      <Toaster />
    </div>
  )
}

// Sous-bandeau or : titre de la section active à gauche, navigation (les 4
// icônes, toujours visibles) à droite — remplace l'ancien rail vertical.
function SubHeader() {
  const t = useT()
  const { pathname } = useLocation()
  const active = NAV.find((n) => pathname.startsWith(n.to)) ?? NAV[0]
  return (
    <div className="flex shrink-0 items-center gap-2.5 bg-teal-500 px-4 py-1.5 dark:bg-teal-600">
      <HemicycleIcon className="h-5 w-5 text-white" />
      <span className="text-sm font-semibold tracking-wide text-white">{t(active.section)}</span>
      <nav className="ml-auto flex items-center gap-1">
        {NAV.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            title={t(n.key)}
            aria-label={t(n.key)}
            className={({ isActive }) =>
              `flex h-8 w-8 items-center justify-center rounded-lg transition ${
                isActive
                  ? 'bg-white/25 text-white'
                  : 'text-white/65 hover:bg-white/15 hover:text-white'
              }`
            }
          >
            <span className="h-5 w-5">{n.icon}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  )
}

// --- Icônes inline (style WidgetIcon : stroke currentColor, 24×24) ---

// Logo « Hémicycle » : demi-cercle de sièges stylisé (3 arcs + tribune).
function HemicycleLogo({ className = 'h-6 w-6' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden>
      <path d="M3 19a9 9 0 0 1 18 0" />
      <path d="M6.5 19a5.5 5.5 0 0 1 11 0" />
      <path d="M10 19a2 2 0 0 1 4 0" />
      <path d="M2 21.5h20" strokeWidth="2" />
    </svg>
  )
}

// Variante icône (rail + sous-bandeau) : mêmes arcs, trait plus léger.
function HemicycleIcon({ className = 'h-5 w-5' }: { className?: string }) {
  return <HemicycleLogo className={className} />
}

function AgentsRailIcon({ className = 'h-5 w-5' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="8" r="3.2" />
      <path d="M5 19c0-3.3 3.1-5 7-5s7 1.7 7 5" />
    </svg>
  )
}

function PulseIcon({ className = 'h-5 w-5' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 12h4l2.5-6 4 13 2.5-7H21" />
    </svg>
  )
}

function FlowIcon({ className = 'h-5 w-5' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="7" height="5" rx="1.2" />
      <rect x="14" y="15" width="7" height="5" rx="1.2" />
      <path d="M6.5 9v4.5a2 2 0 0 0 2 2H14" />
    </svg>
  )
}
