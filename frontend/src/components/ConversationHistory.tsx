// Barre latérale des conversations — panneau PERSISTANT. Liste les
// investigations passées (état FSM + ancienneté), permet d'en créer une, d'en
// sélectionner une, et d'en supprimer via clic-droit → menu contextuel →
// modale de confirmation.
//
// Remplace l'ancien overlay coulissant : ici le panneau est monté en permanence
// à gauche de la zone de chat (cf. DashboardView).

import { useState } from 'react'
import { ConfirmDialog } from './ConfirmDialog'
import { ContextMenu } from './ContextMenu'
import { api } from '../lib/api'
import { usePolling } from '../lib/hooks'
import { useT } from '../lib/i18n'
import { useChannelStore, useConvStore } from '../lib/store'
import { FSM_COLORS, Pill, timeAgo, fsmLabel } from '../lib/ui'

export function ConversationSidebar() {
  const t = useT()
  const { convId, setConvId, resetBoard } = useConvStore()
  // Sélectionner / créer depuis la sidebar doit QUITTER le mode salon (sinon le
  // ChannelPanel resterait affiché par-dessus le ChatPanel).
  const setChannelId = useChannelStore((s) => s.setChannelId)
  const conversations = usePolling(api.conversations, 3000) || []
  // Menu contextuel (clic-droit) : position + conv ciblée.
  const [menu, setMenu] = useState<{ x: number; y: number; id: string } | null>(null)
  // Conversation en attente de confirmation de suppression.
  const [confirmId, setConfirmId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  // Démarre une nouvelle investigation : convId = null → le chat affiche son
  // placeholder. L'UUID est généré au premier envoi (DashboardView) pour éviter
  // les conversations fantômes jamais persistées.
  const startNew = () => {
    setChannelId(null)
    setConvId(null)
    resetBoard()
  }

  const selectConv = (id: string) => {
    setChannelId(null) // quitte le mode salon → réaffiche le ChatPanel
    setConvId(id)
  }

  const doDelete = async () => {
    if (!confirmId) return
    setDeleting(true)
    try {
      await api.deleteConversation(confirmId)
      if (convId === confirmId) setConvId(null)
    } catch {
      /* 409 (lock) ou erreur réseau — on laisse la conv dans la liste */
    }
    setDeleting(false)
    setConfirmId(null)
  }

  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-r border-slate-200 bg-white dark:border-ink-600 dark:bg-ink-800">
      {/* Bouton de création — accent teal, pleine largeur (cf. maquette). */}
      <div className="p-3">
        <button onClick={startNew} className="btn-accent w-full py-2.5 text-[12px] tracking-wide">
          <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round">
            <circle cx="12" cy="12" r="9" /><path d="M12 8v8M8 12h8" />
          </svg>
          {t('history.createConversation')}
        </button>
      </div>

      <div className="flex flex-1 flex-col gap-1 overflow-y-auto px-2 pb-2">
        {conversations.length === 0 && (
          <p className="mt-2 px-2 text-[11px] text-slate-400 dark:text-slate-500">
            {t('history.empty')}
          </p>
        )}
        {conversations.map((c) => {
          const isActive = c.conv_id === convId
          return (
            <button
              key={c.conv_id}
              onClick={() => selectConv(c.conv_id)}
              onContextMenu={(e) => {
                e.preventDefault()
                setMenu({ x: e.clientX, y: e.clientY, id: c.conv_id })
              }}
              className={`block w-full rounded-lg border-l-2 px-3 py-2 text-left text-[12px] transition ${
                isActive
                  ? 'border-teal-500 bg-teal-50 text-teal-700 dark:bg-teal-500/10 dark:text-teal-300'
                  : 'border-transparent text-slate-700 hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-ink-700'
              }`}
            >
              <div className="truncate">{c.title}</div>
              <div className="mt-0.5 flex items-center gap-1">
                <span title={c.fsm_state}>
                  <Pill label={fsmLabel(c.fsm_state)} cls={FSM_COLORS[c.fsm_state]} />
                </span>
                <span className="mono text-[9px] text-slate-400 dark:text-slate-500" title={c.updated_at}>
                  {timeAgo(c.updated_at)}
                </span>
              </div>
            </button>
          )
        })}
      </div>

      {/* Menu contextuel clic-droit : suppression de la conversation ciblée. */}
      {menu && (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          onClose={() => setMenu(null)}
          items={[
            {
              label: t('history.deleteConversation'),
              danger: true,
              icon: (
                <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round">
                  <path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13" />
                </svg>
              ),
              onClick: () => setConfirmId(menu.id),
            },
          ]}
        />
      )}

      {/* Modale de confirmation de suppression. */}
      <ConfirmDialog
        open={!!confirmId}
        title={t('history.deleteConversation')}
        body={t('history.deleteConversationBody')}
        confirmLabel={deleting ? '…' : t('history.deleteConversationConfirm')}
        onConfirm={doDelete}
        onCancel={() => setConfirmId(null)}
      />
    </aside>
  )
}
