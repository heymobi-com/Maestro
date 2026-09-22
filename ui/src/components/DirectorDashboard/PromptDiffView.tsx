import { useMemo } from 'react'
import { diffPrompts } from '../../lib/promptDiff'

/**
 * The current prompt and the proposed one, side by side and marked up.
 *
 * A correction is accepted or rejected on what it changes, and the prose of a
 * compiled prompt is long enough that the change is invisible otherwise: the
 * assistant's answer only says what it thinks it did. Marking the added and
 * removed text is what turns "the note was applied" into a fact the director can
 * check before spending a render on it.
 */

type Props = {
  before: string
  after: string
  /** True once the proposal is in the editor, so applying twice is not offered. */
  applied: boolean
  onApply: () => void
  onBack: () => void
}

// Removed text is struck through as well as tinted: colour alone would not
// survive a colour-blind reader or a dim screen.
const REMOVED = 'bg-chip-red/20 text-chip-red line-through decoration-chip-red/60'
const ADDED = 'bg-accent-green/20 text-accent-green'

export function PromptDiffView({ before, after, applied, onApply, onBack }: Props) {
  const diff = useMemo(() => diffPrompts(before, after), [before, after])

  return (
    <div className="flex-1 min-h-0 flex flex-col gap-1.5">
      <div className="shrink-0 flex items-center gap-2 flex-wrap">
        <span className="text-[10px] text-text-muted">
          {diff.changed
            ? `${diff.changed} cambio(s) · +${diff.added} / −${diff.removed} caracteres`
            : 'La propuesta no cambia nada'}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={onBack}
            className="px-2 py-1 rounded text-[10px] text-text-muted hover:text-text-primary transition-colors"
          >Volver al editor</button>
          <button
            onClick={onApply}
            disabled={applied || !diff.changed}
            className="px-2 py-1 rounded text-[10px] bg-accent-blue/15 text-accent-blue hover:bg-accent-blue/25 transition-colors disabled:opacity-40"
          >{applied ? 'Aplicado' : 'Aplicar al editor'}</button>
        </div>
      </div>

      <div className="flex-1 min-h-0 flex gap-2">
        <section className="flex-1 min-w-0 flex flex-col rounded border border-border bg-bg-tertiary/40">
          <header className="shrink-0 px-2 py-0.5 text-[10px] text-text-muted border-b border-border">
            Actual
          </header>
          <div className="flex-1 min-h-0 overflow-auto px-2 py-1.5 whitespace-pre-wrap break-words leading-relaxed text-text-primary">
            {diff.left.map((segment, index) => (
              segment.kind === 'removed'
                ? <mark key={index} className={REMOVED}>{segment.text}</mark>
                : <span key={index}>{segment.text}</span>
            ))}
          </div>
        </section>

        <section className="flex-1 min-w-0 flex flex-col rounded border border-border bg-bg-tertiary/40">
          <header className="shrink-0 px-2 py-0.5 text-[10px] text-text-muted border-b border-border">
            Propuesta
          </header>
          <div className="flex-1 min-h-0 overflow-auto px-2 py-1.5 whitespace-pre-wrap break-words leading-relaxed text-text-primary">
            {diff.right.map((segment, index) => (
              segment.kind === 'added'
                ? <mark key={index} className={ADDED}>{segment.text}</mark>
                : <span key={index}>{segment.text}</span>
            ))}
          </div>
        </section>
      </div>
    </div>
  )
}
