import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { UIEvent } from 'react'
import { markPieces } from '../../lib/promptMarks'
import { usePanelSearch } from './panelSearch'

/**
 * A textarea whose matches are painted, the way a find box paints them.
 *
 * A textarea cannot style its own text, so the marks live in a layer behind it:
 * the mirror draws the coloured rectangles and the textarea draws the text on top
 * of them. That only works if the two agree on the exact box, which is why the
 * mirror copies the padding, the wrapping and the content width -- a textarea
 * showing a scrollbar is narrower by that width, and that difference alone is what
 * makes a highlight drift on long, wrapped lines.
 */

type Props = {
  value: string
  onChange: (value: string) => void
  ariaLabel?: string
}

const MATCH = 'bg-accent-blue/25 text-transparent'
const ACTIVE = 'bg-chip-yellow/45 text-transparent'

export function HighlightedTextarea({ value, onChange, ariaLabel }: Props) {
  // The element is held in state through a callback ref, which is what lets the
  // marks be derived during render: reading a ref there is a render side effect
  // React rejects, and deriving them in an effect cascades renders.
  const [area, setArea] = useState<HTMLTextAreaElement | null>(null)
  const mirrorRef = useRef<HTMLDivElement | null>(null)
  // The window's search box, when this textarea sits in one.
  const search = usePanelSearch()

  /**
   * Give the mirror the same content width as the textarea.
   *
   * A textarea showing a scrollbar is narrower by that width, and that difference
   * alone is what makes a highlight drift on long, wrapped lines.
   */
  const syncWidth = useCallback(() => {
    const mirror = mirrorRef.current
    if (!area || !mirror) return
    mirror.style.paddingRight = `${Math.max(0, area.offsetWidth - area.clientWidth)}px`
  }, [area])

  useLayoutEffect(syncWidth, [syncWidth, value])
  useEffect(() => {
    window.addEventListener('resize', syncWidth)
    return () => window.removeEventListener('resize', syncWidth)
  }, [syncWidth])

  const pieces = useMemo(() => markPieces(
    value,
    search && area ? search.matchesFor(area) : [],
    search && area ? search.activeFor(area) : -1,
  ), [value, search, area])

  const onScroll = useCallback((event: UIEvent<HTMLTextAreaElement>) => {
    const mirror = mirrorRef.current
    if (!mirror) return
    mirror.scrollTop = event.currentTarget.scrollTop
    mirror.scrollLeft = event.currentTarget.scrollLeft
  }, [])

  return (
    <div className="relative flex-1 min-h-0 rounded border border-border bg-bg-tertiary focus-within:border-accent-blue">
      <div
        ref={mirrorRef}
        aria-hidden="true"
        className="absolute inset-0 overflow-hidden px-2 py-1.5 leading-relaxed whitespace-pre-wrap break-words text-transparent select-none pointer-events-none"
      >
        {pieces.map((piece, index) => (
          piece.mark === 'none'
            ? <span key={index}>{piece.text}</span>
            : <mark key={index} className={piece.mark === 'active' ? ACTIVE : MATCH}>{piece.text}</mark>
        ))}
        {/* A trailing newline keeps the mirror as tall as the textarea's last line. */}
        {'\n'}
      </div>

      <textarea
        ref={setArea}
        value={value}
        onChange={event => onChange(event.target.value)}
        onScroll={onScroll}
        aria-label={ariaLabel}
        spellCheck={false}
        style={{ font: 'inherit' }}
        className="absolute inset-0 w-full h-full resize-none bg-transparent px-2 py-1.5 leading-relaxed whitespace-pre-wrap break-words text-text-primary focus:outline-none"
      />
    </div>
  )
}
