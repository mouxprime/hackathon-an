// Rendu markdown des sorties LLM / agents (synthèses de recherche, rapports).
//
// On délègue le parsing à react-markdown + remark-gfm (tableaux, titres ####,
// liens, séparateurs… — tout GFM), et on ne garde la main que sur le STYLE via
// la map `components` : les classes reprennent la charte navy/crème du chat pour
// que le rendu reste identique à l'ancien renderer maison, en plus complet.
//
// Sécurité : pas de `rehype-raw` → le HTML brut éventuellement présent dans le
// texte d'un agent/LLM n'est PAS interprété (échappé), pas d'injection possible.

import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

// Wrapper unique réutilisé par les widgets (ReportWidget, MarkdownWidget…).
export function Markdown({ children }: { children: string }) {
  return (
    <div className="space-y-0.5">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => <h3 className="mt-2 text-base font-bold text-navy-700 dark:text-navy-200">{children}</h3>,
          h2: ({ children }) => <h4 className="mt-2 text-sm font-semibold text-slate-800 dark:text-slate-100">{children}</h4>,
          h3: ({ children }) => <h5 className="mt-2 text-[13px] font-semibold text-slate-800 dark:text-slate-100">{children}</h5>,
          h4: ({ children }) => <h6 className="mt-2 text-[13px] font-semibold text-slate-700 dark:text-slate-200">{children}</h6>,
          h5: ({ children }) => <h6 className="mt-1.5 text-xs font-semibold text-slate-600 dark:text-slate-300">{children}</h6>,
          h6: ({ children }) => <h6 className="mt-1.5 text-xs font-semibold text-slate-600 dark:text-slate-300">{children}</h6>,
          p: ({ children }) => <p className="text-[13px] leading-snug text-slate-700 dark:text-slate-300">{children}</p>,
          ul: ({ children }) => <ul className="my-1 list-disc space-y-0.5 pl-5 text-[13px] text-slate-700 dark:text-slate-300">{children}</ul>,
          ol: ({ children }) => <ol className="my-1 list-decimal space-y-0.5 pl-5 text-[13px] text-slate-700 dark:text-slate-300">{children}</ol>,
          li: ({ children }) => <li>{children}</li>,
          strong: ({ children }) => <strong className="font-semibold text-slate-900 dark:text-slate-100">{children}</strong>,
          em: ({ children }) => <em className="italic text-slate-700 dark:text-slate-300">{children}</em>,
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noreferrer"
               className="text-navy-600 underline decoration-navy-300 underline-offset-2 hover:text-navy-700 dark:text-navy-300 dark:decoration-navy-500 dark:hover:text-navy-200">
              {children}
            </a>
          ),
          code: ({ children }) => (
            <code className="mono rounded bg-slate-100 px-1 py-px text-[12px] text-navy-700 dark:bg-ink-700 dark:text-navy-200">{children}</code>
          ),
          pre: ({ children }) => (
            <pre className="my-1.5 overflow-x-auto rounded-lg bg-slate-100 p-2 text-[12px] text-slate-800 dark:bg-ink-700 dark:text-slate-200">{children}</pre>
          ),
          hr: () => <hr className="my-2 border-slate-200 dark:border-ink-600" />,
          blockquote: ({ children }) => (
            <blockquote className="my-1 border-l-2 border-slate-300 pl-2 text-[13px] italic text-slate-500 dark:border-ink-500 dark:text-slate-400">{children}</blockquote>
          ),
          // Tableaux : conteneur scrollable horizontal pour les colonnes larges.
          table: ({ children }) => (
            <div className="my-1.5 overflow-x-auto">
              <table className="w-full border-collapse text-[12px]">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead className="bg-slate-50 dark:bg-ink-700">{children}</thead>,
          th: ({ children }) => (
            <th className="border border-slate-200 px-2 py-1 text-left font-semibold text-slate-700 dark:border-ink-600 dark:text-slate-200">{children}</th>
          ),
          td: ({ children }) => (
            <td className="border border-slate-200 px-2 py-1 align-top text-slate-600 dark:border-ink-600 dark:text-slate-300">{children}</td>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}
