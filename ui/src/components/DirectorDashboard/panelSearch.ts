import { createContext, useContext } from 'react'

/**
 * What a floating window's search box knows, for the content inside it.
 *
 * A textarea cannot style its own text, so painting a match needs a layer behind
 * it. The window owns that layer's geometry and the content owns the text, so the
 * window publishes the offsets it found here and the content marks them. Kept in
 * its own module because a file that exports a component should not also export
 * the hook React Fast Refresh needs to reload it independently.
 */

/** One match inside one of a window's textareas, as plain offsets. */
export type PanelSearchMatch = { start: number; end: number }

export type PanelSearch = {
  query: string
  caseSensitive: boolean
  /** The hits inside one particular field, in reading order. */
  matchesFor: (field: HTMLTextAreaElement | null) => PanelSearchMatch[]
  /** Position of the shown hit within that field's own list, or -1. */
  activeFor: (field: HTMLTextAreaElement | null) => number
  total: number
  /** 1-based position of the hit the window is standing on, 0 when there is none. */
  position: number
  next: () => void
  previous: () => void
}

export const PanelSearchContext = createContext<PanelSearch | null>(null)

/** The search state of the window this component sits in, when it has one. */
export function usePanelSearch(): PanelSearch | null {
  return useContext(PanelSearchContext)
}
