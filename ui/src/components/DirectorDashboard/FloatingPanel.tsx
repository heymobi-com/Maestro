import { useEffect, useRef, useState } from 'react'
import type { CSSProperties, PointerEvent as ReactPointerEvent, ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'

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
  children: ReactNode
}

type Box = { x: number; y: number; w: number; h: number }

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
  children,
}: Props) {
  const [box, setBox] = useState<Box>(() => loadBox(storageKey, initialWidth, initialHeight))
  const [fontSize, setFontSize] = useState<number>(() => loadFont(storageKey))
  const dragRef = useRef<{ mode: 'move' | 'resize'; startX: number; startY: number; box: Box } | null>(null)

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
      if (event.key === 'Escape') onClose()
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
  }, [onClose, resizableFont])

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

      <div
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
