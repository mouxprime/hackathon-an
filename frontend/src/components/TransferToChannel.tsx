// Popover « transférer vers un salon » réutilisable. Sert à pousser un contenu
// arbitraire (message de salon OU message d'une discussion privée avec
// l'orchestrateur) dans un salon d'état-major, avec une note optionnelle.
//
// Le contenu transféré est porté par `payload.transfer_from` du message créé —
// ChannelBubble le rend sous forme d'encart « ⤳ <source> · <auteur> ». Pas de
// `channel_id` requis : un transfert depuis le chat privé n'a pas de salon source,
// seul le libellé (`sourceLabel`) est affiché.

import { useMemo, useState } from 'react'
import { api } from '../lib/api'
import { usePolling } from '../lib/hooks'
import { useT } from '../lib/i18n'
import { CURRENT_USER, authorRoleFor } from '../lib/store'

type Props = {
  // Contenu à transférer (un message, d'où qu'il vienne).
  content: { author_role: string; text: string }
  // Libellé de la source affiché dans l'encart de transfert (nom du salon ou,
  // pour le chat privé, le titre de la conversation / « Hémicycle »).
  sourceLabel: string
  // Salon à exclure de la liste des cibles (le salon source, le cas échéant).
  excludeChannelId?: string
  onClose: () => void
}

export function TransferToChannel({ content, sourceLabel, excludeChannelId, onClose }: Props) {
  const t = useT()
  const channels = usePolling(api.channels, 10000) || []
  // Cibles = salons postables (non read-only) hors salon source.
  const targets = useMemo(
    () => channels.filter((c) => !c.readonly && c.id !== excludeChannelId),
    [channels, excludeChannelId],
  )
  const [target, setTarget] = useState<string>('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  // Cible par défaut = premier salon postable dès que la liste est chargée.
  const effectiveTarget = target || targets[0]?.id || ''

  const doTransfer = async () => {
    if (!effectiveTarget || busy) return
    setBusy(true)
    try {
      await api.postChannelMessage(effectiveTarget, {
        text: note.trim(),
        author_id: CURRENT_USER.id,
        author_role: authorRoleFor(effectiveTarget),
        payload: {
          transfer_from: {
            channel_name: sourceLabel,
            author_role: content.author_role,
            text: content.text,
          },
        },
      })
      onClose()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-navy-900/30 p-4 backdrop-blur-sm" onClick={onClose}>
      <div className="card w-full max-w-xs animate-popIn p-3 shadow-pop" onClick={(e) => e.stopPropagation()}>
        <div className="mb-2 flex items-center justify-between">
          <p className="text-[12px] font-semibold text-navy-700 dark:text-navy-200">⤳ {t('channels.transferTitle')}</p>
          <button onClick={onClose} className="text-slate-400 hover:text-navy-700 dark:hover:text-navy-200">✕</button>
        </div>
        <div className="mb-2 rounded-md border border-slate-200 dark:border-ink-600 bg-slate-50 dark:bg-ink-700 px-2 py-1 text-[11px] text-slate-500 dark:text-slate-400">
          <span className="mono text-[9px] uppercase tracking-wide text-slate-400">{content.author_role}</span>
          <p className="line-clamp-3">{content.text}</p>
        </div>
        <label className="block text-[10px] font-semibold uppercase tracking-wide text-slate-400">{t('channels.transferTo')}</label>
        <select value={effectiveTarget} onChange={(e) => setTarget(e.target.value)} className="field mt-1 w-full text-[12px]">
          {targets.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder={t('channels.addNote')}
          className="field mt-2 w-full text-[12px]"
        />
        <button
          onClick={doTransfer}
          disabled={!effectiveTarget || busy}
          className="btn-primary mt-2 w-full py-1.5 text-[12px] disabled:opacity-50"
        >
          {busy ? '…' : t('channels.transfer')}
        </button>
      </div>
    </div>
  )
}
