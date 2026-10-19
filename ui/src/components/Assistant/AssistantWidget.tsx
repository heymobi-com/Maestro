/**
 * The assistant, available over every area of the studio.
 *
 * It is a floating panel rather than a page because the questions it exists for are asked from
 * where the problem is: a clip that looks wrong, a log line, a setting that does not do what it
 * says. So it travels over the Studio, the Director, the Editor and the settings, and it asks
 * the store where the person is standing -- area, open project, pipeline -- because an answer
 * about "this shot" is only useful if it knows which project that is.
 *
 * Nothing here writes to a project. An edit the assistant suggests arrives as a proposal with
 * its own Apply button, and the checks that decide whether it may be applied at all run on the
 * server, against the same contract the clip editor uses.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties, PointerEvent as ReactPointerEvent } from 'react'

import {
  applyAssistantProposal,
  askAssistant,
  fetchAssistantSettings,
  uploadAssistantImage,
  type AssistantProposal,
  type AssistantSettings,
  type AssistantTurn,
} from '../../api/assistant'
import { useStore } from '../../stores/useStore'

type Bubble = { who: 'you' | 'assistant' | 'system'; text: string; proposals?: AssistantProposal[] }

const PANEL = 'fixed z-50 h-[min(70vh,640px)] w-[min(94vw,420px)] '
  + 'rounded-2xl border border-border bg-bg-secondary shadow-2xl flex flex-col overflow-hidden'
// The launcher keeps the corner until it is dragged; after that its spot is remembered.
const LAUNCHER = 'fixed z-50 rounded-full bg-cta px-3.5 py-2 text-xs font-semibold '
  + 'text-white shadow-accent-glow hover:brightness-110'
const CORNER = { right: '1rem', bottom: '1rem' } as const
const STORAGE_KEY = 'maestro.assistant.spot'
const EDGE = 8
// A pointer that moved less than this was a click, not a drag: a drag ends where the button
// lands, and a click opens the panel, and the two must not be confused.
const DRAG_THRESHOLD = 4

type Spot = { x: number; y: number }

function clampToViewport(x: number, y: number, width: number, height: number): Spot {
  const maxX = Math.max(EDGE, window.innerWidth - width - EDGE)
  const maxY = Math.max(EDGE, window.innerHeight - height - EDGE)
  return {
    x: Math.min(Math.max(EDGE, x), maxX),
    y: Math.min(Math.max(EDGE, y), maxY),
  }
}

function storedSpot(): Spot | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Spot
    return typeof parsed?.x === 'number' && typeof parsed?.y === 'number' ? parsed : null
  } catch {
    return null
  }
}

/** Drag a fixed element by its own body, and keep it on screen. */
function useDraggableSpot() {
  const [spot, setSpot] = useState<Spot | null>(() => storedSpot())
  const drag = useRef<{ dx: number; dy: number; moved: boolean } | null>(null)
  // Read by the click that follows a gesture: a drag ends where the button lands, a click opens
  // the panel, and pointerup always fires before the click they belong to.
  const lastMove = useRef(false)

  const start = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    if (event.button !== 0) return
    // A control inside the drag handle keeps its own behaviour.
    if ((event.target as HTMLElement).closest('button[data-no-drag]')) return
    const rect = event.currentTarget.getBoundingClientRect()
    drag.current = {
      dx: event.clientX - rect.left,
      dy: event.clientY - rect.top,
      moved: false,
    }
    lastMove.current = false
    event.currentTarget.setPointerCapture(event.pointerId)
  }, [])

  const move = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    const held = drag.current
    if (!held) return
    const rect = event.currentTarget.getBoundingClientRect()
    if (!held.moved
      && Math.abs(event.clientX - (rect.left + held.dx)) < DRAG_THRESHOLD
      && Math.abs(event.clientY - (rect.top + held.dy)) < DRAG_THRESHOLD) return
    held.moved = true
    setSpot(clampToViewport(
      event.clientX - held.dx, event.clientY - held.dy, rect.width, rect.height,
    ))
  }, [])

  /** Whether the gesture that just ended was a drag, so a click can be told from it. */
  const wasDragged = useCallback(() => lastMove.current, [])

  const end = useCallback(() => {
    const moved = drag.current?.moved === true
    drag.current = null
    lastMove.current = moved
    if (!moved) return
    setSpot(current => {
      if (current) {
        try {
          window.localStorage.setItem(STORAGE_KEY, JSON.stringify(current))
        } catch {
          // A browser with storage disabled still drags; it just forgets after a reload.
        }
      }
      return current
    })
  }, [])

  const style: CSSProperties = spot ? { left: spot.x, top: spot.y } : { ...CORNER }
  const dragProps = {
    onPointerDown: start,
    onPointerMove: move,
    onPointerUp: end,
    onPointerCancel: end,
    style: { touchAction: 'none' as const },
  }

  /**
   * Bring a freshly opened panel back on screen.
   *
   * The spot is shared by the launcher and the panel, and they are not the same size: a button
   * parked in a corner leaves a 420px panel running off the edge. The clamp needs the panel's
   * own measurements, so it happens once it is mounted rather than when the spot was written.
   */
  const fitInto = useCallback((element: HTMLElement | null) => {
    if (!element) return
    const rect = element.getBoundingClientRect()
    setSpot(current => current && clampToViewport(current.x, current.y, rect.width, rect.height))
  }, [])

  return { spot, style, wasDragged, fitInto, dragProps }
}

