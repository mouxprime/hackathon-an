// Dictionnaire français — agrégat des sections (cf. en.ts).

import type { Dict } from './index'
import { common } from './sections/common'
import { chat } from './sections/chat'
import { channels } from './sections/channels'
import { dashboard } from './sections/dashboard'
import { workflows } from './sections/workflows'
import { agents } from './sections/agents'
import { monitoring } from './sections/monitoring'

export const fr: Dict = {
  ...common.fr,
  ...chat.fr,
  ...channels.fr,
  ...dashboard.fr,
  ...workflows.fr,
  ...agents.fr,
  ...monitoring.fr,
}
