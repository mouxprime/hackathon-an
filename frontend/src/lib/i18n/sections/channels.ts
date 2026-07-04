// Section i18n « channels » : ChannelsList + ChannelPanel + toggle Historique/Channels.
// Chaque section exporte { en, fr } ; en.ts / fr.ts agrègent les sections.

export const channels = {
  en: {
    channels: {
      tab: 'Channels',
      tabHistory: 'History',
      title: 'Channels',
      alertsSection: 'Alerts',
      staffSection: 'Staff',
      readonly: 'Read-only',
      readonlyNote: 'Read-only channel — only the orchestrator posts here.',
      empty: 'No messages yet. Start the discussion.',
      emptyReadonly: 'No alerts yet.',
      placeholder: 'Message #{name}…',
      placeholderHémicycle: 'Ask Hémicycle (Enter sends)…',
      send: 'Send',
      // Compose mode (Shift+Tab)
      modeMessage: 'Message',
      modeHémicycle: 'Hémicycle',
      modeToggleHint: 'Shift+Tab — switch Message / Hémicycle',
      modeClickHint: 'click to switch',
      // Channel settings popup
      settings: {
        open: 'Channel settings',
        title: '{name} — settings',
        promptSection: 'Cell system prompt',
        promptHelp: "Describes the cell's mandate. Steers the assistant's planning and answers when tasked from this channel.",
        promptSaved: 'System prompt saved',
        promptSaveFailed: 'Could not save the prompt',
        memorySection: 'Cell memory',
        memoryHelp: '{count} fact(s) — recalled by the assistant when planning from this cell.',
        memoryEmpty: 'No cell memory yet.',
      },
      // Message actions
      reply: 'Reply',
      transfer: 'Transfer',
      delete: 'Delete',
      // Alert actions
      viewOnMap: 'View on map',
      source: 'Source',
      replyingTo: 'Replying to {who}',
      transferTitle: 'Transfer to a channel',
      transferTo: 'Target channel',
      addNote: 'Add a note (optional)…',
      // Editable cell system prompt
      promptEmpty: 'No cell mandate set — click to describe this cell.',
      promptEditTitle: 'Edit the cell system prompt (guides the assistant)',
      promptPlaceholder: "Describe the cell's mandate (e.g. G1 = personnel & HR; ignores intelligence unless it affects personnel)…",
    },
  },
  fr: {
    channels: {
      tab: 'Channels',
      tabHistory: 'Historique',
      title: 'Channels',
      alertsSection: 'Alertes',
      staffSection: 'État-major',
      readonly: 'Lecture seule',
      readonlyNote: "Salon en lecture seule — seul l'orchestrateur y publie.",
      empty: 'Aucun message. Démarrez la discussion.',
      emptyReadonly: "Aucune alerte pour l'instant.",
      placeholder: 'Message #{name}…',
      placeholderHémicycle: 'Demandez à Hémicycle (Entrée envoie)…',
      send: 'Envoyer',
      // Mode de composition (Shift+Tab)
      modeMessage: 'Message',
      modeHémicycle: 'Hémicycle',
      modeToggleHint: 'Maj+Tab — basculer Message / Hémicycle',
      modeClickHint: 'cliquez pour changer',
      // Pop-up réglages du salon
      settings: {
        open: 'Réglages du salon',
        title: '{name} — réglages',
        promptSection: 'Prompt système de la cellule',
        promptHelp: "Décrit le mandat de la cellule. Oriente la planification et les réponses de l'assistant quand on le sollicite depuis ce salon.",
        promptSaved: 'Prompt système enregistré',
        promptSaveFailed: "Échec de l'enregistrement du prompt",
        memorySection: 'Mémoire de la cellule',
        memoryHelp: "{count} fait(s) — rappelés par l'assistant lors de la planification depuis cette cellule.",
        memoryEmpty: 'Aucune mémoire de cellule pour le moment.',
      },
      // Actions de message
      reply: 'Répondre',
      transfer: 'Transférer',
      delete: 'Supprimer',
      // Actions d'alerte
      viewOnMap: 'Voir sur la map',
      source: 'Source',
      replyingTo: 'En réponse à {who}',
      transferTitle: 'Transférer vers un salon',
      transferTo: 'Salon cible',
      addNote: 'Ajouter une note (facultatif)…',
      // Prompt système éditable de la cellule
      promptEmpty: 'Aucun mandat de cellule — cliquez pour décrire cette cellule.',
      promptEditTitle: "Éditer le prompt système de la cellule (oriente l'assistant)",
      promptPlaceholder: "Décrivez le mandat de la cellule (ex. G1 = personnel/RH ; ignore le renseignement sauf si ça touche au personnel)…",
    },
  },
}
