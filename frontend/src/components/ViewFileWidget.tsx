// Widget « Lecteur de fichier » — ouvre et affiche N'IMPORTE QUEL fichier local.
//
// L'analyste sélectionne (bouton ou glisser-déposer) un fichier depuis sa machine :
//   - image (png/jpg/gif/webp/svg…)  → rendu <img>
//   - pdf                             → visionneuse PDF native du navigateur (<iframe>)
//   - vidéo / audio                   → lecteur <video>/<audio>
//   - texte / code / json / md / csv… → rendu texte (markdown stylé pour les .md)
//   - tout le reste (docx, xlsx, zip, binaire…) → fiche méta + « ouvrir dans un onglet »
//
// 100 % côté navigateur : le fichier ne quitte jamais le poste (URL blob locale),
// rien n'est envoyé au backend. L'URL blob est révoquée à chaque changement de
// fichier et au démontage pour éviter les fuites mémoire.

import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { useFileViewerStore } from '../lib/store'
import { Markdown } from './Markdown'

// Extensions traitées comme du texte brut (affichage <pre>) quand le MIME est absent/générique.
const TEXT_EXT = new Set([
  'txt', 'log', 'csv', 'tsv', 'json', 'jsonl', 'ndjson', 'xml', 'yaml', 'yml', 'toml', 'ini',
  'cfg', 'conf', 'env', 'properties', 'sql', 'html', 'htm', 'css', 'scss', 'less',
  'js', 'jsx', 'ts', 'tsx', 'mjs', 'cjs', 'py', 'rb', 'php', 'java', 'kt', 'kts', 'go',
  'rs', 'c', 'h', 'cpp', 'hpp', 'cc', 'cs', 'swift', 'scala', 'pl', 'lua', 'r', 'sh', 'bash',
  'zsh', 'fish', 'ps1', 'bat', 'dockerfile', 'makefile', 'gitignore', 'vue', 'svelte',
  'graphql', 'gql', 'proto', 'tex', 'srt', 'vtt', 'asc',
])
const MD_EXT = new Set(['md', 'markdown', 'mdown', 'mkd'])

// Documents Word et tableurs : rendus inline via librairies lazy-loadées.
const DOCX_EXT = new Set(['docx'])
const SHEET_EXT = new Set(['xlsx', 'xlsm', 'xls', 'ods', 'fods'])

// MIME texte non préfixés `text/` que le navigateur étiquette parfois autrement.
const TEXT_MIME = new Set([
  'application/json', 'application/ld+json', 'application/xml', 'application/javascript',
  'application/x-javascript', 'application/x-yaml', 'application/yaml', 'application/x-sh',
  'application/sql', 'application/x-httpd-php', 'application/toml',
])

type Kind = 'image' | 'pdf' | 'video' | 'audio' | 'text' | 'markdown' | 'docx' | 'sheet' | 'other'

// Au-delà de cette taille on n'essaie pas de charger le texte en mémoire (perf).
const TEXT_MAX_BYTES = 8 * 1024 * 1024 // 8 Mo

// Détermine la catégorie d'affichage d'un fichier à partir de son MIME + extension.
function classify(file: File): Kind {
  const mime = file.type.toLowerCase()
  const ext = (file.name.split('.').pop() || '').toLowerCase()

  if (MD_EXT.has(ext)) return 'markdown'
  if (DOCX_EXT.has(ext) || mime === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document') return 'docx'
  if (SHEET_EXT.has(ext) ||
      mime === 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' ||
      mime === 'application/vnd.ms-excel' ||
      mime === 'application/vnd.oasis.opendocument.spreadsheet') return 'sheet'
  if (mime === 'image/svg+xml' || ext === 'svg') return 'image'
  if (mime.startsWith('image/')) return 'image'
  if (mime === 'application/pdf' || ext === 'pdf') return 'pdf'
  if (mime.startsWith('video/')) return 'video'
  if (mime.startsWith('audio/')) return 'audio'
  if (mime.startsWith('text/')) return 'text'
  if (TEXT_MIME.has(mime)) return 'text'
  if (!mime && TEXT_EXT.has(ext)) return 'text'
  if (TEXT_EXT.has(ext)) return 'text'
  return 'other'
}

// Formate une taille en octets de façon lisible.
function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} o`
  const units = ['Ko', 'Mo', 'Go']
  let v = bytes / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`
}