export function AssistantWidget() {
  const [open, setOpen] = useState(false)
  const [bubbles, setBubbles] = useState<Bubble[]>([])
  const [draft, setDraft] = useState('')
  const [images, setImages] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [settings, setSettings] = useState<AssistantSettings | null>(null)
  const [applied, setApplied] = useState<Record<string, string>>({})
  const scrollRef = useRef<HTMLDivElement>(null)
  const panelRef = useRef<HTMLElement>(null)
  const launcherRef = useRef<HTMLButtonElement>(null)
  const { style, wasDragged, fitInto, dragProps } = useDraggableSpot()

  // Where the person is standing, read from the store rather than guessed from the DOM.
  const sidebarMode = useStore(s => s.sidebarMode)
  const activeWorkspace = useStore(s => s.activeWorkspace)
  const pipelineId = useStore(s => s.pipelineId)
  const context = useMemo(() => ({
    area: String(sidebarMode || 'studio'),
    workspace: activeWorkspace || null,
    pipeline_id: pipelineId || null,
  }), [sidebarMode, activeWorkspace, pipelineId])

  useEffect(() => {
    if (!open) return
    let live = true
    fetchAssistantSettings()
      .then(value => { if (live) setSettings(value) })
      .catch(() => { if (live) setSettings(null) })
    return () => { live = false }
  }, [open])

  useEffect(() => {
    const node = scrollRef.current
    if (node) node.scrollTop = node.scrollHeight
  }, [bubbles, busy])

  // A panel opened from a corner-parked button would otherwise run off the edge.
  useEffect(() => {
    if (open) fitInto(panelRef.current)
  }, [open, fitInto])

  // A window that shrank can leave either of them off-screen. The element's own size is what the
  // clamp needs, which is why this lives here and not in the hook.
  useEffect(() => {
    const onResize = () => fitInto(open ? panelRef.current : launcherRef.current)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [open, fitInto])

  const history = useCallback((): AssistantTurn[] => (
    bubbles
      .filter(bubble => bubble.who !== 'system')
      .map(bubble => ({
        role: bubble.who === 'you' ? 'user' as const : 'assistant' as const,
        content: bubble.text,
      }))
  ), [bubbles])

  const send = useCallback(async () => {
    const question = draft.trim()
    if (!question || busy) return
    const attached = images
    setDraft('')
    setImages([])
    setError('')
    setBusy(true)
    setBubbles(current => [...current, { who: 'you', text: question }])
    try {
      const reply = await askAssistant(
        [...history(), { role: 'user', content: question }], context, attached,
      )
      setBubbles(current => [...current, {
        who: 'assistant',
        text: reply.reply || '(no answer)',
        proposals: reply.proposals,
      }])
    } catch (err) {
      const message = err instanceof Error ? err.message : 'The assistant did not answer.'
      setError(message)
      setBubbles(current => [...current, { who: 'system', text: message }])
    } finally {
      setBusy(false)
    }
  }, [draft, busy, images, history, context])

  const attach = useCallback(async (file: File) => {
    setError('')
    try {
      const path = await uploadAssistantImage(file)
      if (path) setImages(current => [...current, path].slice(0, 4))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The image could not be attached.')
    }
  }, [])

  const apply = useCallback(async (proposal: AssistantProposal) => {
    const key = `${proposal.kind}-${proposal.shot}`
    if (!context.pipeline_id) {
      setApplied(current => ({
        ...current,
        [key]: 'Open a Director project first: a proposal is applied to a saved plan.',
      }))
      return
    }
    try {
      await applyAssistantProposal(proposal, context.pipeline_id, context.workspace)
      setApplied(current => ({ ...current, [key]: `Applied to shot ${proposal.shot}.` }))
    } catch (err) {
      setApplied(current => ({
        ...current,
        [key]: err instanceof Error ? err.message : 'Could not apply.',
      }))
    }
  }, [context])

  if (!open) {
    return (
      <button
        ref={launcherRef}
        type="button"
        {...dragProps}
        onClick={event => {
          // A gesture that moved places the button; only a click opens the panel. A keyboard
          // activation reports detail 0 and raises no pointer events at all, so it is a click.
          if (event.detail === 0 || !wasDragged()) setOpen(true)
        }}
        title="Ask the assistant — drag to move it"
        aria-label="Ask the assistant"
        style={{ ...style, ...dragProps.style }}
        className={LAUNCHER}
      >
        Assistant
      </button>
    )
  }

  return (
    <section ref={panelRef} className={PANEL} style={style} aria-label="Assistant">
      <header
        {...dragProps}
        className="flex cursor-grab items-center gap-2 border-b border-border px-3 py-2 active:cursor-grabbing"
        title="Drag to move the assistant"
      >
        <span className="text-xs font-medium text-text-primary">Assistant</span>
        <span className="rounded bg-bg-tertiary px-1.5 py-0.5 text-[10px] text-text-muted">
          {context.area}
        </span>
        {settings && (
          <span
            className="truncate text-[10px] text-text-muted"
            title={`${settings.provider}${settings.model ? ` · ${settings.model}` : ''}`}
          >
            {settings.model || settings.provider}
            {settings.routes_off_machine ? ' · external' : ''}
          </span>
        )}
        <button
          type="button"
          data-no-drag
          onClick={() => setOpen(false)}
          className="ml-auto text-xs text-text-muted hover:text-text-primary"
          aria-label="Close the assistant"
        >
          ✕
        </button>
      </header>

      <div ref={scrollRef} className="flex-1 space-y-2.5 overflow-y-auto px-3 py-2.5">
        {bubbles.length === 0 && (
          <p className="text-[11px] text-text-muted">
            Ask about the project, a shot, a prompt or a failure in the log. You can attach an
            image. Edits are proposed, not applied: you decide.
          </p>
        )}
        {bubbles.map((bubble, index) => (
          <div key={index} className={bubble.who === 'you' ? 'text-right' : ''}>
            <div
              className={`inline-block max-w-[92%] whitespace-pre-wrap rounded-xl px-2.5 py-1.5
                text-[11px] leading-relaxed ${
                  bubble.who === 'you'
                    ? 'bg-accent-blue/15 text-text-primary'
                    : bubble.who === 'system'
                      ? 'bg-red-500/10 text-red-300'
                      : 'bg-bg-tertiary text-text-primary'
                }`}
            >
              {bubble.text}
            </div>
            {(bubble.proposals || []).map((proposal, proposalIndex) => (
              <ProposalCard
                key={proposalIndex}
                proposal={proposal}
                result={applied[`${proposal.kind}-${proposal.shot}`]}
                onApply={() => void apply(proposal)}
              />
            ))}
          </div>
        ))}
        {busy && <p className="text-[10px] text-text-muted">Thinking…</p>}
      </div>

      <div className="border-t border-border px-3 py-2">
        {images.length > 0 && (
          <p className="mb-1 text-[10px] text-text-muted">
            {images.length} image(s) attached ·{' '}
            <button type="button" className="underline" onClick={() => setImages([])}>
              remove
            </button>
          </p>
        )}
        {error && !busy && <p className="mb-1 text-[10px] text-red-400">{error}</p>}
        <div className="flex items-end gap-1.5">
          <label
            className="cursor-pointer text-sm text-text-muted hover:text-text-primary"
            title="Attach an image"
          >
            <input
              type="file"
              accept="image/*"
              className="hidden"
              onChange={event => {
                const file = event.target.files?.[0]
                if (file) void attach(file)
                event.target.value = ''
              }}
            />
            🖼
          </label>
          <textarea
            value={draft}
            rows={2}
            placeholder="Ask about this project, a shot, a prompt, or a log line…"
            onChange={event => setDraft(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                void send()
              }
            }}
            className="flex-1 resize-none rounded-lg border border-border bg-bg-tertiary px-2 py-1.5
              text-[11px] text-text-primary focus:outline-none focus:border-accent-blue"
          />
          <button
            type="button"
            onClick={() => void send()}
            disabled={busy || !draft.trim()}
            className="rounded-lg bg-accent-blue px-3 py-1.5 text-[11px] text-white disabled:opacity-40"
          >
            Send
          </button>
        </div>
      </div>
    </section>
  )
}

