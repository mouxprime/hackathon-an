// Store Zustand — overlay géo éphémère (IN-MEMORY UNIQUEMENT, pas de persist).
//
// Alimenté par `showOnMap(geo)`, typiquement depuis ChannelPanel à la réception
// d'une alerte portant des détections géolocalisées. GeoMap.tsx s'y abonne pour
// fusionner ces symboles avec les locs issues de l'investigation courante.
// DashboardView.tsx observe `nonce` pour spawner/focus la carte automatiquement.

import { create } from 'zustand'
import type { GeoLoc, GeoPoly } from './types'

type GeoOverlayState = {
  locations: GeoLoc[]
  polygons: GeoPoly[]
  // nonce : incrémenté à chaque `showOnMap` — permet aux effets React de
  // détecter un appel même si le payload est identique au précédent.
  nonce: number
}

type GeoOverlayActions = {
  // Remplace intégralement les locations/polygons courants et bumpe le nonce.
  showOnMap: (geo: { locations: GeoLoc[]; polygons?: GeoPoly[] }) => void
  // Vide les tableaux de symboles. Le nonce EST laissé inchangé : on ne veut
  // pas déclencher un nouveau spawn de carte lors d'un simple effacement.
  clear: () => void
}

export type GeoOverlayStore = GeoOverlayState & GeoOverlayActions

export const useGeoOverlayStore = create<GeoOverlayStore>((set) => ({
  locations: [],
  polygons: [],
  nonce: 0,

  showOnMap: (geo) =>
    set((state) => ({
      locations: geo.locations,
      polygons: geo.polygons ?? [],
      nonce: state.nonce + 1,
    })),

  clear: () => set({ locations: [], polygons: [] }),
}))
