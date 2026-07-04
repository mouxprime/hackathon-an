// Section i18n « dashboard » : la coquille DashboardView + bibliothèque de
// widgets (namespace `dashboard`) et tout le chrome des composants widget
// (namespace `widgets` : en-têtes, libellés, unités, états vides, compteurs).
// Anglais = langue canonique ; français = texte UI existant repris verbatim.

export const dashboard = {
  en: {
    dashboard: {
      emptyBoardTitle: 'No widget on the board.',
      emptyBoardHint:
        'Click the “+” at the bottom right, or drag a widget from the library.',
      collapseChat: 'Collapse the chat',
      collapseChatBtn: '▾ Collapse',
      openChat: 'Open the chat',
      chatBtn: '💬 Chat',
      addWidget: 'Add a widget',
      library: 'Widget library',
      searchWidget: 'Search a widget…',
      noWidgetMatch: 'No widget matches.',
      allWidgetsOnBoard: 'All widgets are already on the board.',
      dragToCanvas: 'drag onto the canvas (or click)',
      clearBoard: 'Clear the board',
      resetDashboard: '↺ Reset the dashboard',
      resetConfirm: 'Reset the widgets?',
      attachmentRejected: 'Attachment “{name}” rejected',
    },
    widgets: {
      // Cadre commun
      frame: { close: 'Close the widget' },
      // Registre (titres + descriptions de la bibliothèque)
      pyramid: {
        title: "AI journey",
        desc: 'Question → orchestrator → agents → tools → answer',
      },
      map: {
        title: 'Geo map',
        desc: 'Leaflet markers — appears automatically with located results',
      },
      channel: {
        title: 'Real-time channel',
        desc: 'Filterable scrolling feed (events + traces)',
      },
      tasks: {
        title: 'Active tasks',
        desc: 'Compact list of dispatched tasks',
      },
      fsm: {
        title: 'FSM',
        desc: 'Current FSM state + latest transitions',
      },
      raw: {
        title: 'Raw events',
        desc: 'Latest UIEvent as JSON (debug)',
      },
      // GeoMap
      geomap: {
        title: 'Geo map',
        markers: { one: '{count} marker', other: '{count} markers' },
        layers: {
          day: 'Day',
          night: 'Night',
          satellite: 'Satellite',
          rail: 'Railways',
        },
        layerTitle: 'Basemap: {name} — click to switch',
        confirmPoint: 'Confirm position',
      },
      // FsmWidget
      fsmWidget: {
        currentPlan: 'Current plan',
        empty: '— empty —',
        latestTransitions: 'Latest transitions',
        none: '— none —',
      },
      // RawEventsWidget
      rawEvents: {
        title: 'Raw events (debug)',
        received: { one: '{count} received', other: '{count} received' },
        waiting: '— waiting —',
      },
      // TasksWidget
      tasksWidget: {
        title: 'Active tasks',
        empty: '— no task —',
      },
      // LiveChannel
      liveChannel: {
        title: 'Real-time channel',
        filters: {
          all: 'all',
          message: 'message',
          widget: 'widget',
          trace: 'trace',
          status: 'status',
        },
        empty: '— waiting for activity —',
      },
      // ExecutionPyramid
      pyramidWidget: {
        title: 'AI journey',
        sendPrompt: 'Send a prompt to see the chain build up.',
        userPrompt: 'User prompt',
        orchestrator: 'ORCHESTRATOR',
        planSteps: {
          one: '{state} · plan = {count} step',
          other: '{state} · plan = {count} steps',
        },
        agentsToolsThoughts: 'Agents · tools · thoughts',
        tools: 'Tools',
        thoughts: 'Thoughts',
        waiting: '— waiting —',
      },
      // widgets.tsx (server-driven)
      trace: {
        thinking: 'thinking…',
        tool: 'tool',
        result: 'result',
        skipped: '(skipped)',
      },
      taskProgress: {
        attempt: 'attempt {n}',
      },
      sourceList: {
        sources: { one: '{count} source', other: '{count} sources' },
        reliability: 'reliab. {value}',
      },
      imageCard: {
        confidence: 'conf. {pct}%',
      },
      mapPayload: {
        locations: { one: '{count} location', other: '{count} locations' },
      },
      labels: {
        report: 'REPORT',
        memory: 'MEMORY',
        memoryProposal: 'TO MEMORIZE',
      },
      queries: {
        title: { one: '🔎 Query ({count})', other: '🔎 Queries ({count})' },
        tool: 'tool',
      },
      bytes: { b: '{n} B', kb: '{n} KB', mb: '{n} MB' },
      dataMgmt: {
        file: 'file',
        bucket: 'bucket {name}',
        download: '⬇ Download',
        collectionsCreated: 'Collection(s) created',
        collection: 'collection',
      },
      markdown: {
        pointsOnMap: {
          one: '📍 {count} point placed on the dashboard map.',
          other: '📍 {count} points placed on the dashboard map.',
        },
        zonesOnMap: {
          one: '🗺 {count} zone drawn on the dashboard map.',
          other: '🗺 {count} zones drawn on the dashboard map.',
        },
      },
      memoryRecall: {
        none: 'no relevant memory',
        loaded: { one: '{count} memory loaded', other: '{count} memories loaded' },
        fromContext: 'Context retrieved from “{name}”',
      },
      memoryProposal: {
        confirm: 'Confirm',
        reject: 'Reject',
        saved: 'saved',
        rejected: 'rejected',
        error: 'save failed',
      },
      notes: {
        title: 'Notes',
        desc: 'Free analyst notes — Markdown, auto-saved per sticky on the board',
        placeholder: 'Type your notes… (Markdown supported)',
        preview: 'Preview',
        edit: 'Edit',
        saved: 'Saved',
        empty: 'Nothing to preview yet.',
      },
    },
  },
  fr: {
    dashboard: {
      emptyBoardTitle: 'Aucun widget sur le tableau.',
      emptyBoardHint:
        'Cliquez sur le « + » en bas à droite, ou glissez un widget depuis la bibliothèque.',
      collapseChat: 'Réduire le chat',
      collapseChatBtn: '▾ Réduire',
      openChat: 'Ouvrir le chat',
      chatBtn: '💬 Chat',
      addWidget: 'Ajouter un widget',
      library: 'Bibliothèque de widgets',
      searchWidget: 'Rechercher un widget…',
      noWidgetMatch: 'Aucun widget ne correspond.',
      allWidgetsOnBoard: 'Tous les widgets sont déjà sur le tableau.',
      dragToCanvas: 'glissez sur le canvas (ou cliquez)',
      clearBoard: 'Vider le tableau',
      resetDashboard: '↺ Réinitialiser le dashboard',
      resetConfirm: 'Réinitialiser les widgets ?',
      attachmentRejected: 'Pièce jointe « {name} » refusée',
    },
    widgets: {
      frame: { close: 'Fermer le widget' },
      pyramid: {
        title: "Parcours de l'IA",
        desc: 'Question → orchestrateur → agents → outils → réponse',
      },
      map: {
        title: 'Carte géo',
        desc: 'Marqueurs Leaflet — apparaît auto avec des résultats localisés',
      },
      channel: {
        title: 'Channel temps réel',
        desc: 'Flux déroulant filtrable (events + traces)',
      },
      tasks: {
        title: 'Tâches actives',
        desc: 'Liste compacte des tâches dispatchées',
      },
      fsm: {
        title: 'FSM',
        desc: 'État FSM courant + dernières transitions',
      },
      raw: {
        title: 'Raw events',
        desc: 'Derniers UIEvent en JSON (debug)',
      },
      geomap: {
        title: 'Carte géo',
        markers: { one: '{count} marqueur', other: '{count} marqueur(s)' },
        layers: {
          day: 'Jour',
          night: 'Nuit',
          satellite: 'Satellite',
          rail: 'Voies ferrées',
        },
        layerTitle: 'Fond : {name} — clic pour changer',
        confirmPoint: 'Confirmer la position',
      },
      fsmWidget: {
        currentPlan: 'Plan en cours',
        empty: '— vide —',
        latestTransitions: 'Dernières transitions',
        none: '— aucune —',
      },
      rawEvents: {
        title: 'Raw events (debug)',
        received: { one: '{count} reçu', other: '{count} reçus' },
        waiting: '— en attente —',
      },
      tasksWidget: {
        title: 'Tâches actives',
        empty: '— aucune tâche —',
      },
      liveChannel: {
        title: 'Channel temps réel',
        filters: {
          all: 'all',
          message: 'message',
          widget: 'widget',
          trace: 'trace',
          status: 'status',
        },
        empty: "— en attente d'activité —",
      },
      pyramidWidget: {
        title: "Parcours de l'IA",
        sendPrompt: 'Envoyez un prompt pour voir la chaîne se construire.',
        userPrompt: 'Prompt utilisateur',
        orchestrator: 'ORCHESTRATEUR',
        planSteps: {
          one: '{state} · plan = {count} étape',
          other: '{state} · plan = {count} étape(s)',
        },
        agentsToolsThoughts: 'Agents · tools · pensées',
        tools: 'Tools',
        thoughts: 'Pensées',
        waiting: '— en attente —',
      },
      trace: {
        thinking: 'réflexion…',
        tool: 'outil',
        result: 'résultat',
        skipped: '(ignoré)',
      },
      taskProgress: {
        attempt: 'essai {n}',
      },
      sourceList: {
        sources: { one: '{count} source', other: '{count} source(s)' },
        reliability: 'fiab. {value}',
      },
      imageCard: {
        confidence: 'conf. {pct}%',
      },
      mapPayload: {
        locations: { one: '{count} lieu', other: '{count} lieu(x)' },
      },
      labels: {
        report: 'RAPPORT',
        memory: 'MÉMOIRE',
        memoryProposal: 'À MÉMORISER',
      },
      queries: {
        title: { one: '🔎 Requête ({count})', other: '🔎 Requêtes ({count})' },
        tool: 'outil',
      },
      bytes: { b: '{n} o', kb: '{n} Ko', mb: '{n} Mo' },
      dataMgmt: {
        file: 'fichier',
        bucket: 'bucket {name}',
        download: '⬇ Télécharger',
        collectionsCreated: 'Collection(s) créée(s)',
        collection: 'collection',
      },
      markdown: {
        pointsOnMap: {
          one: '📍 {count} point placé sur la carte du dashboard.',
          other: '📍 {count} point(s) placé(s) sur la carte du dashboard.',
        },
        zonesOnMap: {
          one: '🗺 {count} emprise tracée sur la carte du dashboard.',
          other: '🗺 {count} emprise(s) tracée(s) sur la carte du dashboard.',
        },
      },
      memoryRecall: {
        none: 'aucun souvenir pertinent',
        loaded: { one: '{count} souvenir chargé', other: '{count} souvenir(s) chargé(s)' },
        fromContext: 'Contexte récupéré depuis « {name} »',
      },
      memoryProposal: {
        confirm: 'Valider',
        reject: 'Rejeter',
        saved: 'mémorisé',
        rejected: 'rejeté',
        error: 'échec de sauvegarde',
      },
      notes: {
        title: 'Notes',
        desc: "Notes libres de l'analyste — Markdown, sauvegarde auto par post-it sur le tableau",
        placeholder: 'Saisissez vos notes… (Markdown supporté)',
        preview: 'Aperçu',
        edit: 'Éditer',
        saved: 'Enregistré',
        empty: 'Rien à prévisualiser.',
      },
    },
  },
}
