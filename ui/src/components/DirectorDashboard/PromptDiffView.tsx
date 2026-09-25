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
  /** What this comparison covers, when it is only part of the prompt. */
  scope?: string
  /** Identifies the shot, so the reader's height and layout are remembered for it. */
  persistKey?: string
  onApply: () => void
  onBack: () => void
}

// The two columns are long prose, so the box they sit in is the reader's to size:
// it never shrinks below this, it can be dragged taller, and the height and the
// layout come back the next time this shot is compared.
const MIN_COMPARE_PX = 240
const DEFAULT_COMPARE_PX = 420
const HEIGHT_KEY = 'maestro.promptDiff.height.'
const STACKED_KEY = 'maestro.promptDiff.stacked.'

function loadNumber(key: string, fallback: number): number {
  if (typeof window === 'undefined') return fallback
  const stored = Number(window.localStorage.getItem(key))
  return Number.isFinite(stored) && stored >= MIN_COMPARE_PX ? stored : fallback
}

function loadFlag(key: string): boolean {
  if (typeof window === 'undefined') return false
  return window.localStorage.getItem(key) === '1'
}

function save(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // A full or blocked storage must not stop the comparison from working.
  }
}

// Removed text is struck through as well as tinted: colour alone would not
// survive a colour-blind reader or a dim screen.
const REMOVED = 'bg-chip-red/20 text-chip-red line-through decoration-chip-red/60'
const ADDED = 'bg-accent-green/20 text-accent-green'

const GAP = <span className="text-text-muted select-none">…</span>

export function PromptDiffView({
  before, after, applied, canApply = true, note, scope, persistKey, onApply, onBack,
}: Props) {
  const diff = useMemo(() => diffPrompts(before, after), [before, after])
  const [showAll, setShowAll] = useState(false)
  const [stacked, setStacked] = useState(() => loadFlag(STACKED_KEY + (persistKey || '')))
  const [compareHeight, setCompareHeight] = useState(() =>
    loadNumber(HEIGHT_KEY + (persistKey || ''), DEFAULT_COMPARE_PX))

  // One column per row keeps a narrow window readable: side by side, a 30-word
  // sentence wrapped into two 300-pixel strips is where the change got lost.
  const toggleStacked = () => setStacked(value => {
    save(STACKED_KEY + (persistKey || ''), value ? '0' : '1')
    return !value
  })

  const startResize = (event: React.PointerEvent) => {
    event.preventDefault()
    const startY = event.clientY
    const startHeight = compareHeight
    const onMove = (move: PointerEvent) => {
      const next = Math.max(MIN_COMPARE_PX, Math.round(startHeight + move.clientY - startY))
      setCompareHeight(next)
      save(HEIGHT_KEY + (persistKey || ''), String(next))
    }
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

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

  const column = (region: DiffRegion, side: 'left' | 'right', oneAbove: boolean) => (
    <div
      className={
        oneAbove
          ? 'min-w-0 px-2 py-1.5 whitespace-pre-wrap break-words text-[13px] leading-relaxed text-text-primary'
          : side === 'left'
            ? 'flex-1 min-w-0 px-2 py-1.5 border-r border-border whitespace-pre-wrap break-words text-[13px] leading-relaxed text-text-primary'
            : 'flex-1 min-w-0 px-2 py-1.5 whitespace-pre-wrap break-words text-[13px] leading-relaxed text-text-primary'
      }
    >
      {oneAbove && (
        <div className="mb-0.5 text-[12px] text-text-muted">{side === 'left' ? 'Actual' : 'Propuesta'}</div>
      )}
      {region.truncatedBefore && GAP}
      {marked(region[side], side === 'left' ? 'removed' : 'added')}
      {region.truncatedAfter && GAP}
    </div>
  )

  // Stacked, each side is full width and carries its own label; side by side, the
  // one header above the box names both columns.
  const regionBody = (region: DiffRegion) => (stacked ? (
    <div className="flex flex-col">
      {column(region, 'left', true)}
      <div className="h-px bg-border/60" />
      {column(region, 'right', true)}
    </div>
  ) : (
    <div className="flex">
      {column(region, 'left', false)}
      {column(region, 'right', false)}
    </div>
  ))

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
          {scope && <span className="text-[12px] text-text-muted">{scope}</span>}
          <button
            onClick={toggleStacked}
            className="px-2 py-1 rounded text-[12px] text-text-muted hover:text-text-primary transition-colors"
            title="Poner una columna encima de la otra para leer frases largas"
          >{stacked ? 'Lado a lado' : 'Apilado'}</button>
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

      {/* The height is fixed by the reader, never by how much text arrived: the box used
          to be a flex child with min-h-0, so a long analysis above it or a short window
          collapsed it to nothing and the two fields could not be read at all. */}
      <div
        className="flex min-h-[240px] flex-col rounded border border-border bg-bg-tertiary/40"
        style={{ height: `${compareHeight}px`, resize: 'vertical', overflow: 'hidden' }}
      >
        {!stacked && (
          <div className="shrink-0 flex border-b border-border text-[12px] text-text-muted">
            <div className="flex-1 min-w-0 px-2 py-1 border-r border-border">Actual</div>
            <div className="flex-1 min-w-0 px-2 py-1">Propuesta</div>
          </div>
        )}
        <div className="flex-1 min-h-0 overflow-auto divide-y divide-border/60">
          {regions.map(region => (
            <div key={region.index}>
              {pruned && (
                <div className="px-2 py-1 text-[12px] text-accent-blue bg-accent-blue/5">
                  Cambio {region.index} de {diff.regions.length}
                  {region.field ? ` · en ${region.field}` : ''}
                </div>
              )}
              {regionBody(region)}
            </div>
          ))}
        </div>
        {/* A native corner handle is not discoverable at this width, so the grab area
            is explicit: drag it to give the prompt as much height as it needs. */}
        <div
          onPointerDown={startResize}
          role="separator"
          aria-label="Cambiar la altura de la comparación"
          title="Arrastra para cambiar la altura"
          className="h-2 shrink-0 cursor-ns-resize rounded-b bg-bg-tertiary/60 hover:bg-accent-blue/20"
        />
      </div>
    </div>
  )
}
