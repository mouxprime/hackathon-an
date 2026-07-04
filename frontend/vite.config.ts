import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Préfixe d'URL sous lequel l'app est servie (ex: "/hemicycle/" derrière APISIX).
// Vite expose la valeur via `import.meta.env.BASE_URL` côté client — utilisé par
// React Router (basename) et le client API (préfixe /api & /ws).
const base = process.env.VITE_BASE ?? '/'

// Cible du proxy /api & /ws : par défaut, le service docker-compose `gateway`.
// En K8s, on injecte `GATEWAY_PROXY_TARGET=http://hemicycle-hemicycle-gateway:8000`.
const gatewayHttp = process.env.GATEWAY_PROXY_TARGET ?? 'http://gateway:8000'
const gatewayWs = gatewayHttp.replace(/^http/, 'ws')

// Le dev server écoute sur toutes les interfaces et proxifie l'API/WS vers le gateway.
// Front et API partagent ainsi la même origine → un seul tunnel (Tailscale) suffit,
// et zéro souci de mixed-content ou de CORS. Fonctionne en local comme à distance.
export default defineConfig({
  base,
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    allowedHosts: true,
    proxy: {
      '/api': { target: gatewayHttp, changeOrigin: true },
      '/ws': { target: gatewayWs, ws: true },
    },
  },
})
