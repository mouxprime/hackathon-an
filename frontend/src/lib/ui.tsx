// Helpers d'affichage partagés : palettes d'états, formatage du temps, petits composants.

import type { ReactNode } from 'react'

// Couleurs des états de la FSM (conversation) — teintes claires + variantes nuit.
// En mode nuit : fonds translucides (-500/15) + texte clair (-300) sur surfaces ink.
export const FSM_COLORS: Record<string, string> = {
  IDLE: 'text-slate-600 bg-slate-100 border-slate-300 dark:text-slate-300 dark:bg-ink-700 dark:border-ink-500',
  PLANNING: 'text-navy-700 bg-navy-50 border-navy-200 dark:text-navy-200 dark:bg-navy-500/15 dark:border-navy-500/30',
  AWAITING_PLAN: 'text-navy-700 bg-navy-50 border-navy-200 dark:text-navy-200 dark:bg-navy-500/15 dark:border-navy-500/30',
  AWAITING_SUBAGENTS: 'text-amber-700 bg-amber-50 border-amber-200 dark:text-amber-300 dark:bg-amber-500/15 dark:border-amber-500/30',
  WRITING_REPORT: 'text-violet-700 bg-violet-50 border-violet-200 dark:text-violet-300 dark:bg-violet-500/15 dark:border-violet-500/30',
  DONE: 'text-emerald-700 bg-emerald-50 border-emerald-200 dark:text-emerald-300 dark:bg-emerald-500/15 dark:border-emerald-500/30',
  FAILED: 'text-red-700 bg-red-50 border-red-200 dark:text-red-300 dark:bg-red-500/15 dark:border-red-500/30',
}

// Couleurs des statuts de tâche.
export const STATUS_COLORS: Record<string, string> = {
  pending: 'text-slate-600 bg-slate-100 border-slate-300 dark:text-slate-300 dark:bg-ink-700 dark:border-ink-500',
  running: 'text-navy-700 bg-navy-50 border-navy-200 dark:text-navy-200 dark:bg-navy-500/15 dark:border-navy-500/30',
  retrying: 'text-amber-700 bg-amber-50 border-amber-200 dark:text-amber-300 dark:bg-amber-500/15 dark:border-amber-500/30',
  done: 'text-emerald-700 bg-emerald-50 border-emerald-200 dark:text-emerald-300 dark:bg-emerald-500/15 dark:border-emerald-500/30',
  failed: 'text-red-700 bg-red-50 border-red-200 dark:text-red-300 dark:bg-red-500/15 dark:border-red-500/30',
  timeout: 'text-red-700 bg-red-50 border-red-200 dark:text-red-300 dark:bg-red-500/15 dark:border-red-500/30',
  cancelled: 'text-slate-500 bg-slate-100 border-slate-200 dark:text-slate-400 dark:bg-ink-700 dark:border-ink-600',
}

// Couleur d'un pod selon son état.
export const POD_COLORS: Record<string, string> = {
  idle: 'text-emerald-700 bg-emerald-50 border-emerald-200 dark:text-emerald-300 dark:bg-emerald-500/15 dark:border-emerald-500/30',
  busy: 'text-navy-700 bg-navy-50 border-navy-200 dark:text-navy-200 dark:bg-navy-500/15 dark:border-navy-500/30',
  starting: 'text-slate-600 bg-slate-100 border-slate-300 dark:text-slate-300 dark:bg-ink-700 dark:border-ink-500',
}

// Emoji par discipline d'agent — partagé par la palette du builder, les cartes
// Agents et le parcours de l'IA. Disciplines métier : VOTES / DEPUTES / LOIS.
export const DISCIPLINE_EMOJI: Record<string, string> = {
  VOTES: '🗳️', DEPUTES: '🧑‍💼', LOIS: '📜',
}

// Icône d'un agent d'après sa discipline (repli robot si inconnue).
export function agentEmoji(discipline?: string): string {
  return DISCIPLINE_EMOJI[(discipline || '').toUpperCase()] || '🤖'
}

