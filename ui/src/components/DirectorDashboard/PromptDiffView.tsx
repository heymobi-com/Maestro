import { useMemo, useState } from 'react'
import { diffPrompts, type DiffRegion, type DiffSegment } from '../../lib/promptDiff'

/**
 * The current prompt and the proposed one, side by side and marked up.
 *
 * A correction is accepted or rejected on what it changes, and the prose of a
 * compiled prompt is long enough that the change is invisible otherwise: the
 * assistant's answer only says what it thinks it did. Marking the added and
 * removed text is what turns "the note was applied" into a fact the director can
 * check before spending a render on it.
 *
 * Marked-up whole columns were still unreadable: the six fields repeat verbatim
 * in every shot, so a measured one-word edit marks 0.1% of 6,184 characters and a
 * director has to hunt for it in two columns of near-identical text. The window
 * therefore opens on the changes alone -- each with the sentence it sits in and
 * the field it lands in -- and "Todo el prompt" brings the full text back.
 */

type Props = {
  before: string
  after: string
  /** True once the proposal is in the editor, so applying twice is not offered. */
  applied: boolean
  /** False when the proposal cannot be applied as it is (a windowed clip). */
  canApply?: boolean
  /** What to say instead of the apply button. */
  note?: string
  onApply: () => void
  onBack: () => void
}

// Removed text is struck through as well as tinted: colour alone would not
// survive a colour-blind reader or a dim screen.
const REMOVED = 'bg-chip-red/20 text-chip-red line-through decoration-chip-red/60'
const ADDED = 'bg-accent-green/20 text-accent-green'

const GAP = <span className="text-text-muted select-none">…</span>

export function PromptDiffView({
  before, after, applied, canApply = true, note, onApply, onBack,
}: Props) {
  const diff = useMemo(() => diffPrompts(before, after), [before, after])
  const [showAll, setShowAll] = useState(false)

  // The whole prompt is the single region that covers everything, so both modes
  // render through the same markup instead of diverging.
  const whole: DiffRegion = {
    index: 0,
    field: '',
    left: diff.left,
    right: diff.right,
    truncatedBefore: false,
    truncatedAfter: false,
  }
  const pruned = !showAll && diff.regions.length > 0
  const regions = pruned ? diff.regions : [whole]

  const marked = (segments: DiffSegment[], kind: 'removed' | 'added') =>
    segments.map((segment, index) => (
      segment.kind === kind
        ? <mark key={index} className={kind === 'removed' ? REMOVED : ADDED}>
            {segment.text}
          </mark>
        : <span key={index}>{segment.text}</span>
    ))

  const column = (region: DiffRegion, side: 'left' | 'right') => (
    <div
      className={
        side === 'left'
          ? 'flex-1 min-w-0 px-2 py-1.5 border-r border-border whitespace-pre-wrap break-words text-[13px] leading-relaxed text-text-primary'
          : 'flex-1 min-w-0 px-2 py-1.5 whitespace-pre-wrap break-words text-[13px] leading-relaxed text-text-primary'
      }
    >
      {region.truncatedBefore && GAP}
      {marked(region[side], side === 'left' ? 'removed' : 'added')}
      {region.truncatedAfter && GAP}
    </div>
  )

  return (
    <div className="flex-1 min-h-0 flex flex-col gap-1.5">
      <div className="shrink-0 flex items-center gap-2 flex-wrap">
        <span className="text-[12px] leading-snug text-text-muted">
          {diff.changed
            ? `${diff.changed} cambio(s) · ${diff.regions.length || 1} lugar(es) · `
              + `+${diff.added} / −${diff.removed} caracteres`
            : 'La propuesta no cambia nada'}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => setShowAll(value => !value)}
            className="px-2 py-1 rounded text-[12px] text-text-muted hover:text-text-primary transition-colors"
          >{showAll ? 'Solo cambios' : 'Todo el prompt'}</button>
          <button
            onClick={onBack}
            className="px-2 py-1 rounded text-[12px] text-text-muted hover:text-text-primary transition-colors"
          >Volver al editor</button>
          {canApply ? (
            <button
              onClick={onApply}
              disabled={applied || !diff.changed}
              className="px-2 py-1 rounded text-[12px] bg-accent-blue/15 text-accent-blue hover:bg-accent-blue/25 transition-colors disabled:opacity-40"
            >{applied ? 'Aplicado' : 'Aplicar al editor'}</button>
          ) : (
            <span className="text-[12px] text-text-muted">{note}</span>
          )}
        </div>
      </div>

      <div className="flex-1 min-h-0 flex flex-col rounded border border-border bg-bg-tertiary/40">
        <div className="shrink-0 flex border-b border-border text-[12px] text-text-muted">
          <div className="flex-1 min-w-0 px-2 py-1 border-r border-border">Actual</div>
          <div className="flex-1 min-w-0 px-2 py-1">Propuesta</div>
        </div>
        <div className="flex-1 min-h-0 overflow-auto divide-y divide-border/60">
          {regions.map(region => (
            <div key={region.index}>
              {pruned && (
                <div className="px-2 py-1 text-[12px] text-accent-blue bg-accent-blue/5">
                  Cambio {region.index} de {diff.regions.length}
                  {region.field ? ` · en ${region.field}` : ''}
                </div>
              )}
              <div className="flex">
                {column(region, 'left')}
                {column(region, 'right')}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
