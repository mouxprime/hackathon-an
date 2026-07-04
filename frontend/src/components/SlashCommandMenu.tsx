// Menu de commandes slash — s'ouvre quand l'analyste tape « / » en 1er caractère.
// Composant PUREMENT présentationnel : la navigation clavier (↑/↓/Enter/Tab/Esc)
// est gérée par l'input parent (ChatPanel.onKeyDown), pas par un listener window.
// Ça évite la fragilité d'un handler capture cross-composant (timing/closure).

import { useT } from '../lib/i18n'

export type SlashCommandName =
  | 'agents' | 'clear' | 'compact' | 'context' | 'new' | 'rename' | 'resume'

export type SlashCmd = { name: SlashCommandName; descKey: string }

const COMMANDS: SlashCmd[] = [
  { name: 'agents',  descKey: 'chat.cmd.agents.desc' },
  { name: 'clear',   descKey: 'chat.cmd.clear.desc' },
  { name: 'compact', descKey: 'chat.cmd.compact.desc' },
  { name: 'context', descKey: 'chat.cmd.context.desc' },
  { name: 'new',     descKey: 'chat.cmd.new.desc' },
  { name: 'rename',  descKey: 'chat.cmd.rename.desc' },
  { name: 'resume',  descKey: 'chat.cmd.resume.desc' },
]

// Filtre les commandes selon le préfixe tapé (après le « / »). Exporté pour que
// le parent calcule la même liste que celle affichée (source de vérité partagée).
export function filterSlashCommands(query: string): SlashCmd[] {
  const q = query.toLowerCase().replace(/^\//, '')
  if (!q) return COMMANDS
  return COMMANDS.filter((c) => c.name.startsWith(q))
}

type Props = {
  // Liste filtrée + index surligné : fournis par le parent (état contrôlé).
  items: SlashCmd[]
  cursor: number
  onSelect: (name: SlashCommandName) => void
  onHover: (index: number) => void
}

export function SlashCommandMenu({ items, cursor, onSelect, onHover }: Props) {
  const t = useT()
  if (items.length === 0) return null

  return (
    <div className="absolute bottom-full left-0 right-0 mb-1 z-50 animate-popIn">
      <ul role="listbox" className="card shadow-pop overflow-hidden py-1">
        {items.map((cmd, i) => (
          <li
            key={cmd.name}
            role="option"
            aria-selected={i === cursor}
            onMouseEnter={() => onHover(i)}
            // mousedown (pas click) : se déclenche avant le blur de l'input,
            // donc la sélection part même si le focus quitte le champ.
            onMouseDown={(e) => { e.preventDefault(); onSelect(cmd.name) }}
            className={`flex cursor-pointer items-center gap-2.5 px-3 py-1.5 transition ${
              i === cursor
                ? 'bg-navy-50 dark:bg-navy-500/15'
                : 'hover:bg-slate-50 dark:hover:bg-ink-700'
            }`}
          >
            <span className="mono text-[11px] font-semibold text-navy-700 dark:text-navy-200 w-14 shrink-0">
              /{cmd.name}
            </span>
            <span className="text-[11px] text-slate-500 dark:text-slate-400 truncate">
              {t(cmd.descKey)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
