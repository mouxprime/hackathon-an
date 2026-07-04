// Carte de repli quand le parse Zod d'un payload de widget échoue : message
// neutre (pas de jargon, pas d'accusation du backend) + détails techniques
// repliés pour le debug. Ne casse jamais le fil de conversation.

export function WidgetErrorCard({ details }: { details?: string }) {
  return (
    <div className="card border-l-4 border-l-slate-300 p-3 dark:border-l-ink-500">
      <p className="text-[12.5px] text-slate-600 dark:text-slate-300">
        ⚠️ Les données de ce widget sont illisibles — l'information textuelle de la
        réponse reste valable.
      </p>
      {details && (
        <details className="mt-1.5">
          <summary className="mono cursor-pointer select-none text-[10.5px] text-slate-400 dark:text-slate-500">
            Détails techniques
          </summary>
          <pre className="mono mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words text-[10px] leading-relaxed text-slate-500 dark:text-slate-400">
            {details}
          </pre>
        </details>
      )}
    </div>
  )
}
