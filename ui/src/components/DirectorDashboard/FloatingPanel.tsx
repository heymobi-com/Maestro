import { useCallback, useEffect, useRef, useState } from 'react'
import type { CSSProperties, PointerEvent as ReactPointerEvent, ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { ChevronDown, ChevronUp, Search, X } from 'lucide-react'

/**
 * A floating, movable and resizable window.
 *
 * The Director Dashboard edits long compiled prompts inside a small card, which
 * is unreadable for a prompt that runs to several thousand characters. Reading
 * and editing it needs a window the user can size themselves, so this keeps the
 * box a user drags and resizes, and remembers it per `storageKey`.
 */

type Props = {
  title: string
  subtitle?: string
  onClose: () => void
  storageKey: string
  initialWidth?: number
  initialHeight?: number
  footer?: ReactNode
  /** Hand the whole body to one child (a textarea or a player) instead of a scrolling box. */
  fill?: boolean
  /** Show the A- / A+ text-size controls. For windows holding text to read. */
  resizableFont?: boolean
  /**
   * Show the search box in the top bar. A compiled prompt runs to thousands of
   * characters, so finding one misplaced ``(S3)`` or timestamp by eye is the slow
   * part of a manual edit.
   */
  search?: boolean
  children: ReactNode
}

type Box = { x: number; y: number; w: number; h: number }

type TextHit = { field: HTMLTextAreaElement; start: number; end: number }

/** Every occurrence of *needle* in the window's own textareas, in reading order. */
function findTextHits(
  root: HTMLElement | null,
  needle: string,
  caseSensitive: boolean,
): TextHit[] {
  const found: TextHit[] = []
  if (!root || !needle) return found
  const wanted = caseSensitive ? needle : needle.toLowerCase()
  const fields = Array.from(root.querySelectorAll<HTMLTextAreaElement>('textarea'))
  for (const field of fields) {
    const haystack = caseSensitive ? field.value : field.value.toLowerCase()
    let from = 0
    while (from <= haystack.length - wanted.length) {
      const at = haystack.indexOf(wanted, from)
      if (at < 0) break
      found.push({ field, start: at, end: at + needle.length })
      // A one-character needle would otherwise never advance past its own match.
      from = at + Math.max(1, wanted.length)
    }
  }
  return found
}

const MIN_WIDTH = 380
const MIN_HEIGHT = 240
const MARGIN = 8

// Text size for the body. A compiled prompt is read at length, so this is a
// reading setting, not a styling flourish: it is clamped, remembered, and
// reachable without a mouse wheel or a browser zoom that would also shrink the
// controls you need to change it back.
const DEFAULT_FONT = 12
const MIN_FONT = 9
const MAX_FONT = 28

function clampFont(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_FONT
  return Math.min(MAX_FONT, Math.max(MIN_FONT, Math.round(value)))
}

function loadFont(storageKey: string): number {
  try {
    const saved = window.localStorage.getItem(`director-float:${storageKey}`)
    if (!saved) return DEFAULT_FONT
    const parsed = JSON.parse(saved) as { font?: unknown }
    return typeof parsed.font === 'number' ? clampFont(parsed.font) : DEFAULT_FONT
  } catch {
    return DEFAULT_FONT
  }
}

function defaultBox(width: number, height: number): Box {
  const maxW = typeof window === 'undefined' ? width : window.innerWidth - MARGIN * 2
  const maxH = typeof window === 'undefined' ? height : window.innerHeight - MARGIN * 2
  const w = Math.min(width, Math.max(MIN_WIDTH, maxW))
  const h = Math.min(height, Math.max(MIN_HEIGHT, maxH))
  return {
    w,
    h,
    x: Math.round((window.innerWidth - w) / 2),
    y: Math.round(Math.max(MARGIN, (window.innerHeight - h) / 2 - 24)),
  }
}

function clamp(box: Box): Box {
  const w = Math.max(MIN_WIDTH, Math.min(box.w, window.innerWidth - MARGIN))
  const h = Math.max(MIN_HEIGHT, Math.min(box.h, window.innerHeight - MARGIN))
  // Keep the title bar reachable: never let the window be dragged fully offscreen.
  const x = Math.max(MARGIN - w + 96, Math.min(box.x, window.innerWidth - MARGIN - 96))
  const y = Math.max(MARGIN, Math.min(box.y, window.innerHeight - MARGIN - 40))
  return { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h) }
}