function ProposalCard({ proposal, result, onApply }: {
  proposal: AssistantProposal
  result?: string
  onApply: () => void
}) {
  if (proposal.error) {
    return (
      <div className="mt-1.5 rounded-lg border border-red-500/40 bg-red-500/5 p-2 text-left text-[10px] text-red-300">
        {proposal.error}
      </div>
    )
  }
  const refused = proposal.acceptable === false
  return (
    <div className="mt-1.5 space-y-1.5 rounded-lg border border-border bg-bg-secondary p-2 text-left">
      <p className="text-[10px] uppercase tracking-wider text-text-muted">
        {proposal.kind === 'tag' ? 'Proposed tag' : 'Proposed prompt'} · shot {proposal.shot}
      </p>
      {refused && (
        <p className="text-[10px] text-amber-300">
          The prompt contract refuses this as it stands:{' '}
          {(proposal.problems || []).join(' ')}
        </p>
      )}
      {proposal.prompt && (
        <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-bg-tertiary p-1.5 text-[10px] text-text-primary">
          {proposal.prompt}
        </pre>
      )}
      {proposal.tag && <p className="text-[11px] text-text-primary">{proposal.tag}</p>}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onApply}
          disabled={refused || Boolean(result)}
          className="rounded bg-accent-blue px-2 py-1 text-[10px] text-white disabled:opacity-40"
        >
          Apply to shot {proposal.shot}
        </button>
        {result && <span className="text-[10px] text-text-muted">{result}</span>}
      </div>
    </div>
  )
}