// Libellé affiché d'un agent connu (nom produit, SANS emoji — l'emoji est rendu
// séparément par `agentEmoji`/`agentDisplay`, sinon il apparaît en double).
// Clé acceptée : id technique (`agent_votes`) OU discipline (`votes`).
const AGENT_LABELS: Record<string, string> = {
  agent_votes: 'Agent Votes',
  votes: 'Agent Votes',
  agent_deputes: 'Agent Députés',
  deputes: 'Agent Députés',
  agent_lois: 'Agent Lois',
  lois: 'Agent Lois',
}
export function agentLabel(idOrDiscipline?: string): string {
  const key = (idOrDiscipline || '').toLowerCase()
  return AGENT_LABELS[key] || (idOrDiscipline || '').toUpperCase()
}

// Emoji + libellé en une seule chaîne — pour les sites qui n'affichent pas
// d'avatar séparé (pyramide d'exécution, cartes d'agent du fil).
export function agentDisplay(idOrDiscipline?: string): string {
  const disc = (idOrDiscipline || '').toLowerCase().replace(/^agent_/, '')
  const emoji = DISCIPLINE_EMOJI[disc.toUpperCase()]
  const label = agentLabel(idOrDiscipline)
  return emoji ? `${emoji} ${label}` : label
}

// Libellé citoyen d'un état FSM de l'orchestrateur — le « parcours de l'IA »
// doit être lisible sans jargon. L'état technique reste disponible pour le
// `title=` (info-bulle) des pills.
const FSM_LABELS_FR: Record<string, string> = {
  IDLE: 'En attente de votre question',
  PLANNING: 'Je prépare ma réponse',
  AWAITING_PLAN: 'Plan proposé',
  AWAITING_SUBAGENTS: "J'interroge les données de l'Assemblée",
  WRITING_REPORT: 'Je rédige en français clair',
  DONE: 'Réponse prête',
  FAILED: "Je n'ai pas pu répondre",
}
export function fsmLabel(state?: string | null): string {
  if (!state) return ''
  return FSM_LABELS_FR[state] || state
}

// Couleurs du statut d'agent (Healthy / Error / Idle) — carte Agents.
export const STATUS_AGENT_CLS: Record<string, string> = {
  healthy: 'text-emerald-700 bg-emerald-50 border-emerald-200 dark:text-emerald-300 dark:bg-emerald-500/15 dark:border-emerald-500/30',
  error: 'text-red-700 bg-red-50 border-red-200 dark:text-red-300 dark:bg-red-500/15 dark:border-red-500/30',
  idle: 'text-slate-600 bg-slate-100 border-slate-300 dark:text-slate-300 dark:bg-ink-700 dark:border-ink-500',
}

// Âge lisible d'un horodatage ISO (« 12s », « 3min », « 2h »).
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return '—'
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 0) return '0s'
  if (s < 60) return `${Math.floor(s)}s`
  if (s < 3600) return `${Math.floor(s / 60)}min`
  return `${Math.floor(s / 3600)}h`
}

// Vrai si l'horodatage est plus récent que `maxS` secondes (fraîcheur d'un heartbeat).
export function isFresh(iso: string | null | undefined, maxS: number): boolean {
  if (!iso) return false
  return (Date.now() - new Date(iso).getTime()) / 1000 < maxS
}

// Badge coloré générique.
export function Pill({ label, cls }: { label: string; cls?: string }) {
  return (
    <span
      className={`mono inline-block rounded border px-1.5 py-0.5 text-[11px] font-medium ${
        cls || 'text-slate-600 bg-slate-100 border-slate-300 dark:text-slate-300 dark:bg-ink-700 dark:border-ink-500'
      }`}
    >
      {label}
    </span>
  )
}

// Conteneur de panneau avec titre (carte blanche + ombre douce).
export function Panel({ title, right, children }: { title: string; right?: ReactNode; children: ReactNode }) {
  return (
    <div className="card">
      <div className="flex items-center justify-between border-b border-slate-200 px-3 py-2 dark:border-ink-600">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">{title}</h2>
        {right}
      </div>
      <div className="p-3">{children}</div>
    </div>
  )
}

// Barre de progression 0 → 1.
export function Bar({ pct, cls }: { pct: number; cls?: string }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded bg-slate-200 dark:bg-ink-700">
      <div
        className={`h-full rounded transition-all duration-300 ${cls || 'bg-navy-600 dark:bg-navy-500'}`}
        style={{ width: `${Math.max(0, Math.min(1, pct)) * 100}%` }}
      />
    </div>
  )
}
