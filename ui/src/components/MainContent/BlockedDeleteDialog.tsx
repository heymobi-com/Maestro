import { useCallback, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle } from 'lucide-react'
import { useStore } from '../../stores/useStore'

/**
 * The app's own question before deleting the take a Director shot is using.
 *
 * The server refuses the delete and names the shot. This used to be answered by
 * a native `window.confirm`, which can answer itself: a service-worker PWA and
 * several webviews dismiss the dialog and return true, so pressing Cancel still
 * deleted the take with force — leaving the shot pointing at a file that no
 * longer existed, which is exactly what the guard exists to prevent. Owning the
 * dialog means closing it can only ever mean "keep the clip": the safe answer
 * holds focus, Escape and the backdrop both keep, and deleting needs a
 * deliberate click on the danger button.
 */
export function BlockedDeleteDialog() {
  const blocked = useStore(s => s.blockedDelete)
  const confirmBlockedDelete = useStore(s => s.confirmBlockedDelete)
  const cancelBlockedDelete = useStore(s => s.cancelBlockedDelete)
  const keepRef = useRef<HTMLButtonElement>(null)

  const keep = useCallback(() => cancelBlockedDelete(), [cancelBlockedDelete])

  useEffect(() => {
    if (!blocked) return
    keepRef.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.stopPropagation()
      keep()
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [blocked, keep])

  if (!blocked) return null

  return createPortal(
    <div
      className="fixed inset-0 z-[10000] flex items-center justify-center bg-black/75 p-4"
      onClick={event => { if (event.target === event.currentTarget) keep() }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="blocked-delete-title"
        className="w-full max-w-lg rounded-2xl border border-border bg-bg-primary p-4 shadow-2xl"
      >
        <div className="flex items-start gap-3">
          <AlertTriangle size={20} className="mt-0.5 shrink-0 text-indicator-warning" />
          <div className="min-w-0 flex-1">
            <h2 id="blocked-delete-title" className="text-sm font-semibold text-text-primary">
              This clip belongs to a Director film
            </h2>
            <p className="mt-2 text-xs leading-relaxed break-words text-text-secondary">
              {blocked.message}
            </p>
            <p className="mt-2 text-[11px] break-words text-text-muted">{blocked.output.name}</p>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <button
            ref={keepRef}
            type="button"
            onClick={keep}
            className="rounded-lg border border-border px-3 py-2 text-xs text-text-primary transition-colors hover:bg-bg-tertiary"
          >
            Keep the clip
          </button>
          <button
            type="button"
            onClick={() => void confirmBlockedDelete()}
            className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-400 transition-colors hover:bg-red-500/20"
          >
            Delete it anyway
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
