/**
 * Regenerate, the way the file belongs to.
 *
 * A Director clip belongs to a pipeline, so regenerating it through the Studio
 * reroll did nothing useful: that path loaded the Director project and then fired
 * a Studio generation with whatever params the sidebar happened to hold. The clip
 * is regenerated through its own pipeline instead, and replaced in its own
 * position.
 *
 * The decision and its progress state are ours, so they live here rather than in
 * the gallery card upstream edits whenever it touches the media feed. The card
 * keeps one hook call and one `<RerollStatus>` line.
 */

import { useCallback, useState } from 'react'
import type { OutputMetadata } from '../types'

export interface DirectorAwareRerollOptions {
  meta: OutputMetadata | null
  index: number
  setSelectedOutput: (index: number) => void
  rerunClipVideo: (pid: string, clipIndex: number, prompt?: string) => Promise<unknown>
  rerollGeneration: () => Promise<void>
}

export interface DirectorAwareReroll {
  rerolling: boolean
  rerollError: string | null
  handleReroll: () => Promise<void>
}

export function useDirectorAwareReroll({
  meta,
  index,
  setSelectedOutput,
  rerunClipVideo,
  rerollGeneration,
}: DirectorAwareRerollOptions): DirectorAwareReroll {
  const [rerolling, setRerolling] = useState(false)
  const [rerollError, setRerollError] = useState<string | null>(null)

  const handleReroll = useCallback(async () => {
    setSelectedOutput(index)
    setRerollError(null)
    const directorPid = meta?.director_pipeline_id
    const directorClipIndex = meta?.director_clip_index
    setRerolling(true)
    try {
      if (directorPid && typeof directorClipIndex === 'number') {
        await rerunClipVideo(directorPid, directorClipIndex)
      } else {
        await rerollGeneration()
      }
    } catch (e) {
      // The card carries the reason: the action menu closes on click, so a
      // failure with nowhere to appear looks like the action did nothing.
      setRerollError(e instanceof Error ? e.message : String(e))
    } finally {
      setRerolling(false)
    }
  }, [index, meta, setSelectedOutput, rerunClipVideo, rerollGeneration])

  return { rerolling, rerollError, handleReroll }
}
