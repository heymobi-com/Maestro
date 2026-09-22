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
   * Copy the geometry the browser actually gave the textarea onto the mirror.
   *
   * Inheriting the font was not enough. The app raises every textarea to 16px on
   * a window narrower than 768px (the iOS zoom-prevention rule), and `!important`
   * beats an inline `font-size: inherit`, so the two layers disagreed by 6.5px per
   * line and the marks walked away from the words they belong to -- which is what
   * "the highlight lands on the wrong text" was. Measuring beats assuming: the
   * mirror follows whatever the textarea ended up with, whatever set it.
   */
  const syncGeometry = useCallback(() => {
    const mirror = mirrorRef.current
    if (!area || !mirror) return
    const computed = window.getComputedStyle(area)
    for (const property of [
      'fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'lineHeight', 'letterSpacing',
      'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
      'whiteSpace', 'overflowWrap', 'wordBreak', 'tabSize',
    ] as const) {
      mirror.style[property] = computed[property]
    }
  }, [area])

  useLayoutEffect(syncGeometry, [syncGeometry, value])
  useEffect(() => {
    window.addEventListener('resize', syncGeometry)
    return () => window.removeEventListener('resize', syncGeometry)
  }, [syncGeometry])

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
    // The line height lives here, on the element both layers inherit from: the
    // textarea needs `font: inherit` to stop the browser giving it its own
    // monospace font, and that shorthand resets line-height too. Setting
    // `leading-relaxed` on the textarea or the mirror instead made the two layers
    // disagree by a few pixels per line, which is what walked the marks away from
    // the words they belong to.
    <div className="relative flex-1 min-h-0 rounded border border-border bg-bg-tertiary leading-relaxed focus-within:border-accent-blue">
      <div
        ref={mirrorRef}
        aria-hidden="true"
        className="absolute inset-0 overflow-hidden px-2 py-1.5 whitespace-pre-wrap break-words text-transparent select-none pointer-events-none"
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
        /*
         * The font is set as longhands, never with the `font` shorthand: the
         * shorthand resets line-height, and a textarea that wraps its lines at a
         * different height than the layer behind it is what walks a highlight away
         * from the words. The scrollbar is hidden for the same reason -- a reserved
         * scrollbar makes this element narrower than the mirror -- and the window is
         * resizable, so the wheel and the keyboard still scroll it.
         */
        style={{
          fontFamily: 'inherit',
          fontSize: 'inherit',
          fontWeight: 'inherit',
          fontStyle: 'inherit',
          lineHeight: 'inherit',
          letterSpacing: 'inherit',
        }}
        className="absolute inset-0 w-full h-full resize-none bg-transparent px-2 py-1.5 whitespace-pre-wrap break-words text-text-primary focus:outline-none [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      />
    </div>
  )
}
