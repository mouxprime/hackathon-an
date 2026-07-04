// Store global léger (Zustand) — partage la conversation active entre les
// vues /dashboard, /operations et la barre latérale. Tout le reste reste local.

import { create } from 'zustand'

// Clé localStorage du tableau de bord (widgets actifs + positions). Définie ici
// pour que `resetBoard` puisse l'effacer même si le DashboardView est démonté.
export const DASHBOARD_LS_KEY = 'hemicycle.dashboard.v3'

type ConvStore = {
  convId: string | null
  setConvId: (id: string | null) => void
  // Nettoyage du tableau : `resetBoard` efface l'état persisté ET bumpe ce
  // compteur, que le DashboardView monté observe pour vider en direct. Déclenché
  // par « nouvelle investigation » et par le clic sur le logo.
  boardResetSeq: number
  resetBoard: () => void
}

export const useConvStore = create<ConvStore>((set) => ({
  convId: null,
  setConvId: (id) => set({ convId: id }),
  boardResetSeq: 0,
  resetBoard: () => {
    // Efface le persisté (couvre le cas DashboardView démonté, ex. clic logo
    // depuis /agents) ; le bump rafraîchit la vue si elle est déjà montée.
    try { localStorage.removeItem(DASHBOARD_LS_KEY) } catch { /* quota / mode privé */ }
    set((s) => ({ boardResetSeq: s.boardResetSeq + 1 }))
  },
}))

// --- Channel actif (mode salon de groupe) -------------------------------------

// L'utilisateur courant. Pas d'auth réelle : identité mono-utilisateur partagée,
// utilisée comme auteur des posts (author_id) et affichée comme « moi » (bulles
// à droite).
export const CURRENT_USER = { id: 'analyst-1' }

// Libellé de rôle (chip) affiché pour l'auteur, DÉRIVÉ du salon où l'on poste :
// « g4 » → « Analyste (G4) ». L'identité (author_id) reste mono-utilisateur ; seul
// le libellé de cellule suit le salon courant. Hors salon (chat privé
// d'investigation, pas de cellule) → « Analyste » générique.
export const authorRoleFor = (channelId?: string | null): string =>
  channelId ? `Analyste (${channelId.toUpperCase()})` : 'Analyste'

type ChannelStore = {
  // `channelId != null` ⇒ le panneau principal affiche ce salon au lieu de
  // l'investigation. `null` ⇒ retour au chat d'investigation (ChatPanel).
  channelId: string | null
  setChannelId: (id: string | null) => void
}

export const useChannelStore = create<ChannelStore>((set) => ({
  channelId: null,
  setChannelId: (id) => set({ channelId: id }),
}))

// --- Seuil de contexte (badge tokens) ----------------------------------------

const CONTEXT_TOKENS_KEY = 'hemicycle.context.maxTokens.v1'
const CONTEXT_TOKENS_DEFAULT = 100_000
// Cap système : Hémicycle ne dépasse JAMAIS ce contexte d'input total (FSM + LLM).
const CONTEXT_TOKENS_MAX = 256_000

function initialMaxTokens(): number {
  try {
    const raw = localStorage.getItem(CONTEXT_TOKENS_KEY)
    if (raw) {
      const n = Number(raw)
      // Clamp au cap système pour rattraper les valeurs persistées au-delà.
      if (Number.isFinite(n) && n > 0) return Math.min(CONTEXT_TOKENS_MAX, n)
    }
  } catch { /* ignore */ }
  return CONTEXT_TOKENS_DEFAULT
}

type ContextStore = {
  maxTokens: number
  setMaxTokens: (n: number) => void
}

// Store du seuil de tokens : contrôle la couleur du badge, persisté localement.
export const useContextStore = create<ContextStore>((set) => ({
  maxTokens: initialMaxTokens(),
  setMaxTokens: (n) => {
    const clamped = Math.max(1000, Math.min(CONTEXT_TOKENS_MAX, n))
    try { localStorage.setItem(CONTEXT_TOKENS_KEY, String(clamped)) } catch { /* quota */ }
    set({ maxTokens: clamped })
  },
}))

// --- Thème clair / nuit -----------------------------------------------------

export type Theme = 'light' | 'dark'

const THEME_KEY = 'hemicycle.theme.v1'