type Loaded = {
  file: File
  url: string
  kind: Kind
  text?: string       // rempli pour les fichiers texte/markdown
  html?: string       // rempli pour docx (mammoth) et tableurs (SheetJS)
  textError?: string  // message si le contenu n'a pas pu être chargé
}

// Convertit un .docx en HTML via mammoth (lazy-loadé).
async function docxToHtml(file: File): Promise<string> {
  const mammoth = await import('mammoth')
  const arrayBuffer = await file.arrayBuffer()
  const { value } = await mammoth.convertToHtml({ arrayBuffer })
  return value || '<p><em>Document vide.</em></p>'
}

// Convertit un tableur (xlsx/xls/ods…) en HTML : une table par feuille (SheetJS lazy-loadé).
async function sheetToHtml(file: File): Promise<string> {
  const XLSX = await import('xlsx')
  const data = new Uint8Array(await file.arrayBuffer())
  const wb = XLSX.read(data, { type: 'array' })
  return wb.SheetNames
    .map((name) => {
      const table = XLSX.utils.sheet_to_html(wb.Sheets[name], { id: '', editable: false })
      return `<h3 class="vf-sheet-title">${name}</h3>${table}`
    })
    .join('')
}

export function ViewFileWidget({ instanceId }: { instanceId?: string }) {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [remoteError, setRemoteError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  // Conserve l'URL blob courante pour la révoquer proprement (closure stable).
  const urlRef = useRef<string | null>(null)

  // Révoque l'URL blob au démontage du widget.
  useEffect(() => {
    return () => { if (urlRef.current) URL.revokeObjectURL(urlRef.current) }
  }, [])

  // Chargement PROGRAMMATIQUE : si le widget a été spawné depuis le badge d'une
  // pièce jointe (descripteur déposé dans le store), on va chercher les octets
  // originaux via le gateway et on les passe au MÊME pipeline que le glisser-déposer.
  useEffect(() => {
    if (!instanceId) return
    const desc = useFileViewerStore.getState().getFile(instanceId)
    if (!desc) return
    let cancelled = false
    fetch(api.attachmentContentUrl(desc.attachmentId))
      .then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.blob() })
      .then((blob) => {
        if (cancelled) return
        const type = desc.contentType || blob.type || ''
        openFile(new File([blob], desc.filename, { type }))
      })
      .catch(() => { if (!cancelled) setRemoteError('Impossible de charger la pièce jointe.') })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instanceId])

  // Ajuste la taille du widget à l'ASPECT de l'image une fois ses dimensions
  // naturelles connues : photo paysage → widget large, portrait → widget haut.
  const onImageMeasured = (nw: number, nh: number) => {
    if (!instanceId || !nw || !nh) return
    const maxW = 1100, maxH = 860, minW = 320, minH = 280
    const scale = Math.min(1, maxW / nw, maxH / nh)
    const w = Math.max(minW, Math.round(nw * scale))
    const h = Math.max(minH, Math.round(nh * scale)) + 44 // + barre d'actions
    useFileViewerStore.getState().resize?.(instanceId, w, h)
  }

  // Charge un fichier : crée l'URL blob, classe le type, lit le texte si pertinent.
  const openFile = (file: File) => {
    if (urlRef.current) URL.revokeObjectURL(urlRef.current)
    const url = URL.createObjectURL(file)
    urlRef.current = url
    const kind = classify(file)
    const base: Loaded = { file, url, kind }

    if (kind === 'text' || kind === 'markdown') {
      if (file.size > TEXT_MAX_BYTES) {
        setLoaded({ ...base, textError: `Fichier trop volumineux pour l'aperçu texte (${humanSize(file.size)}).` })
        return
      }
      // Lecture asynchrone du contenu texte.
      const reader = new FileReader()
      reader.onload = () => setLoaded({ ...base, text: String(reader.result ?? '') })
      reader.onerror = () => setLoaded({ ...base, textError: 'Lecture du fichier impossible.' })
      reader.readAsText(file)
      // Affiche d'abord la fiche sans texte, le contenu arrive au onload.
      setLoaded(base)
      return
    }

    if (kind === 'docx' || kind === 'sheet') {
      // Conversion asynchrone via librairie lazy-loadée ; on garde l'URL pour
      // détecter une ouverture concurrente d'un autre fichier (course de promesses).
      setLoaded(base)
      const convert = kind === 'docx' ? docxToHtml : sheetToHtml
      convert(file)
        .then((html) => {
          if (urlRef.current !== url) return // un autre fichier a été ouvert entre-temps
          setLoaded({ ...base, html })
        })
        .catch(() => {
          if (urlRef.current !== url) return
          setLoaded({ ...base, textError: 'Conversion du document impossible (fichier corrompu ou format non géré).' })
        })
      return
    }

    setLoaded(base)
  }

  // Handler du <input type=file>.
  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) openFile(f)
    e.target.value = '' // permet de re-sélectionner le même fichier
  }

  // Glisser-déposer.
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const f = e.dataTransfer.files?.[0]
    if (f) openFile(f)
  }

  return (
    <div
      className="flex h-full flex-col overflow-hidden"
      onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
      onDragLeave={() => setDragOver(false)}
      onDrop={onDrop}
    >
      {/* Barre d'actions */}
      <div className="flex shrink-0 items-center gap-2 border-b border-slate-200 dark:border-ink-700 px-3 py-2">
        <button
          onClick={() => inputRef.current?.click()}
          className="flex h-8 items-center gap-1.5 rounded-md bg-navy-700 px-3 text-[12px]
                     font-medium text-white transition hover:bg-navy-600"
        >
          📂 Ouvrir un fichier
        </button>
        {loaded && (
          <>
            <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-slate-700 dark:text-slate-200" title={loaded.file.name}>
              {loaded.file.name}
            </span>
            <span className="shrink-0 text-[11px] text-slate-400 dark:text-slate-500">
              {humanSize(loaded.file.size)}
            </span>
            <a
              href={loaded.url}
              target="_blank"
              rel="noreferrer"
              className="shrink-0 rounded px-1.5 py-0.5 text-[11px] text-slate-500 dark:text-slate-400
                         hover:bg-slate-200 dark:hover:bg-ink-700 transition"
              title="Ouvrir dans un nouvel onglet"
            >
              ↗
            </a>
            <a
              href={loaded.url}
              download={loaded.file.name}
              className="shrink-0 rounded px-1.5 py-0.5 text-[11px] text-slate-500 dark:text-slate-400
                         hover:bg-slate-200 dark:hover:bg-ink-700 transition"
              title="Télécharger"
            >
              ⤓
            </a>
          </>
        )}
        <input ref={inputRef} type="file" className="hidden" onChange={onPick} />
      </div>

      {/* Zone d'affichage */}
      <div className="relative min-h-0 flex-1 overflow-auto bg-slate-50 dark:bg-ink-900">
        {loaded ? (
          <FileView loaded={loaded} onImageMeasured={onImageMeasured} />
        ) : remoteError ? (
          <div className="flex h-full items-center justify-center p-6 text-center text-sm text-rose-500">
            {remoteError}
          </div>
        ) : instanceId && useFileViewerStore.getState().getFile(instanceId) ? (
          <Loading />
        ) : (
          <Dropzone active={dragOver} onClick={() => inputRef.current?.click()} />
        )}
        {/* Surcouche de glisser-déposer quand un fichier est déjà ouvert */}
        {loaded && dragOver && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center
                          border-2 border-dashed border-navy-500 bg-navy-500/10 text-sm font-medium text-navy-700 dark:text-navy-200">
            Déposer pour remplacer
          </div>
        )}
      </div>
    </div>
  )
}

