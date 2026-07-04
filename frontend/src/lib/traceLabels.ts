// Humanisation des traces d'outils (tool_call / tool_result) pour le
// « Parcours de l'IA » : le citoyen doit lire « Recherche du scrutin… » plutôt
// qu'un nom d'endpoint MCP. `trace_data` peut contenir :
//   { tool, mcp_server?, endpoint?, count?, preview? }
// Si `mcp_server` est présent, l'appelant affiche une chip « via {mcp_server} »
// (transparence sur la source de données — thème « IA de confiance »).

export type HumanTrace = {
  label: string        // libellé lisible citoyen
  via?: string         // serveur MCP interrogé (chip « via … »)
  technical?: string   // détail technique (tool + endpoint) pour le title=
}

// Mapping mots-clés → libellé d'ACTION (tool_call). Premier match gagne.
const CALL_PATTERNS: [RegExp, string][] = [
  [/scrutin|vote/i, 'Recherche du scrutin…'],
  [/depute|acteur|elu/i, 'Recherche du député…'],
  [/groupe/i, 'Recherche du groupe politique…'],
  [/dossier|loi|legislat/i, 'Consultation du dossier législatif…'],
  [/amendement/i, 'Recherche des amendements…'],
  [/article|legifrance|texte/i, 'Lecture du texte de loi…'],
  [/circonscription|circo/i, 'Recherche de la circonscription…'],
  [/search|recherche|query/i, 'Recherche dans les données publiques…'],
  [/fetch|get|read|list/i, 'Consultation des données officielles…'],
]

// Libellé d'un tool_call.
export function traceCallLabel(d: Record<string, any> | null | undefined): HumanTrace {
  const tool = String(d?.tool || '')
  const endpoint = String(d?.endpoint || '')
  const haystack = `${tool} ${endpoint}`
  for (const [re, label] of CALL_PATTERNS) {
    if (re.test(haystack)) {
      return { label, via: d?.mcp_server, technical: [tool, endpoint].filter(Boolean).join(' ') }
    }
  }
  return {
    label: tool ? `Interrogation de ${tool}…` : 'Interrogation des données…',
    via: d?.mcp_server,
    technical: [tool, endpoint].filter(Boolean).join(' '),
  }
}

// Libellé d'un tool_result : privilégie le compte de résultats si fourni.
export function traceResultLabel(d: Record<string, any> | null | undefined): HumanTrace {
  const tool = String(d?.tool || '')
  const via = d?.mcp_server as string | undefined
  const technical = [tool, String(d?.endpoint || '')].filter(Boolean).join(' ')
  if (d?.skipped) return { label: 'Étape ignorée', via, technical }
  if (typeof d?.count === 'number') {
    const what =
      /scrutin|vote/i.test(tool) ? (d.count > 1 ? 'votes trouvés' : 'vote trouvé')
      : /depute|acteur/i.test(tool) ? (d.count > 1 ? 'députés trouvés' : 'député trouvé')
      : /dossier|loi/i.test(tool) ? (d.count > 1 ? 'dossiers trouvés' : 'dossier trouvé')
      : (d.count > 1 ? 'résultats trouvés' : 'résultat trouvé')
    return { label: `${d.count} ${what}`, via, technical }
  }
  if (typeof d?.preview === 'string' && d.preview.trim()) {
    return { label: d.preview.trim().slice(0, 120), via, technical }
  }
  return { label: 'Données reçues', via, technical }
}

// Point d'entrée générique selon le kind de la trace.
export function humanizeTrace(kind: string, d: Record<string, any> | null | undefined): HumanTrace {
  if (kind === 'tool_call') return traceCallLabel(d)
  if (kind === 'tool_result') return traceResultLabel(d)
  return { label: String(d?.text || ''), via: d?.mcp_server }
}
