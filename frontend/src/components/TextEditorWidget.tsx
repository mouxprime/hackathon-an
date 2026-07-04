// Widget d'édition de texte riche (TipTap v2).
//
// Toolbar : Gras | Italique | taille (S/M/L/XL) | police (Sans/Serif/Mono) | ⬇ PDF | ⬇ DOCX
//
// L'IA peut écrire en streaming via useTextEditorStore.setReportText().
// Le mode Rapport redirige les chunks vers cet éditeur si présent sur le canvas.

import { useEffect, useCallback, useRef } from 'react'
import { useEditor, EditorContent } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import { TextStyle, Color } from '@tiptap/extension-text-style'
import { FontFamily } from '@tiptap/extension-font-family'
import { Table } from '@tiptap/extension-table'
import { TableRow } from '@tiptap/extension-table-row'
import { TableHeader } from '@tiptap/extension-table-header'
import { TableCell } from '@tiptap/extension-table-cell'
import { exportReportPdf } from '../lib/exportReportPdf'
import { BlinkingCaret } from './blinkingCaret'

// Extension TextStyle étendue avec l'attribut fontSize (pas de paquet dédié disponible).
const FontSizeStyle = TextStyle.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      fontSize: {
        default: null,
        parseHTML: (el) => (el as HTMLElement).style.fontSize || null,
        renderHTML: (attrs) =>
          attrs.fontSize ? { style: `font-size: ${attrs.fontSize}` } : {},
      },
    }
  },
})
import TextAlign from '@tiptap/extension-text-align'
import { marked } from 'marked'
import { useTextEditorStore } from '../lib/store'

// Activation explicite GFM + breaks : sans GFM les tableaux ne sont jamais parsés,
// et sans `breaks` un retour à la ligne simple est mangé (le LLM en pose souvent).
marked.setOptions({ gfm: true, breaks: true })

const FONT_SIZES = [
  { label: '10', value: '10px' },
  { label: '12', value: '12px' },
  { label: '14', value: '14px' },
  { label: '16', value: '16px' },
  { label: '18', value: '18px' },
  { label: '20', value: '20px' },
  { label: '24', value: '24px' },
  { label: '28', value: '28px' },
  { label: '32', value: '32px' },
]

const FONT_FAMILIES = [
  { label: 'Sans',  value: 'Plus Jakarta Sans, sans-serif' },
  { label: 'Serif', value: 'Georgia, serif' },
  { label: 'Mono',  value: 'JetBrains Mono, monospace' },
]