// Écran d'accueil / cible de dépôt quand aucun fichier n'est ouvert.
function Dropzone({ active, onClick }: { active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`flex h-full w-full flex-col items-center justify-center gap-2 p-6 text-center transition ${
        active ? 'bg-navy-500/10' : ''
      }`}
    >
      <span className="text-4xl opacity-60">📄</span>
      <span className="text-sm font-medium text-slate-600 dark:text-slate-300">
        Glisser un fichier ici, ou cliquer pour parcourir
      </span>
      <span className="text-[11px] text-slate-400 dark:text-slate-500">
        Image, PDF, vidéo, audio, texte/code, ou tout autre fichier
      </span>
    </button>
  )
}

// Rend le contenu selon la catégorie détectée.
function FileView({ loaded, onImageMeasured }: {
  loaded: Loaded
  onImageMeasured?: (naturalW: number, naturalH: number) => void
}) {
  const { kind, url, file, text, textError } = loaded

  switch (kind) {
    case 'image':
      return (
        <div className="flex min-h-full items-center justify-center p-4">
          <img
            src={url}
            alt={file.name}
            className="max-h-full max-w-full object-contain"
            onLoad={(e) => onImageMeasured?.(e.currentTarget.naturalWidth, e.currentTarget.naturalHeight)}
          />
        </div>
      )

    case 'pdf':
      // Visionneuse PDF native du navigateur.
      return <iframe src={url} title={file.name} className="h-full w-full border-0" />

    case 'video':
      return (
        <div className="flex min-h-full items-center justify-center bg-black p-2">
          <video src={url} controls className="max-h-full max-w-full" />
        </div>
      )

    case 'audio':
      return (
        <div className="flex min-h-full flex-col items-center justify-center gap-4 p-6">
          <span className="text-4xl opacity-60">🎵</span>
          <audio src={url} controls className="w-full max-w-md" />
        </div>
      )

    case 'markdown':
      if (textError) return <FallbackCard loaded={loaded} note={textError} />
      if (text == null) return <Loading />
      return (
        <div className="p-4">
          <Markdown>{text}</Markdown>
        </div>
      )

    case 'docx':
    case 'sheet':
      if (textError) return <FallbackCard loaded={loaded} note={textError} />
      if (loaded.html == null) return <Loading />
      return (
        <div className="p-4">
          <div
            className="vf-doc text-[13px] leading-relaxed text-slate-800 dark:text-slate-100"
            dangerouslySetInnerHTML={{ __html: loaded.html }}
          />
        </div>
      )

    case 'text':
      if (textError) return <FallbackCard loaded={loaded} note={textError} />
      if (text == null) return <Loading />
      return (
        <pre className="m-0 whitespace-pre-wrap break-words p-4 font-mono text-[12px]
                        leading-relaxed text-slate-800 dark:text-slate-100">
          {text}
        </pre>
      )

    default:
      // Type non rendu nativement (docx, xlsx, zip, binaire…).
      return (
        <FallbackCard
          loaded={loaded}
          note="Aperçu intégré indisponible pour ce type de fichier. Ouvrez-le dans un nouvel onglet ou téléchargez-le pour l'examiner avec l'application adaptée."
        />
      )
  }
}

