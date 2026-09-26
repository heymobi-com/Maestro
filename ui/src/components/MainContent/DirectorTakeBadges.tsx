/**
 * What a Director take adds to a gallery card.
 *
 * Both are ours, so they live here rather than inline in the media feed that
 * upstream edits whenever it touches the gallery: the card keeps a one-line
 * element where the markup used to be.
 */

import { Loader2 } from 'lucide-react'
import type { OutputMetadata } from '../../types'

/** The shot this take belongs to, and whether it is the newer one for it. */
export function DirectorTakeBadges({ meta }: { meta: OutputMetadata | null }) {
  const shot = typeof meta?.director_clip_index === 'number' ? meta.director_clip_index + 1 : null
  if (shot === null && !meta?.director_supersedes) return null
  return (
    <>
      {shot !== null && (
        <span className="text-text-muted"> &middot; shot {shot}</span>
      )}
      {/* A regenerated take sits directly above the one it replaces, so say which
          is which: both carry the same shot number. */}
      {meta?.director_supersedes && (
        <span className="text-accent-blue" title={`Replaces ${meta.director_supersedes}`}>
          {' '}&middot; new take
        </span>
      )}
    </>
  )
}

/** Progress and failures for a regeneration started from the card. */
export function RerollStatus({ rerolling, error }: { rerolling: boolean; error: string | null }) {
  if (!rerolling && !error) return null
  return (
    <>
      {rerolling && (
        <span className="flex shrink-0 items-center gap-1 rounded bg-accent-blue/15 px-1.5 py-0.5 text-[10px] text-accent-blue">
          <Loader2 size={10} className="animate-spin" />
          Regenerating
        </span>
      )}
      {error && (
        <span
          className="max-w-[220px] shrink-0 truncate rounded bg-red-500/15 px-1.5 py-0.5 text-[10px] text-chip-red"
          title={error}
        >
          {error}
        </span>
      )}
    </>
  )
}
