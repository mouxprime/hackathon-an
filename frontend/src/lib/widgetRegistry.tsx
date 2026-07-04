// Catalogue des widgets disponibles sur le /dashboard.
//
// Chaque widget = { id, title, icon, description, defaultLayout, render }.
// Le DashboardView pioche dans ce registry pour rendre, ajouter et masquer.
// Ajouter un nouveau widget = une ligne ici + son composant. Les widgets actifs
// + leur layout sont persistés dans localStorage (cf. DashboardView).

import type { ReactNode } from 'react'
import { ExecutionPyramid } from '../components/ExecutionPyramid'
import { FsmWidget } from '../components/FsmWidget'
import { GeoMap } from '../components/GeoMap'
import { LiveChannel } from '../components/LiveChannel'
import { NotesWidget } from '../components/NotesWidget'
import { RawEventsWidget } from '../components/RawEventsWidget'
import { TasksWidget } from '../components/TasksWidget'
import { TextEditorWidget } from '../components/TextEditorWidget'
import { ViewFileWidget } from '../components/ViewFileWidget'
import type { Progress, UIEvent } from './types'

// Contexte passé à chaque widget — toutes les sources live disponibles.
export type WidgetCtx = {
  events: UIEvent[]
  traces: Progress[]
  progress: Record<string, Progress>
  connected: boolean
  send: (text: string) => void
  sendMapPoint?: (lat: number, lon: number, fieldLabel?: string, requestId?: string) => void
  // Ferme/retire un widget du canvas par son instanceId. Fourni par DashboardView.
  removeWidget?: (instanceId: string) => void
}

export type WidgetSpec = {
  id: string
  title: string
  icon: string
  description: string
  defaultLayout: { w: number; h: number; minW: number; minH: number }
  render: (ctx: WidgetCtx, instanceId: string) => ReactNode
}

// Grille = 12 colonnes, hauteur de ligne = 60 px. Unités w/h en multiples.
// Le chat est fixe à gauche du dashboard (cf. DashboardView), il ne fait pas
// partie de cette bibliothèque.
export const WIDGETS: WidgetSpec[] = [
  {
    id: 'pyramid',
    title: 'widgets.pyramid.title',
    icon: '▲',
    description: 'widgets.pyramid.desc',
    defaultLayout: { w: 4, h: 7, minW: 3, minH: 4 },
    render: ({ events, traces, progress }, _instanceId) =>
      <ExecutionPyramid events={events} traces={traces} progress={progress} />,
  },
  {
    id: 'map',
    title: 'widgets.map.title',
    icon: '🗺',
    description: 'widgets.map.desc',
    defaultLayout: { w: 4, h: 4, minW: 3, minH: 3 },
    render: ({ events, sendMapPoint, send }, _instanceId) => <GeoMap events={events} sendMapPoint={sendMapPoint} send={send} />,
  },
  {
    id: 'channel',
    title: 'widgets.channel.title',
    icon: '📡',
    description: 'widgets.channel.desc',
    defaultLayout: { w: 3, h: 11, minW: 2, minH: 6 },
    render: ({ events, traces }, _instanceId) => <LiveChannel events={events} traces={traces} />,
  },
  {
    id: 'tasks',
    title: 'widgets.tasks.title',
    icon: '⚙',
    description: 'widgets.tasks.desc',
    defaultLayout: { w: 3, h: 5, minW: 2, minH: 3 },
    render: ({ events, progress }, _instanceId) => <TasksWidget events={events} progress={progress} />,
  },
  {
    id: 'fsm',
    title: 'widgets.fsm.title',
    icon: '◐',
    description: 'widgets.fsm.desc',
    defaultLayout: { w: 3, h: 5, minW: 2, minH: 3 },
    render: ({ events }, _instanceId) => <FsmWidget events={events} />,
  },
  {
    id: 'raw',
    title: 'widgets.raw.title',
    icon: '{ }',
    description: 'widgets.raw.desc',
    defaultLayout: { w: 4, h: 6, minW: 3, minH: 3 },
    render: ({ events }, _instanceId) => <RawEventsWidget events={events} />,
  },
  {
    id: 'texteditor',
    title: 'Traitement de texte',
    icon: '📝',
    description: 'Éditeur riche — gras, italique, police, export PDF/DOCX. L\'IA rédige en direct.',
    defaultLayout: { w: 5, h: 8, minW: 3, minH: 4 },
    render: (_ctx, instanceId) => <TextEditorWidget instanceId={instanceId} />,
  },
  {
    id: 'viewfile',
    title: 'Lecteur de fichier',
    icon: '📂',
    description: 'Ouvre et lit n\'importe quel fichier — PDF, image, vidéo, audio, texte/code, Word (.docx), Excel (.xlsx), ou autre. 100 % local, rien n\'est envoyé au serveur.',
    defaultLayout: { w: 5, h: 8, minW: 3, minH: 4 },
    render: (_ctx, instanceId) => <ViewFileWidget instanceId={instanceId} />,
  },
  {
    id: 'notes',
    title: 'widgets.notes.title',
    icon: '📌',
    description: 'widgets.notes.desc',
    defaultLayout: { w: 3, h: 5, minW: 2, minH: 3 },
    render: (_ctx, instanceId) => <NotesWidget instanceId={instanceId} />,
  },
]

// Layout par défaut au premier chargement : zone widgets vide. L'analyste
// dispose seulement du chat fixe à gauche, et compose à droite via "+ Widget".
export const DEFAULT_ACTIVE: string[] = []

// Layouts (positions/dimensions) par défaut, breakpoint `lg` (12 cols).
export const DEFAULT_LG_LAYOUT: any[] = []

// Retourne la spec à partir d'un ID de spec pur (ex. 'pyramid') — usage interne.
export function specById(specId: string): WidgetSpec | undefined {
  return WIDGETS.find((w) => w.id === specId)
}

// Retourne la spec à partir d'un ID d'instance (ex. 'pyramid-1716000000001').
// Format : `${specId}-${timestamp}` ou l'ancien format = specId pur (rétrocompat).
export function specByInstanceId(instanceId: string): WidgetSpec | undefined {
  const exact = WIDGETS.find((w) => w.id === instanceId)
  if (exact) return exact
  return WIDGETS.find((w) => instanceId.startsWith(w.id + '-'))
}

// Compteur monotone process-local : départage deux instanceId créés dans le même
// milliseconde (spawn en rafale, ex. carte + texteditor au même tick), sinon
// `Date.now()` seul collisionnait → clé React dupliquée + écrasement de position.
let instanceSeq = 0

// Génère un ID d'instance unique pour un type de widget donné.
export function makeInstanceId(specId: string): string {
  return `${specId}-${Date.now()}-${++instanceSeq}`
}
