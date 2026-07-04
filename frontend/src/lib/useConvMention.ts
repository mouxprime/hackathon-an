// Détecte le déclencheur @ dans le draft et filtre la liste de conversations.
// Expose l'état ouvert/fermé du picker, les résultats filtrés et une fonction
// de sélection qui retire le trigger du draft et retourne la ConvRef choisie.

import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from './api'
import type { Conversation, ConvRef } from './types'

// @ précédé d'un début de chaîne ou d'un espace, suivi de texte non-espace
// jusqu'à la fin du draft (position curseur = fin pour un <input> simple).
const TRIGGER_RE = /(?:^|\s)@(\S*)$/

export function useConvMention(draft: string, currentConvId: string | null) {
  // Détecte le déclencheur et extrait la requête de filtrage.
  const match = useMemo(() => TRIGGER_RE.exec(draft), [draft])
  const pickerOpen = match !== null
  const query = match ? match[1] : ''

  // Liste des conversations — chargée à l'ouverture du picker, nettoyée à la fermeture.
  // Le flag `alive` évite les mises à jour sur un effet démonté ou périmé (race condition).
  const [conversations, setConversations] = useState<Conversation[]>([])
  useEffect(() => {
    if (!pickerOpen) {
      setConversations([])   // libère mémoire quand le picker se ferme
      return
    }
    let alive = true
    api.conversations()
      .then((data) => { if (alive) setConversations(data) })
      .catch(() => {})
    return () => { alive = false }
  }, [pickerOpen])

  // Filtrage insensible à la casse, exclusion de la conv active (ref circulaire).
  const filtered = useMemo(() => {
    if (!pickerOpen) return []
    const q = query.toLowerCase()
    return conversations
      .filter((c) => c.conv_id !== currentConvId)
      .filter((c) => c.title.toLowerCase().includes(q))
      .slice(0, 10)
  }, [pickerOpen, query, conversations, currentConvId])

  // Sélectionne une conv : retire "@query" de la fin du draft et retourne la ref.
  const select = useCallback(
    (conv: Conversation): { draft: string; ref: ConvRef } => {
      // Remplace "@query" en fin de draft ; conserve l'espace de tête si présent.
      const newDraft = draft
        .replace(/(?:^|\s)@\S*$/, (m) => (m.startsWith(' ') ? ' ' : ''))
        .trimEnd()
      return { draft: newDraft, ref: { conv_id: conv.conv_id, title: conv.title } }
    },
    [draft],
  )

  // Ferme le picker sans sélectionner — retire le déclencheur du draft.
  const close = useCallback(
    (): string =>
      draft
        .replace(/(?:^|\s)@\S*$/, (m) => (m.startsWith(' ') ? ' ' : ''))
        .trimEnd(),
    [draft],
  )

  return { pickerOpen, filtered, select, close }
}