function loadBox(storageKey: string, width: number, height: number): Box {
  const fallback = defaultBox(width, height)
  try {
    const saved = window.localStorage.getItem(`director-float:${storageKey}`)
    if (!saved) return fallback
    const parsed = JSON.parse(saved) as Partial<Box>
    if (
      typeof parsed.x !== 'number' || typeof parsed.y !== 'number'
      || typeof parsed.w !== 'number' || typeof parsed.h !== 'number'
    ) {
      return fallback
    }
    return clamp(parsed as Box)
  } catch {
    return fallback
  }
}

export function FloatingPanel({
  title,
  subtitle,
  onClose,
  storageKey,
  initialWidth = 760,
  initialHeight = 560,
  footer,
  fill = false,
  resizableFont = false,
  search = false,
  children,
}: Props) {
    const [box, setBox] = useState<Box>(() => loadBox(storageKey, initialWidth, initialHeight))
    const [fontSize, setFontSize] = useState<number>(() => loadFont(storageKey))
    const dragRef = useRef<{ mode: 'move' | 'resize'; startX: number; startY: number; box: Box } | null>(null)
    const bodyRef = useRef<HTMLDivElement | null>(null)
    const searchInputRef = useRef<HTMLInputElement | null>(null)
    const [query, setQuery] = useState('')
    const [matchCount, setMatchCount] = useState(0)
    const [matchPosition, setMatchPosition] = useState(0)
    const [caseSensitive, setCaseSensitive] = useState(false)

    /**
     * Select the match in its own textarea and bring it into view.
     *
     * Writing into a textarea is not how these prompts are read, so the caret is
     * put on the match itself: the browser then scrolls that line into view and
     * the user can type over the mistake right away. Focus returns to the search
     * box so Enter keeps cycling, and an unfocused selection is still drawn.
     */
    const showHit = useCallback((hit: TextHit) => {
      hit.field.focus()
      hit.field.setSelectionRange(hit.start, hit.end)
      searchInputRef.current?.focus({ preventScroll: true })
    }, [])

    const runSearch = useCallback((direction: 1 | -1) => {
      const hits = findTextHits(bodyRef.current, query, caseSensitive)
      setMatchCount(hits.length)
      if (!hits.length) {
        setMatchPosition(0)
        return
      }
      const next = (matchPosition - 1 + direction + hits.length) % hits.length
      setMatchPosition(next + 1)
      showHit(hits[next])
    }, [caseSensitive, matchPosition, query, showHit])

    // Searching as the user types: the first hit is shown and the count updates,
    // which is what tells a reader that an element appears more than once.
    useEffect(() => {
      if (!search) return
      const hits = findTextHits(bodyRef.current, query, caseSensitive)
      setMatchCount(hits.length)
      setMatchPosition(hits.length ? 1 : 0)
      if (hits.length) showHit(hits[0])
    }, [search, query, caseSensitive, showHit])

    // Editing the prompt changes how many times the query appears. Count only:
    // jumping while someone types would fight their own caret.
    useEffect(() => {
      if (!search) return
      const root = bodyRef.current
      if (!root) return
      const onInput = () => {
        setMatchCount(findTextHits(root, query, caseSensitive).length)
      }
      root.addEventListener('input', onInput)
      return () => root.removeEventListener('input', onInput)
    }, [search, query, caseSensitive])
    useEffect(() => {
      // Debounced: this used to write on every pointer move of a drag, which is a
      // synchronous disk write per mouse event.
      const timer = window.setTimeout(() => {
        try {
          window.localStorage.setItem(
            `director-float:${storageKey}`,
            JSON.stringify({ ...box, font: fontSize }),
          )
        } catch {
          // Private mode or a full quota: the window simply forgets its settings.
        }
      }, 250)
      return () => window.clearTimeout(timer)
    }, [storageKey, box, fontSize])

  useEffect(() => {
    const onResize = () => setBox(current => clamp(current))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (search && document.activeElement === searchInputRef.current) {
          // The first Escape leaves the box; a second one closes the window,
          // which is what someone who is still searching expects.
          event.preventDefault()
          searchInputRef.current?.blur()
          return
        }
        onClose()
        return
      }
      if (search && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f') {
        event.preventDefault()
        searchInputRef.current?.focus()
        searchInputRef.current?.select()
        return
      }
      if (!resizableFont || !(event.ctrlKey || event.metaKey)) return
      // Ctrl + / Ctrl - / Ctrl 0, the sizes a reader already expects.
      if (event.key === '+' || event.key === '=') {
        event.preventDefault()
        setFontSize(current => clampFont(current + 1))
      } else if (event.key === '-') {
        event.preventDefault()
        setFontSize(current => clampFont(current - 1))
      } else if (event.key === '0') {
        event.preventDefault()
        setFontSize(DEFAULT_FONT)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, resizableFont, search])

  useEffect(() => {
    const onMove = (event: PointerEvent) => {
      const drag = dragRef.current
      if (!drag) return
      const dx = event.clientX - drag.startX
      const dy = event.clientY - drag.startY
      setBox(clamp(drag.mode === 'move'
        ? { ...drag.box, x: drag.box.x + dx, y: drag.box.y + dy }
        : { ...drag.box, w: drag.box.w + dx, h: drag.box.h + dy }))
    }
    const onUp = () => { dragRef.current = null }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
    }
  }, [])

  const startDrag = (mode: 'move' | 'resize') => (event: ReactPointerEvent) => {
    if (event.button !== 0) return
    dragRef.current = { mode, startX: event.clientX, startY: event.clientY, box }
  }

  const style: CSSProperties = {
    position: 'fixed',
    left: box.x,
    top: box.y,
    width: box.w,
    height: box.h,
  }

  return createPortal(
    <div
      role="dialog"
      aria-label={title}
      style={style}
      className="z-[300] flex flex-col overflow-hidden rounded-lg border border-border bg-bg-secondary shadow-2xl"
    >
      <div
        onPointerDown={startDrag('move')}
        className="flex items-center justify-between gap-2 px-3 py-1.5 bg-bg-tertiary border-b border-border cursor-move select-none shrink-0"
      >
        <div className="min-w-0">
          <div className="text-xs font-medium text-text-primary truncate">{title}</div>
          {subtitle && (
            <div className="text-[9px] text-text-muted truncate" title={subtitle}>{subtitle}</div>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {resizableFont && (
            // Kept out of the drag handler: pressing these must never start a move.
            <div
              role="group"
              aria-label="Text size"
              onPointerDown={event => event.stopPropagation()}
              className="flex items-center gap-0.5 rounded border border-border bg-bg-secondary px-0.5 py-0.5"
            >
              <button
                onClick={() => setFontSize(current => clampFont(current - 1))}
                disabled={fontSize <= MIN_FONT}
                className="px-1 rounded text-[10px] text-text-muted hover:text-text-primary disabled:opacity-30 transition-colors"
                title="Smaller text (Ctrl -)"
                aria-label="Smaller text"
              >A-</button>
              <button
                onClick={() => setFontSize(DEFAULT_FONT)}
                className="px-1 rounded text-[9px] tabular-nums text-text-muted hover:text-text-primary transition-colors"
                title="Reset text size (Ctrl 0)"
                aria-label={`Reset text size, currently ${fontSize} pixels`}
              >{fontSize}</button>
              <button
                onClick={() => setFontSize(current => clampFont(current + 1))}
                disabled={fontSize >= MAX_FONT}
                className="px-1 rounded text-[11px] text-text-muted hover:text-text-primary disabled:opacity-30 transition-colors"
                title="Larger text (Ctrl +)"
                aria-label="Larger text"
              >A+</button>
            </div>
          )}
          <button
            onClick={onClose}
            onPointerDown={event => event.stopPropagation()}
            className="p-0.5 rounded text-text-muted hover:text-text-primary transition-colors shrink-0"
            title="Close (Esc)"
            aria-label="Close"
          >
            <X size={13} />
          </button>
        </div>
      </div>

{search && (
          // Kept out of the drag handler: typing here must never move the window.
          <div
            onPointerDown={event => event.stopPropagation()}
            className="flex items-center gap-1.5 px-2.5 py-1 border-b border-border bg-bg-tertiary/40 shrink-0"
          >
            <Search size={12} className="text-text-muted shrink-0" aria-hidden="true" />
            <input
              ref={searchInputRef}
              value={query}
              onChange={event => setQuery(event.target.value)}
              onKeyDown={event => {
                if (event.key !== 'Enter') return
                event.preventDefault()
                runSearch(event.shiftKey ? -1 : 1)
              }}
              placeholder="Buscar en el texto (Ctrl+F)"
              aria-label="Buscar en el texto"
              className="flex-1 min-w-0 bg-bg-secondary border border-border rounded px-1.5 py-0.5 text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-blue"
            />
            <button
              onClick={() => setCaseSensitive(current => !current)}
              aria-pressed={caseSensitive}
              title={caseSensitive ? 'Coincidir mayúsculas y minúsculas' : 'Ignorar mayúsculas y minúsculas'}
              aria-label="Coincidir mayúsculas y minúsculas"
              className={`px-1 rounded text-[10px] transition-colors ${caseSensitive ? 'text-accent-blue bg-accent-blue/15' : 'text-text-muted hover:text-text-primary'}`}
            >Aa</button>
            <span
              className="text-[10px] tabular-nums text-text-muted w-12 text-right shrink-0"
              title={query && !matchCount ? 'Sin coincidencias' : undefined}
            >{query ? `${matchPosition}/${matchCount}` : ''}</span>
            <button
              onClick={() => runSearch(-1)}
              disabled={!matchCount}
              title="Coincidencia anterior (Shift+Enter)"
              aria-label="Coincidencia anterior"
              className="p-0.5 rounded text-text-muted hover:text-text-primary disabled:opacity-30 transition-colors shrink-0"
            ><ChevronUp size={12} /></button>
            <button
              onClick={() => runSearch(1)}
              disabled={!matchCount}
              title="Coincidencia siguiente (Enter)"
              aria-label="Coincidencia siguiente"
              className="p-0.5 rounded text-text-muted hover:text-text-primary disabled:opacity-30 transition-colors shrink-0"
            ><ChevronDown size={12} /></button>
          </div>
        )}

        <div
          ref={bodyRef}
        className={`flex-1 min-h-0 p-2.5 ${fill ? 'flex flex-col' : 'overflow-auto'}`}
        style={{ fontSize: `${fontSize}px` }}
      >{children}</div>

      {footer && (
        <div className="shrink-0 border-t border-border px-2.5 py-1.5 flex items-center justify-end gap-2 bg-bg-tertiary/60">
          {footer}
        </div>
      )}

      {/* Drag the corner to resize. The window size is remembered per clip. */}
      <div
        onPointerDown={startDrag('resize')}
        className="absolute bottom-0 right-0 w-4 h-4 cursor-nwse-resize"
        title="Drag to resize"
        aria-hidden="true"
        style={{
          background:
            'linear-gradient(135deg, transparent 45%, var(--color-border) 45%, var(--color-border) 55%, transparent 55%, transparent 70%, var(--color-border) 70%, var(--color-border) 80%, transparent 80%)',
        }}
      />
    </div>,
    document.body,
  )
}
