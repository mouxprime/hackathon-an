// Dictionnaire anglais (langue par défaut) — agrégat des sections.
// Chaque section (sections/*.ts) exporte { en, fr } ; on étale ici les `.en`.

import type { Dict } from './index'
import { common } from './sections/common'
import { chat } from './sections/chat'
import { channels } from './sections/channels'
import { dashboard } from './sections/dashboard'
import { workflows } from './sections/workflows'
import { agents } from './sections/agents'
import { monitoring } from './sections/monitoring'

export const en: Dict = {
  ...common.en,
  ...chat.en,
  ...channels.en,
  ...dashboard.en,
  ...workflows.en,
  ...agents.en,
  ...monitoring.en,
}
