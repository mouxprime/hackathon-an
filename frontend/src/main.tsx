import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { App } from './App'
// Polices self-hostées (bundlées au build → fonctionnent en cluster air-gap).
import '@fontsource-variable/plus-jakarta-sans'
import '@fontsource-variable/jetbrains-mono'
import './index.css'
import 'leaflet/dist/leaflet.css'

// `import.meta.env.BASE_URL` vaut "/" en local et "/hemicycle/" derrière APISIX.
// React Router supporte un `basename` sans slash final.
const basename = (import.meta.env.BASE_URL || '/').replace(/\/$/, '')

// Pas de StrictMode : éviter le double-montage des effets (double connexion WebSocket en dev).
ReactDOM.createRoot(document.getElementById('root')!).render(
  <BrowserRouter basename={basename}>
    <App />
  </BrowserRouter>,
)