// CSS de la page A4 dupliqué depuis index.css (.report-page + descendants) pour
// que les exports PDF/DOCX rendent EXACTEMENT le même look que dans le widget :
// largeur A4, headings navy, paragraphes justifiés, tableaux GFM stylés, etc.
// Sans cette injection, html2pdf et Word reçoivent du HTML nu et écrasent tout.
// Seules les règles « light » sont incluses (les exports sont toujours imprimés
// sur fond blanc, peu importe le thème dans lequel l'utilisateur édite).
export const REPORT_PAGE_CSS = `
.report-page {
  background: #ffffff;
  width: 794px;
  max-width: 100%;
  margin: 0 auto;
  padding: 92px 84px;
  font-family: "Plus Jakarta Sans", "Inter", ui-sans-serif, system-ui, sans-serif;
  color: #0f172a;
  font-size: 13.5px;
  line-height: 1.65;
}
.report-page h1 { font-size: 26px; font-weight: 700; line-height: 1.25; color: #223061; margin: 0 0 18px 0; padding-bottom: 10px; border-bottom: 2px solid #223061; letter-spacing: -0.01em; page-break-after: avoid; break-after: avoid-page; page-break-inside: avoid; break-inside: avoid; }
.report-page h2 { font-size: 19px; font-weight: 700; color: #223061; margin: 28px 0 10px 0; letter-spacing: -0.005em; page-break-after: avoid; break-after: avoid-page; page-break-inside: avoid; break-inside: avoid; }
.report-page h3 { font-size: 15.5px; font-weight: 700; color: #1e3a72; margin: 20px 0 6px 0; page-break-after: avoid; break-after: avoid-page; page-break-inside: avoid; break-inside: avoid; }
.report-page h4, .report-page h5, .report-page h6 { font-size: 14px; font-weight: 700; color: #1e3a72; margin: 16px 0 4px 0; page-break-after: avoid; break-after: avoid-page; page-break-inside: avoid; break-inside: avoid; }
.report-page p { margin: 0 0 12px 0; text-align: justify; hyphens: auto; page-break-inside: avoid; break-inside: avoid; orphans: 3; widows: 3; }
.report-page strong { font-weight: 700; color: #223061; }
.report-page em { font-style: italic; }
.report-page ul, .report-page ol { margin: 0 0 14px 0; padding-left: 26px; page-break-inside: avoid; break-inside: avoid; }
.report-page ul { list-style: disc; }
.report-page ol { list-style: decimal; }
.report-page li { margin: 4px 0; page-break-inside: avoid; break-inside: avoid; }
.report-page li > p { margin: 0; }
.report-page blockquote { margin: 14px 0; padding: 6px 14px; border-left: 3px solid #223061; background: rgba(34, 48, 97, 0.04); color: #1e3a72; font-style: italic; page-break-inside: avoid; break-inside: avoid; }
.report-page code { font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 0.88em; background: #f1f5f9; border: 1px solid #e2e8f0; border-radius: 3px; padding: 1px 5px; }
.report-page pre { font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 12.5px; background: #0f172a; color: #e2e8f0; border-radius: 6px; padding: 12px 14px; overflow-x: auto; margin: 14px 0; page-break-inside: avoid; break-inside: avoid; }
.report-page pre code { background: transparent; border: 0; padding: 0; color: inherit; }
.report-page table, .report-page .report-table { width: 100%; border-collapse: collapse; margin: 14px 0; font-size: 12.5px; page-break-inside: auto; break-inside: auto; }
.report-page thead { display: table-header-group; }
.report-page tr { page-break-inside: avoid; break-inside: avoid; }
.report-page th, .report-page td { border: 1px solid #cbd5e1; padding: 8px 10px; text-align: left; vertical-align: top; min-width: 1em; }
.report-page th { background: #f1f5f9; color: #223061; font-weight: 700; border-bottom-width: 2px; }
.report-page tr:nth-child(even) td { background: #f8fafc; }
.report-page hr { border: 0; border-top: 1px solid #cbd5e1; margin: 22px 0; }
`

// Icônes SVG inline — trait outline, stroke=currentColor pour hériter de la couleur
// du bouton (navy au repos, blanc quand actif). viewBox 24×24 standard.
const SVG_PROPS = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
}
function IconBold() {
  return (
    <svg width="18" height="18" {...SVG_PROPS} strokeWidth={2.6}>
      <path d="M7 4.5h6.2a3.6 3.6 0 0 1 0 7.2H7zM7 11.7h7.1a3.8 3.8 0 0 1 0 7.6H7z" />
    </svg>
  )
}
function IconItalic() {
  return (
    <svg width="18" height="18" {...SVG_PROPS} strokeWidth={2.4}>
      <line x1="19" y1="5"  x2="11" y2="5"  />
      <line x1="13" y1="19" x2="5"  y2="19" />
      <line x1="15" y1="5"  x2="9"  y2="19" />
    </svg>
  )
}
function IconAlignLeft() {
  return (
    <svg width="18" height="18" {...SVG_PROPS} strokeWidth={2}>
      <line x1="3" y1="6"  x2="21" y2="6"  />
      <line x1="3" y1="11" x2="14" y2="11" />
      <line x1="3" y1="16" x2="18" y2="16" />
      <line x1="3" y1="21" x2="12" y2="21" />
    </svg>
  )
}
function IconAlignCenter() {
  return (
    <svg width="18" height="18" {...SVG_PROPS} strokeWidth={2}>
      <line x1="3" y1="6"  x2="21" y2="6"  />
      <line x1="6" y1="11" x2="18" y2="11" />
      <line x1="3" y1="16" x2="21" y2="16" />
      <line x1="8" y1="21" x2="16" y2="21" />
    </svg>
  )
}
function IconAlignRight() {
  return (
    <svg width="18" height="18" {...SVG_PROPS} strokeWidth={2}>
      <line x1="3"  y1="6"  x2="21" y2="6"  />
      <line x1="10" y1="11" x2="21" y2="11" />
      <line x1="6"  y1="16" x2="21" y2="16" />
      <line x1="12" y1="21" x2="21" y2="21" />
    </svg>
  )
}
function IconAlignJustify() {
  return (
    <svg width="18" height="18" {...SVG_PROPS} strokeWidth={2}>
      <line x1="3" y1="6"  x2="21" y2="6"  />
      <line x1="3" y1="11" x2="21" y2="11" />
      <line x1="3" y1="16" x2="21" y2="16" />
      <line x1="3" y1="21" x2="21" y2="21" />
    </svg>
  )
}
function IconColorA() {
  // « A » outline pour le bouton couleur — la pastille colorée est ajoutée en
  // dessous via une div séparée pour qu'elle reflète la couleur courante.
  return (
    <svg width="18" height="18" {...SVG_PROPS} strokeWidth={2.2}>
      <path d="M5 19L12 4l7 15" />
      <path d="M7.5 14h9" />
    </svg>
  )
}
function IconDownload() {
  return (
    <svg width="14" height="14" {...SVG_PROPS} strokeWidth={2.2}>
      <path d="M12 3v12" />
      <path d="M7 10l5 5 5-5" />
      <path d="M4 20h16" />
    </svg>
  )
}

