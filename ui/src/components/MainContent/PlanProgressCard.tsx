import { useCallback, useEffect, useRef, useState } from 'react'
import { Loader2, Square } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import { formatGenerationDuration } from '../../lib/format'
import {
  cancelDirectorPlanOperation,
  fetchDirectorPlanOperation,
  type DirectorPlanOperation,
} from '../../api/client'

/** How often the card re-reads the planner's counters while a pass is live. */
const POLL_MS = 2000

const LABEL_NAMES: Record<string, string> = {
  music_video: 'music video',
  short_film: 'short film',
  podcast: 'podcast',
  viral_video: 'viral video',
}

function describeOperation(operation: DirectorPlanOperation): string {
  const label = LABEL_NAMES[operation.label] || operation.label.replace(/_/g, ' ')
  return label ? `Planning clips — ${label}` : 'Planning clips'
}

/**
 * Progress and Stop for the planning pass that runs before any clip exists.
 *
 * Planning a long timeline takes batches, and until this card existed the pass
 * was only visible as raw planner text inside the Director chat: there was no
 * counter on the main screen and no way to stop it, so an unwanted pass had to
 * be waited out or the backend restarted, which discarded the plan. The card
 * reads the planner's own batch counters, so what it shows is real progress
 * rather than an animation standing in for it.
 */
export function PlanProgressCard() {
  const directorLoading = useStore(s => s.directorLoading)
  const [operation, setOperation] = useState<DirectorPlanOperation | null>(null)
  const [stopping, setStopping] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const stoppingRef = useRef(false)

  useEffect(() => {
    let disposed = false
    let timer: number | undefined
    // A reload cannot know whether a pass is running, so the first tick always
    // asks the server; after that the card follows what the server reports or
    // what this window is waiting for.
    let keep = true

    const tick = async () => {
      try {
        const next = await fetchDirectorPlanOperation()
        if (disposed) return
        setOperation(next)
        keep = next !== null || directorLoading
        if (!next) {
          stoppingRef.current = false
          setStopping(false)
        }
      } catch {
        if (disposed) return
        // Unreachable backend: hold the last known card instead of making the
        // pass look finished, and only keep trying while work is expected.
        keep = directorLoading
      }
      if (keep && !disposed) timer = window.setTimeout(tick, POLL_MS)
    }

    void tick()
    return () => {
      disposed = true
      if (timer) window.clearTimeout(timer)
    }
  }, [directorLoading])

  const stopPass = useCallback(async () => {
    if (!operation || stoppingRef.current) return
    stoppingRef.current = true
    setStopping(true)
    setError(null)
    try {
      await cancelDirectorPlanOperation(operation.id)
    } catch (e) {
      stoppingRef.current = false
      setStopping(false)
      setError(String(e instanceof Error ? e.message : e))
    }
  }, [operation])

  if (!operation) return null

  const total = operation.total
  const current = Math.min(operation.current, total || operation.current)
  const percent = total > 0 ? Math.min(100, (current / total) * 100) : 0
  const running = !operation.cancelling

  return (
    <div
      role="status"
      aria-live="polite"
      className="rounded-xl border border-accent-blue/30 bg-bg-tertiary px-4 py-3"
    >
      <div className="flex items-center gap-2">
        <Loader2
          size={14}
          className={`shrink-0 ${running ? 'animate-spin text-accent-blue' : 'text-text-muted'}`}
        />
        <p className="flex-1 min-w-0 truncate text-sm font-medium text-text-secondary">
          {describeOperation(operation)}
        </p>
        {running ? (
          <button
            type="button"
            onClick={stopPass}
            disabled={stopping}
            className="flex shrink-0 items-center gap-1 text-xs text-red-400 transition-colors hover:text-red-300 disabled:opacity-50"
            title="Stop after the batch being planned right now"
          >
            <Square size={11} />
            Stop
          </button>
        ) : (
          <span className="shrink-0 text-[11px] text-text-muted">Stopping…</span>
        )}
      </div>

      <p className="mt-1 truncate text-xs text-text-muted">
        {operation.message || 'Working…'}
      </p>

      {total > 0 && (
        <div className="mt-2 flex items-center gap-2">
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-bg-active">
            <div
              className="h-full rounded-full bg-accent-green transition-all duration-300"
              style={{ width: `${percent}%` }}
            />
          </div>
          <span className="shrink-0 text-[10px] tabular-nums text-text-muted">
            {current}/{total}
          </span>
        </div>
      )}

      <div className="mt-1 flex items-center gap-2 text-[10px] text-text-muted">
        <span>{formatGenerationDuration(operation.elapsed_seconds)} elapsed</span>
        {operation.cancelling && <span>· the batch in progress finishes first</span>}
      </div>

      {error && (
        <p className="mt-1 text-[11px] break-words text-red-400">{error}</p>
      )}
    </div>
  )
}
