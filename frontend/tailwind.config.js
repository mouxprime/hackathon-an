/** @type {import('tailwindcss').Config} */
// Thème « Hémicycle » (Assemblée nationale) : header/nav en bleu Assemblée
// #223061 (texte blanc), contenu sur canvas clair avec cartes blanches + ombres
// douces. `slate` = rampe Tailwind par défaut. Les CLÉS de couleurs historiques
// (navy / teal) sont conservées, seuls les hex sont remappés :
//   navy → bleu Assemblée (ancre 700 = #223061)
//   teal → or hémicycle  (ancre 500 = #8a6420, contraste AA sur blanc)
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  // Mode nuit piloté par la classe `.dark` sur <html> (posée par useThemeStore).
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Échelle navy = bleu Assemblée (ancre #223061 = navy-700).
        navy: {
          50: '#eef1f9',
          100: '#d9dff0',
          200: '#b4c0e0',
          300: '#8b9cc9',
          400: '#6478ad',
          500: '#47598f',
          600: '#344275',
          700: '#223061', // ancre — marque principale (bleu Assemblée)
          800: '#182348',
          900: '#101731',
        },
        // Accent « or hémicycle » : sous-bandeau, boutons d'action, sélection de
        // conversation. Ancre 500 = #8a6420 (contraste AA sur blanc), 600 = hover.
        // La clé `teal` est conservée pour ne pas toucher les ~centaines d'usages.
        teal: {
          50: '#f8f2e3', // fond sélection conversation (clair)
          100: '#efe1c2', // bulle utilisateur (clair)
          200: '#e0c894',
          300: '#c9a75e', // accent dark (lumineux sur ink-*)
          400: '#a9822f', // hover bouton or (dark)
          500: '#8a6420', // ancre — sous-bandeau / bouton primaire d'action
          600: '#7a571b', // hover sous-bandeau / bouton
          700: '#684a17', // press / actif
          800: '#553c12',
          900: '#422e0e',
        },
        cream: '#faf7ef', // fond du chat (blanc crème)
        // Surfaces du mode nuit (bleu-nuit on-brand, dérivé du navy de la marque).
        // Pendant clair des `slate`/`white` : ink-900 = canvas, ink-800 = carte/champ,
        // ink-700 = surface élevée/hover/code, ink-600/500 = bordures.
        ink: {
          900: '#0a1226',
          800: '#111a33',
          700: '#16203d',
          600: '#1e2a47',
          500: '#27365a',
        },
      },
      fontFamily: {
        sans: [
          '"Plus Jakarta Sans Variable"', 'Plus Jakarta Sans',
          'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif',
        ],
        mono: [
          '"JetBrains Mono Variable"', 'JetBrains Mono',
          'ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace',
        ],
      },
      boxShadow: {
        xs: '0 1px 2px rgba(15, 23, 42, 0.05)',
        card: '0 4px 14px rgba(15, 23, 42, 0.06), 0 1px 3px rgba(15, 23, 42, 0.04)',
        md: '0 8px 24px rgba(15, 23, 42, 0.08), 0 2px 6px rgba(15, 23, 42, 0.04)',
        pop: '0 16px 40px rgba(15, 23, 42, 0.12), 0 4px 12px rgba(15, 23, 42, 0.06)',
      },
      transitionTimingFunction: {
        out: 'cubic-bezier(0.16, 1, 0.3, 1)',
      },
      keyframes: {
        fadeInUp: {
          '0%': { opacity: '0', transform: 'translateY(6px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        fadeIn: { '0%': { opacity: '0' }, '100%': { opacity: '1' } },
        popIn: {
          '0%': { opacity: '0', transform: 'scale(0.96)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
        slideInRight: {
          '0%': { opacity: '0', transform: 'translateX(8px)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        },
        // Barre de progression indéterminée : un segment qui balaie la piste.
        indeterminate: {
          '0%': { left: '-40%', width: '40%' },
          '50%': { left: '30%', width: '50%' },
          '100%': { left: '100%', width: '40%' },
        },
        // Halo qui « respire » sur le bouton Approuver d'un plan (teal-500 = or).
        softPulse: {
          '0%, 100%': { boxShadow: '0 0 0 0 rgba(138, 100, 32, 0.5)' },
          '50%': { boxShadow: '0 0 0 5px rgba(138, 100, 32, 0)' },
        },
        // Anneau pulsé sur la barre d'input en mode renommage (navy-600).
        ringPulse: {
          '0%, 100%': { boxShadow: '0 0 0 2px rgba(52, 66, 117, 0.35)' },
          '50%': { boxShadow: '0 0 0 3px rgba(52, 66, 117, 0.7)' },
        },
      },
      animation: {
        fadeInUp: 'fadeInUp 0.28s cubic-bezier(0.16, 1, 0.3, 1)',
        fadeIn: 'fadeIn 0.2s ease',
        popIn: 'popIn 0.22s cubic-bezier(0.16, 1, 0.3, 1)',
        slideInRight: 'slideInRight 0.24s cubic-bezier(0.16, 1, 0.3, 1)',
        indeterminate: 'indeterminate 1.2s ease-in-out infinite',
        softPulse: 'softPulse 1.8s ease-in-out infinite',
        ringPulse: 'ringPulse 1.5s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
