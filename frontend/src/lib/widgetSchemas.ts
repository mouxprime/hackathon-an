// Schémas Zod TOLÉRANTS des payloads métier (contrats gelés avec le backend).
// Tolérants = champs optionnels partout où possible + objets « loose »
// (équivalent .passthrough() : les champs inconnus passent sans erreur).
// Un parse raté ne casse jamais le fil : le composant affiche WidgetErrorCard.

import { z } from 'zod'

// ── Briques partagées ────────────────────────────────────────────────────────

// Source citée par un agent. `url` est désormais fourni par le backend.
export const sourceItemSchema = z.looseObject({
  title: z.string().optional(),
  url: z.string().optional(),
  snippet: z.string().optional(),
  source: z.string().optional(),
  reliability: z.union([z.string(), z.number()]).optional(),
})
export type SourceItemPayload = z.infer<typeof sourceItemSchema>

const sourcesField = z.array(sourceItemSchema).optional()

// ── vote_chart ───────────────────────────────────────────────────────────────

export const groupeVoteSchema = z.looseObject({
  nom: z.string().optional(),
  abrege: z.string().optional(),
  couleur: z.string().optional(),
  pour: z.number().optional(),
  contre: z.number().optional(),
  abstention: z.number().optional(),
  nonVotant: z.number().optional(),
  position_majoritaire: z.string().optional(),
})

export const votesPayloadSchema = z.looseObject({
  scrutin: z.looseObject({
    uid: z.string().optional(),
    numero: z.union([z.string(), z.number()]).optional(),
    date: z.string().optional(),
    titre: z.string().optional(),
    sort: z.string().optional(),
    pour: z.number().optional(),
    contre: z.number().optional(),
    abstentions: z.number().optional(),
    non_votants: z.number().optional(),
    url_an: z.string().optional(),
    url_data: z.string().optional(),
  }).optional(),
  position_depute: z.looseObject({
    acteur_ref: z.string().optional(),
    nom: z.string().optional(),
    groupe: z.string().optional(),
    position: z.string().optional(),
  }).optional().nullable(),
  groupes: z.array(groupeVoteSchema).optional(),
  sources: sourcesField,
  summary: z.string().optional(),
})
export type VotesPayload = z.infer<typeof votesPayloadSchema>

// ── fiche_depute ─────────────────────────────────────────────────────────────

export const deputeSchema = z.looseObject({
  uid: z.string().optional(),
  nom_complet: z.string().optional(),
  groupe: z.string().optional(),
  circo: z.string().optional(),
  actif: z.boolean().optional(),
  url_an: z.string().optional(),
  url_photo: z.string().optional(),
})

export const deputesPayloadSchema = z.looseObject({
  depute: deputeSchema.optional(),
  deputes: z.array(deputeSchema).optional(),
  votes_recents: z.array(z.looseObject({
    scrutin_titre: z.string().optional(),
    date: z.string().optional(),
    position: z.string().optional(),
    url_an: z.string().optional(),
  })).optional(),
  stats: z.looseObject({
    participation: z.number().optional(),
    loyaute: z.number().optional(),
  }).optional().nullable(),
  sources: sourcesField,
  summary: z.string().optional(),
})
export type DeputesPayload = z.infer<typeof deputesPayloadSchema>

// ── timeline_loi ─────────────────────────────────────────────────────────────

export const loisPayloadSchema = z.looseObject({
  dossier: z.looseObject({
    titre: z.string().optional(),
    uid: z.string().optional(),
    statut: z.string().optional(),
    url_an: z.string().optional(),
  }).optional(),
  etapes: z.array(z.looseObject({
    date: z.string().optional(),
    libelle: z.string().optional(),
    chambre: z.string().optional(),
    // Tolérant : un statut inattendu ne fait pas échouer le parse (rendu « pending »).
    statut: z.string().optional(),
  })).optional(),
  articles: z.array(z.any()).optional(),
  sources: sourcesField,
  summary: z.string().optional(),
})
export type LoisPayload = z.infer<typeof loisPayloadSchema>

// ── Helper de parse ──────────────────────────────────────────────────────────

export type ParseResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string }

// Parse tolérant : renvoie soit les données typées, soit une erreur lisible
// (jamais d'exception). À consommer par les composants widget : sur erreur,
// afficher <WidgetErrorCard details={error} />.
export function parseWidgetData<S extends z.ZodType>(
  schema: S,
  data: unknown,
): ParseResult<z.infer<S>> {
  const res = schema.safeParse(data ?? {})
  if (res.success) return { ok: true, data: res.data }
  const detail = res.error.issues
    .slice(0, 5)
    .map((i) => `${i.path.join('.') || '(racine)'} : ${i.message}`)
    .join(' · ')
  return { ok: false, error: detail || 'structure inattendue' }
}
