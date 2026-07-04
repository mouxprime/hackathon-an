// Caret d'insertion clignotant pour le TextEditorWidget.
//
// Le caret natif du contenteditable (1px, parfois invisible après une rédaction
// IA) ne dit pas à l'analyste où il écrit sur la page A4. On dessine donc un
// caret bloc clignotant en OVERLAY : un <span> positionné en absolu dans le
// conteneur scrollable de l'éditeur, placé via view.coordsAtPos — sans rien
// injecter dans le DOM éditable, donc sans perturber la frappe ni l'IME. Le
// caret natif est masqué en CSS (.ProseMirror { caret-color: transparent }).
import { Extension } from '@tiptap/core'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import type { EditorView } from '@tiptap/pm/view'

export const BlinkingCaret = Extension.create({
  name: 'blinkingCaret',
  addProseMirrorPlugins() {
    return [
      new Plugin({
        key: new PluginKey('blinkingCaret'),
        view(editorView) {
          // Conteneur scrollable (marqué `data-editor-scroll` côté widget). Le
          // caret y est ajouté comme enfant absolu : il défile naturellement avec
          // le contenu, sans avoir à écouter le scroll.
          const container = editorView.dom.closest('[data-editor-scroll]') as HTMLElement | null
          const caret = document.createElement('span')
          caret.className = 'blinking-caret'
          caret.setAttribute('aria-hidden', 'true')
          caret.style.display = 'none'
          if (container) container.appendChild(caret)

          // Place le caret sur la tête de sélection. Masqué si l'éditeur n'a pas
          // le focus ou si du texte est sélectionné (la surbrillance suffit alors).
          const reposition = (view: EditorView) => {
            if (!container) return
            const { selection } = view.state
            if (!view.hasFocus() || !selection.empty) {
              caret.style.display = 'none'
              return
            }
            let coords
            try {
              coords = view.coordsAtPos(selection.head)
            } catch {
              caret.style.display = 'none'
              return
            }
            const rect = container.getBoundingClientRect()
            caret.style.display = 'block'
            caret.style.top = `${coords.top - rect.top + container.scrollTop}px`
            caret.style.left = `${coords.left - rect.left + container.scrollLeft}px`
            caret.style.height = `${Math.max(coords.bottom - coords.top, 14)}px`
            // Redémarre le clignotement → caret plein au moment où on bouge/tape,
            // comme un vrai caret, puis reprise du blink.
            caret.style.animation = 'none'
            void caret.offsetWidth
            caret.style.animation = ''
          }

          // focus/blur ne déclenchent pas toujours une transaction → on les écoute.
          const onFocusChange = () => reposition(editorView)
          editorView.dom.addEventListener('focus', onFocusChange)
          editorView.dom.addEventListener('blur', onFocusChange)

          return {
            update: (view) => reposition(view),
            destroy: () => {
              editorView.dom.removeEventListener('focus', onFocusChange)
              editorView.dom.removeEventListener('blur', onFocusChange)
              caret.remove()
            },
          }
        },
      }),
    ]
  },
})