// Indicateur de chargement texte.
function Loading() {
  return (
    <div className="flex h-full items-center justify-center text-sm text-slate-400 dark:text-slate-500">
      <span className="animate-pulse">Lecture…</span>
    </div>
  )
}

// Fiche de repli : métadonnées du fichier + actions ouvrir/télécharger.
function FallbackCard({ loaded, note }: { loaded: Loaded; note: string }) {
  const { file, url } = loaded
  const ext = (file.name.split('.').pop() || '?').toUpperCase()
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
      <span className="flex h-16 w-16 items-center justify-center rounded-lg bg-navy-700 text-sm font-bold text-white">
        {ext.slice(0, 4)}
      </span>
      <div>
        <p className="text-sm font-medium text-slate-700 dark:text-slate-200">{file.name}</p>
        <p className="text-[11px] text-slate-400 dark:text-slate-500">
          {file.type || 'type inconnu'} · {humanSize(file.size)}
        </p>
      </div>
      <p className="max-w-xs text-[12px] text-slate-500 dark:text-slate-400">{note}</p>
      <div className="flex gap-2">
        <a
          href={url}
          target="_blank"
          rel="noreferrer"
          className="rounded-md bg-navy-700 px-3 py-1.5 text-[12px] font-medium text-white transition hover:bg-navy-600"
        >
          ↗ Ouvrir dans un onglet
        </a>
        <a
          href={url}
          download={file.name}
          className="rounded-md border border-slate-300 dark:border-ink-600 px-3 py-1.5 text-[12px]
                     font-medium text-slate-600 dark:text-slate-300 transition hover:bg-slate-100 dark:hover:bg-ink-700"
        >
          ⤓ Télécharger
        </a>
      </div>
    </div>
  )
}