type Props = {
  instanceId: string
}

export function TextEditorWidget({ instanceId }: Props) {
  const { setActiveEditorId, takeReportText, saveContent, getContent } = useTextEditorStore()

  // Snapshot du contenu d'avant le démontage précédent. Lu UNE seule fois au
  // mount (clé : instanceId) — TipTap initialisera son arbre ProseMirror à
  // partir de cet HTML, et le streaming reprend sur le markdown accumulé.
  const initial = useRef(getContent(instanceId))
  const accumulatedMd = useRef(initial.current?.markdown ?? '')
  // Conteneur de la zone d'édition — utilisé pour cloner la page A4 live
  // (avec ses styles inline déjà appliqués) au moment d'exporter.
  const editorAreaRef = useRef<HTMLDivElement>(null)

  const editor = useEditor({
    extensions: [
      StarterKit,
      FontSizeStyle,
      FontFamily,
      // Color : pose une span avec `color:` inline. Survit aux exports puisque
      // c'est du style en ligne (pas dépendant d'une feuille externe).
      Color,
      TextAlign.configure({ types: ['heading', 'paragraph'] }),
      // Table : indispensable au rendu des `| col |` GFM produits par le rapport.
      // Sans cette extension, marked.parse génère bien du `<table>` mais TipTap
      // l'efface au setContent → la grille devient une bouillie de texte concaténé.
      Table.configure({ resizable: false, HTMLAttributes: { class: 'report-table' } }),
      TableRow,
      TableHeader,
      TableCell,
      // Caret bloc clignotant en overlay (le caret natif est masqué en CSS) —
      // l'analyste voit où il écrit, y compris pendant la rédaction IA.
      BlinkingCaret,
    ],
    content: initial.current?.html ?? '',
    editorProps: {
      attributes: {
        // `report-page` (cf. index.css) applique : largeur A4, padding type page,
        // hiérarchie h1/h2/h3 distincte, tableaux stylés, espacement de paragraphe.
        class: 'report-page focus:outline-none text-slate-800 dark:text-slate-100',
      },
    },
    // Édition manuelle de l'analyste : on persiste l'HTML à chaque update.
    // Sans ça, ses modifs disparaîtraient au prochain unmount.
    onUpdate: ({ editor }) => {
      saveContent(instanceId, { html: editor.getHTML(), markdown: accumulatedMd.current })
    },
  })

  // Enregistre cet éditeur comme éditeur actif dès le montage.
  useEffect(() => {
    setActiveEditorId(instanceId)
    return () => {
      // Ne nettoie l'`activeEditorId` que si on est encore celui qui le détenait —
      // évite d'effacer l'ID d'un éventuel autre mount qui aurait pris la suite.
      if (useTextEditorStore.getState().activeEditorId === instanceId) {
        useTextEditorStore.setState({ activeEditorId: null })
      }
    }
  }, [instanceId, setActiveEditorId])

  // Réconcilie l'éditeur avec le rapport IA toutes les 60 ms (streaming).
  // `takeReportText` rend le markdown COMPLET courant (live puis canonique) :
  // on remplace le contenu de façon idempotente. Insensible à la course de
  // montage — un éditeur qui apparaît en cours de rédaction rattrape d'un coup
  // tout le texte déjà streamé. On persiste à chaque application pour qu'un
  // remount instantané (fullscreen) ne perde rien.
  useEffect(() => {
    if (!editor) return
    const interval = setInterval(() => {
      const md = takeReportText()
      // null = rien de neuf ; identique = déjà affiché (évite un setContent inutile
      // qui ferait sauter le curseur de l'analyste pendant qu'il édite).
      if (md === null || md === accumulatedMd.current) return
      accumulatedMd.current = md
      const html = String(marked.parse(md))
      editor.commands.setContent(html, { emitUpdate: false })
      editor.commands.focus('end')
      saveContent(instanceId, { html, markdown: md })
    }, 60)
    return () => clearInterval(interval)
  }, [editor, takeReportText, instanceId, saveContent])

  // Récupère le HTML « tel qu'affiché » : on prend l'outerHTML du <div class=
  // "report-page"> vivant dans le DOM (donc avec les styles inline TipTap déjà
  // posés : color, fontFamily, fontSize, text-align). Si l'élément n'est pas
  // monté (cas limite), on retombe sur editor.getHTML() enveloppé à la main.
  const getRenderedReportHtml = useCallback(() => {
    const live = editorAreaRef.current?.querySelector('.report-page') as HTMLElement | null
    if (live) {
      const clone = live.cloneNode(true) as HTMLElement
      // On retire les classes Tailwind utilitaires propres à l'édition (focus, dark)
      // pour ne garder que la classe `report-page` que cible le CSS injecté.
      clone.className = 'report-page'
      return clone.outerHTML
    }
    return `<div class="report-page">${editor?.getHTML() ?? ''}</div>`
  }, [editor])

  // Export PDF via le helper partagé (pdfmake) : vraie couche texte, extractible
  // par les agents NLP. Même pipeline que la carte rapport du chat.
  const exportPdf = useCallback(async () => {
    if (!editor) return
    await exportReportPdf(editor.getHTML())
  }, [editor])

  // Export DOCX : Word et LibreOffice ouvrent nativement les .doc contenant du
  // HTML+CSS. On embarque le bloc <style> .report-page pour que les headings,
  // tableaux, alignements et couleurs (styles inline) soient préservés.
  const exportDocx = useCallback(() => {
    if (!editor) return
    const html = `<!DOCTYPE html>
<html xmlns:o='urn:schemas-microsoft-com:office:office'
      xmlns:w='urn:schemas-microsoft-com:office:word'
      xmlns='http://www.w3.org/TR/REC-html40'>
<head>
<meta charset='utf-8'>
<title>Rapport Hémicycle</title>
<style>${REPORT_PAGE_CSS}</style>
</head>
<body>${getRenderedReportHtml()}</body></html>`
    const blob = new Blob(['﻿', html], { type: 'application/msword' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'rapport-hemicycle.doc'
    a.click()
    URL.revokeObjectURL(url)
  }, [editor, getRenderedReportHtml])

  if (!editor) return null

  // Classes communes des boutons de la toolbar — carrés 32×32, icônes 18px centrées.
  const btn = 'flex h-8 w-8 items-center justify-center rounded-md transition'
  const btnIdle = 'text-navy-700 dark:text-slate-200 hover:bg-slate-200 dark:hover:bg-ink-700'
  const btnActive = 'bg-navy-700 text-white shadow-sm'
  // Valeurs courantes au curseur (rafraîchies à chaque transaction TipTap).
  const currentSize = (editor.getAttributes('textStyle').fontSize as string) || ''
  const currentFamily = (editor.getAttributes('textStyle').fontFamily as string) || ''
  const currentColor = (editor.getAttributes('textStyle').color as string) || ''
  const selectCls =
    'h-8 rounded-md border border-slate-300 dark:border-ink-600 bg-white dark:bg-ink-800 ' +
    'px-2 text-[12px] font-medium text-navy-700 dark:text-slate-200 ' +
    'hover:border-navy-700 focus:outline-none focus:ring-2 focus:ring-navy-700/30 transition'

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Toolbar — plus haute (h≈44), icônes 18px, groupes séparés par un divider vertical. */}
      <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-slate-200 dark:border-ink-700 bg-slate-50 dark:bg-ink-900 px-3 py-1.5">
        {/* Groupe Police / Taille — selects pour rester compact malgré ~9 tailles */}
        <select
          value={currentFamily}
          onChange={(e) => {
            const v = e.target.value
            if (v) editor.chain().focus().setFontFamily(v).run()
            else editor.chain().focus().unsetFontFamily().run()
          }}
          className={`${selectCls} min-w-[88px]`}
          title="Police"
        >
          <option value="">Police</option>
          {FONT_FAMILIES.map((f) => (
            <option key={f.value} value={f.value}>{f.label}</option>
          ))}
        </select>
        <select
          value={currentSize}
          onChange={(e) => {
            const v = e.target.value
            if (v) editor.chain().focus().setMark('textStyle', { fontSize: v }).run()
            else editor.chain().focus().setMark('textStyle', { fontSize: null }).run()
          }}
          className={`${selectCls} w-[64px]`}
          title="Taille du texte"
        >
          <option value="">Taille</option>
          {FONT_SIZES.map((s) => (
            <option key={s.value} value={s.value}>{s.label}</option>
          ))}
        </select>

        <div className="mx-2 h-6 w-px bg-slate-300 dark:bg-ink-600" />

        {/* Groupe Style — gras / italique, bien espacés */}
        <button
          onMouseDown={(e) => { e.preventDefault(); editor.chain().focus().toggleBold().run() }}
          className={`${btn} ${editor.isActive('bold') ? btnActive : btnIdle}`}
          title="Gras (Ctrl+B)"
          aria-label="Gras"
        >
          <IconBold />
        </button>
        <button
          onMouseDown={(e) => { e.preventDefault(); editor.chain().focus().toggleItalic().run() }}
          className={`${btn} ${editor.isActive('italic') ? btnActive : btnIdle}`}
          title="Italique (Ctrl+I)"
          aria-label="Italique"
        >
          <IconItalic />
        </button>

        {/* Couleur du texte — bouton « A » + barre colorée sous la lettre.
            Clic sur la lettre = ouvre le picker natif ; clic-droit = reset. */}
        <label
          className={`${btn} ${btnIdle} relative cursor-pointer`}
          title="Couleur du texte (clic) — clic-droit pour réinitialiser"
          onContextMenu={(e) => {
            e.preventDefault()
            editor.chain().focus().unsetColor().run()
          }}
        >
          <IconColorA />
          <span
            className="pointer-events-none absolute bottom-1 left-1.5 right-1.5 h-[3px] rounded-sm"
            style={{ background: currentColor || '#0f172a' }}
          />
          <input
            type="color"
            value={currentColor || '#000000'}
            onChange={(e) => editor.chain().focus().setColor(e.target.value).run()}
            className="absolute inset-0 cursor-pointer opacity-0"
            aria-label="Choisir la couleur du texte"
          />
        </label>
        <div className="mx-2 h-6 w-px bg-slate-300 dark:bg-ink-600" />

        {/* Groupe Alignement — left / center / right / justify */}
        {([
          { value: 'left',    Icon: IconAlignLeft,    title: 'Aligner à gauche' },
          { value: 'center',  Icon: IconAlignCenter,  title: 'Centrer'          },
          { value: 'right',   Icon: IconAlignRight,   title: 'Aligner à droite' },
          { value: 'justify', Icon: IconAlignJustify, title: 'Justifier'        },
        ] as const).map(({ value, Icon, title }) => (
          <button
            key={value}
            onMouseDown={(e) => { e.preventDefault(); editor.chain().focus().setTextAlign(value).run() }}
            className={`${btn} ${editor.isActive({ textAlign: value }) ? btnActive : btnIdle}`}
            title={title}
            aria-label={title}
          >
            <Icon />
          </button>
        ))}

        {/* Exports — alignés à droite */}
        <div className="ml-auto flex items-center gap-1.5">
          <button
            onClick={exportPdf}
            className="flex h-8 items-center gap-1.5 rounded-md border border-slate-300 dark:border-ink-600 bg-white dark:bg-ink-800 px-2.5 text-[12px] font-medium text-navy-700 dark:text-slate-200 hover:border-navy-700 hover:bg-navy-700 hover:text-white transition"
            title="Télécharger en PDF"
          >
            <IconDownload /> PDF
          </button>
          <button
            onClick={exportDocx}
            className="flex h-8 items-center gap-1.5 rounded-md border border-slate-300 dark:border-ink-600 bg-white dark:bg-ink-800 px-2.5 text-[12px] font-medium text-navy-700 dark:text-slate-200 hover:border-navy-700 hover:bg-navy-700 hover:text-white transition"
            title="Télécharger en DOCX"
          >
            <IconDownload /> DOCX
          </button>
        </div>
      </div>
      {/* Zone d'édition : fond gris « bureau », page A4 centrée par-dessus.
          Padding ≈ 20px = la « marge externe » autour de la feuille, qui matche
          la contrainte de redimensionnement (A4 + 20 px de marge G/D).
          Le `ref` sert aux exports — on clone le `.report-page` qu'il contient. */}
      <div ref={editorAreaRef} data-editor-scroll className="relative flex-1 overflow-y-auto bg-slate-100 dark:bg-ink-900 px-5 py-5">
        <EditorContent editor={editor} />
      </div>
    </div>
  )
}
