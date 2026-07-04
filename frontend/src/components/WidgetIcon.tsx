// Icônes line-art des widgets (24×24, stroke = currentColor). Centralise le visuel
// de chaque type de widget pour la bibliothèque, la taskbar et la liste des
// fenêtres — un seul endroit à éditer pour faire évoluer l'iconographie.
// Le `specId` (pas l'instanceId) sélectionne le tracé ; défaut = carré générique.

type Props = { specId: string; className?: string }

// Attributs communs à tous les SVG : trait fin arrondi, sans remplissage.
const SVG = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
}

// Tracés par type de widget. Chaque entrée = le contenu interne du <svg>.
const PATHS: Record<string, JSX.Element> = {
  // Pyramide d'exécution : triangle stratifié.
  pyramid: (
    <>
      <path d="M12 3 21 20 H3 Z" />
      <path d="M9.7 8.5 H14.3" />
      <path d="M7.4 13 H16.6" />
    </>
  ),
  // Carte géo : épingle de localisation.
  map: (
    <>
      <path d="M12 21s7-5.7 7-11a7 7 0 1 0-14 0c0 5.3 7 11 7 11Z" />
      <circle cx="12" cy="10" r="2.4" />
    </>
  ),
  // Canal live : ondes de diffusion.
  channel: (
    <>
      <circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none" />
      <path d="M8.6 15.4a5 5 0 0 1 0-6.8" />
      <path d="M15.4 8.6a5 5 0 0 1 0 6.8" />
      <path d="M6.2 17.8a9 9 0 0 1 0-11.6" />
      <path d="M17.8 6.2a9 9 0 0 1 0 11.6" />
    </>
  ),
  // Tâches sous-agents : checklist cochée.
  tasks: (
    <>
      <path d="M9.5 6.5 H20" />
      <path d="M9.5 12 H20" />
      <path d="M9.5 17.5 H20" />
      <path d="M4 6l1.3 1.3L7.2 5" />
      <path d="M4 11.5l1.3 1.3L7.2 10.5" />
      <path d="M4 17l1.3 1.3L7.2 16" />
    </>
  ),
  // Machine à états : deux nœuds reliés par une transition fléchée.
  fsm: (
    <>
      <circle cx="6" cy="12" r="3" />
      <circle cx="18" cy="12" r="3" />
      <path d="M9 12 H15" />
      <path d="M13 10l2 2-2 2" />
    </>
  ),
  // Événements bruts : accolades de code { }.
  raw: (
    <>
      <path d="M9 4c-2 0-2.5 1.4-2.5 3.2 0 1.6-.4 3-1.5 3.3v3c1.1.3 1.5 1.7 1.5 3.3 0 1.8.5 3.2 2.5 3.2" />
      <path d="M15 4c2 0 2.5 1.4 2.5 3.2 0 1.6.4 3 1.5 3.3v3c-1.1.3-1.5 1.7-1.5 3.3 0 1.8-.5 3.2-2.5 3.2" />
    </>
  ),
  // Traduction : globe (langues du monde).
  translation: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12 H21" />
      <path d="M12 3a14 14 0 0 1 0 18" />
      <path d="M12 3a14 14 0 0 0 0 18" />
    </>
  ),
  // Traitement de texte : feuille avec lignes de texte.
  texteditor: (
    <>
      <path d="M6.5 3 H14 l3.5 3.5 V21 H6.5 Z" />
      <path d="M14 3 V6.5 H17.5" />
      <path d="M9 12 H15" />
      <path d="M9 15.5 H15" />
      <path d="M9 8.5 H11" />
    </>
  ),
  // Lecteur de fichier : dossier ouvert avec une feuille.
  viewfile: (
    <>
      <path d="M3 7.5a1.5 1.5 0 0 1 1.5-1.5H9l2 2h6.5a1.5 1.5 0 0 1 1.5 1.5v1.5H5.2a1.5 1.5 0 0 0-1.45 1.1L3 18Z" />
      <path d="M3.75 18 5.2 12.6a1.5 1.5 0 0 1 1.45-1.1H21l-1.6 5.4A1.5 1.5 0 0 1 18 18Z" />
    </>
  ),
  // Notes / Post-it : feuillet avec coin replié + lignes de texte.
  notes: (
    <>
      <path d="M5 4h10l4 4v12H5Z" />
      <path d="M15 4v4h4" />
      <path d="M8 12h7" />
      <path d="M8 15.5h7" />
    </>
  ),
  // Audio → Texte : micro sur socle.
  transcribe: (
    <>
      <rect x="9.5" y="3" width="5" height="9" rx="2.5" />
      <path d="M6.5 11a5.5 5.5 0 0 0 11 0" />
      <path d="M12 16.5V20" />
      <path d="M9 20h6" />
    </>
  ),
  // GraphIT : graphe de connaissance (nœud central + satellites).
  graphit: (
    <>
      <circle cx="12" cy="12" r="2.3" />
      <circle cx="5" cy="6" r="1.8" />
      <circle cx="19" cy="7" r="1.8" />
      <circle cx="16.5" cy="19" r="1.8" />
      <path d="M10.4 10.5 6.4 7.3" />
      <path d="M13.7 11 17.3 8.2" />
      <path d="M13 13.8 15.7 17.4" />
    </>
  ),
}

// Carré générique pour tout specId inconnu.
const FALLBACK = (
  <rect x="4" y="4" width="16" height="16" rx="3" />
)

// Rend l'icône SVG d'un widget à partir de son specId.
export function WidgetIcon({ specId, className = 'h-5 w-5' }: Props) {
  return (
    <svg {...SVG} className={className} aria-hidden>
      {PATHS[specId] ?? FALLBACK}
    </svg>
  )
}