// Thème initial : préférence sauvegardée, sinon préférence système (prefers-color-scheme).
function initialTheme(): Theme {
  if (typeof window === 'undefined') return 'light'
  const saved = localStorage.getItem(THEME_KEY)
  if (saved === 'light' || saved === 'dark') return saved
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

// Applique le thème au DOM (classe `.dark` sur <html>) et le persiste.
export function applyTheme(theme: Theme): void {
  if (typeof document === 'undefined') return
  document.documentElement.classList.toggle('dark', theme === 'dark')
  try {
    localStorage.setItem(THEME_KEY, theme)
  } catch {
    /* localStorage indisponible (mode privé) : on garde juste l'état mémoire */
  }
}

type ThemeStore = {
  theme: Theme
  toggle: () => void
  setTheme: (t: Theme) => void
}

// Store du thème : init depuis localStorage/système, applique au DOM à chaque changement.
export const useThemeStore = create<ThemeStore>((set, get) => ({
  theme: initialTheme(),
  toggle: () => {
    const next: Theme = get().theme === 'dark' ? 'light' : 'dark'
    applyTheme(next)
    set({ theme: next })
  },
  setTheme: (t) => {
    applyTheme(t)
    set({ theme: t })
  },
}))

// --- Store du widget éditeur de texte ----------------------------------------
//
// Rôle double :
//   1. relayer le rapport IA streamé (DashboardView → texteditor actif),
//   2. PERSISTER le contenu de chaque éditeur (par instanceId) en RAM, pour
//      qu'il survive aux unmounts (passage en plein écran, repli/restoration
//      depuis la taskbar, déplacement entre canvas et overlay fullscreen).
//
// Sans (2), TipTap perd toute son arbre ProseMirror à chaque démontage et le
// rapport rédigé disparaît à la première manipulation de fenêtre.
//
// (1) passe par `pendingReportText` (le markdown COMPLET du rapport à un instant
// donné), pas par une file de deltas : l'éditeur REMPLACE son contenu de façon
// idempotente. L'ancien mécanisme de chunks perdait des tokens quand l'éditeur
// montait après le début du streaming (course de montage) et empêchait le filet
// canonique de se déclencher.

type EditorContent = { html: string; markdown: string }

type TextEditorStore = {
  // ID de l'instance texteditor active (celle qui reçoit le rapport).
  activeEditorId: string | null
  // Markdown COMPLET du rapport (live au fil du streaming, puis canonique) à
  // refléter dans l'éditeur actif. `null` = rien à appliquer.
  pendingReportText: string | null
  // Snapshot par instance : HTML (pour réhydrater TipTap) + markdown accumulé.
  contentByEditor: Record<string, EditorContent>
  setActiveEditorId: (id: string | null) => void
  // Demande à l'éditeur actif d'afficher ce texte complet (idempotent).
  setReportText: (text: string) => void
  // Lit et consomme le texte de rapport en attente (appelé par le widget).
  takeReportText: () => string | null
  // Sauvegarde / lecture / nettoyage du snapshot par éditeur.
  saveContent: (id: string, content: EditorContent) => void
  getContent: (id: string) => EditorContent | null
  clearContent: (id: string) => void
}

export const useTextEditorStore = create<TextEditorStore>((set, get) => ({
  activeEditorId: null,
  pendingReportText: null,
  contentByEditor: {},
  setActiveEditorId: (id) => set({ activeEditorId: id }),
  setReportText: (text) => set({ pendingReportText: text }),
  takeReportText: () => {
    const t = get().pendingReportText
    if (t !== null) set({ pendingReportText: null })
    return t
  },
  saveContent: (id, content) =>
    set((s) => ({ contentByEditor: { ...s.contentByEditor, [id]: content } })),
  getContent: (id) => get().contentByEditor[id] ?? null,
  clearContent: (id) =>
    set((s) => {
      const next = { ...s.contentByEditor }
      delete next[id]
      return { contentByEditor: next }
    }),
}))

// --- Store du widget lecteur de fichier --------------------------------------
//
// Quand l'analyste clique le badge 📎 d'une pièce jointe, le DashboardView spawn
// un widget `viewfile` et dépose ICI son descripteur (indexé par instanceId). Le
// widget lit le descripteur au montage, va chercher les octets via le gateway
// (GET /api/attachments/{id}/content) et les rend. `resize` est un canal que le
// DashboardView enregistre une fois (setResizer) pour que le widget ajuste sa
// taille à l'ASPECT du fichier une fois l'image/le média mesuré (paysage→large,
// portrait→haut).

export type FileDescriptor = { attachmentId: string; filename: string; contentType?: string }

type FileViewerStore = {
  files: Record<string, FileDescriptor>
  setFile: (instanceId: string, d: FileDescriptor) => void
  getFile: (instanceId: string) => FileDescriptor | null
  clearFile: (instanceId: string) => void
  resize: ((instanceId: string, w: number, h: number) => void) | null
  setResizer: (fn: (instanceId: string, w: number, h: number) => void) => void
}

export const useFileViewerStore = create<FileViewerStore>((set, get) => ({
  files: {},
  setFile: (instanceId, d) => set((s) => ({ files: { ...s.files, [instanceId]: d } })),
  getFile: (instanceId) => get().files[instanceId] ?? null,
  clearFile: (instanceId) =>
    set((s) => {
      const next = { ...s.files }
      delete next[instanceId]
      return { files: next }
    }),
  resize: null,
  setResizer: (fn) => set({ resize: fn }),
}))

// --- Streaming des rapports en cours de rédaction ----------------------------
//
// L'orchestrateur publie des `Progress(kind="report_chunk")` pendant la
// rédaction LLM ; le front les accumule ICI, indexés par `stream_id`
// (= widget_id du futur widget `report`). Le ReportWidget lit ce buffer tant
// que le streaming n'est pas `done` et bascule ensuite sur `data.text` du
// widget canonique (qui survit au refresh via l'event-sourcing).

type ReportStream = { text: string; done: boolean }

type ReportStreamStore = {
  buffers: Record<string, ReportStream>
  push: (streamId: string, delta: string, done: boolean) => void
  clear: (streamId: string) => void
}

export const useReportStreamStore = create<ReportStreamStore>((set) => ({
  buffers: {},
  push: (streamId, delta, done) =>
    set((s) => {
      const cur = s.buffers[streamId]
      // Re-prep (item 5.2) : si le stream était déjà terminé (done=true), le nouvel
      // arrivage démarre un NOUVEAU cycle — on réinitialise le buffer plutôt que
      // de concaténer. Pendant un streaming en cours (done=false), on accumule normalement.
      const base = cur && !cur.done ? cur : { text: '', done: false }
      return {
        buffers: {
          ...s.buffers,
          [streamId]: { text: base.text + (delta || ''), done: base.done || done },
        },
      }
    }),
  clear: (streamId) =>
    set((s) => {
      const next = { ...s.buffers }
      delete next[streamId]
      return { buffers: next }
    }),
}))
