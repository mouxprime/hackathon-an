// Système de toasts global — store Zustand léger + composant <Toaster /> à monter
// une fois dans App. Utilisation depuis n'importe quel composant :
//
//   import { useToast } from '../lib/toast'
//   const toast = useToast()
//   toast.success("Agent déployé")
//   toast.error("Build échoué", "voir les logs")
//
// Auto-dismiss après 5 s par défaut ; click sur le toast le ferme aussi.

import { create } from 'zustand'

export type ToastKind = 'success' | 'error' | 'info' | 'warn'

export type Toast = {
  id: number
  kind: ToastKind
  title: string
  detail?: string
}

type ToastStore = {
  items: Toast[]
  push: (t: Omit<Toast, 'id'>) => void
  dismiss: (id: number) => void
}

let nextId = 1

const useToastStore = create<ToastStore>((set) => ({
  items: [],
  push: (t) => {
    const id = nextId++
    set((s) => ({ items: [...s.items, { ...t, id }] }))
    // Auto-dismiss après 5 s (warn/error : 7 s, restent plus visibles).
    const ttl = t.kind === 'error' || t.kind === 'warn' ? 7000 : 5000
    setTimeout(() => set((s) => ({ items: s.items.filter((x) => x.id !== id) })), ttl)
  },
  dismiss: (id) => set((s) => ({ items: s.items.filter((x) => x.id !== id) })),
}))

// API publique : hook léger pour pousser un toast.
export function useToast() {
  const push = useToastStore((s) => s.push)
  return {
    success: (title: string, detail?: string) => push({ kind: 'success', title, detail }),
    error: (title: string, detail?: string) => push({ kind: 'error', title, detail }),
    info: (title: string, detail?: string) => push({ kind: 'info', title, detail }),
    warn: (title: string, detail?: string) => push({ kind: 'warn', title, detail }),
  }
}

const KIND_CLS: Record<ToastKind, string> = {
  success: 'border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-500/40 dark:bg-emerald-500/15 dark:text-emerald-200',
  error: 'border-red-200 bg-red-50 text-red-800 dark:border-red-500/40 dark:bg-red-500/15 dark:text-red-200',
  info: 'border-navy-200 bg-navy-50 text-navy-800 dark:border-navy-500/40 dark:bg-navy-500/20 dark:text-navy-100',
  warn: 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/15 dark:text-amber-200',
}

const KIND_ICON: Record<ToastKind, string> = {
  success: '✓', error: '✕', info: 'ℹ', warn: '⚠',
}

export function Toaster() {
  const { items, dismiss } = useToastStore()
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2">
      {items.map((t) => (
        <button
          key={t.id}
          onClick={() => dismiss(t.id)}
          className={`pointer-events-auto group rounded-xl border px-3 py-2 text-left shadow-pop transition hover:opacity-100 ${KIND_CLS[t.kind]}`}
          style={{ animation: 'fadeInUp 0.2s ease' }}
        >
          <div className="flex items-start gap-2">
            <span className="mt-0.5 text-base">{KIND_ICON[t.kind]}</span>
            <div className="flex-1">
              <div className="text-sm font-semibold">{t.title}</div>
              {t.detail && (
                <div className="mt-0.5 text-[11px] opacity-80">{t.detail}</div>
              )}
            </div>
            <span className="text-[10px] opacity-50 group-hover:opacity-100">✕</span>
          </div>
        </button>
      ))}
    </div>
  )
}
