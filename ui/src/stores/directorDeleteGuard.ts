/**
 * Deleting a take, and the one case the server refuses.
 *
 * The server refuses to remove the file a Director shot is using and names the
 * shot. That question belongs to the app: a native `confirm()` can answer itself,
 * because a service-worker PWA and several webviews dismiss it as true, so
 * pressing cancel still deleted the take with force and left the shot pointing at
 * a file that no longer existed. The file is therefore left alone until the user
 * answers in the app's own dialog.
 *
 * Ours, so it lives here rather than in the store upstream edits every release.
 * The store spreads these actions and calls `isShotInUseRefusal` from its own
 * delete, which keeps its side of the seam to a few lines.
 */

import * as api from '../api/client'
import { outputIdentity } from '../lib/galleryIdentity'
import type { OutputFile, OutputMetadata } from '../types'

export interface BlockedDeleteState {
  /** The take a Director shot is using, waiting for the user's own answer. */
  blockedDelete: { output: OutputFile; message: string } | null
}

export const directorDeleteDefaults: BlockedDeleteState = { blockedDelete: null }

/** The slice of the store this guard reads and writes. */
export interface BlockedDeleteHost extends BlockedDeleteState {
  outputs: OutputFile[]
  outputsTotal: number
  selectedOutput: number
  selectedOutputMeta: OutputMetadata | null
  filteredOutputs: () => OutputFile[]
  loadOutputMetadata: (name: string, workspace?: string) => void
  _forgetDeletedOutput: (output: OutputFile) => void
}

export type BlockedDeletePatch = Partial<Pick<
  BlockedDeleteHost,
  'outputs' | 'outputsTotal' | 'selectedOutput' | 'blockedDelete' | 'selectedOutputMeta'
>>

/** The server's refusal, which is the only text that names the holding shot. */
export function isShotInUseRefusal(message: string): boolean {
  return /is using for shot/.test(message)
}

/** Drop the item from the gallery, only once the server confirmed it is gone. */
export function forgetDeletedOutput<T extends BlockedDeleteHost>(
  set: (patch: BlockedDeletePatch) => void,
  get: () => T,
  output: OutputFile,
): void {
  const allOutputs = get().outputs.filter(o => outputIdentity(o) !== outputIdentity(output))
  const newIdx = Math.min(get().selectedOutput, Math.max(0, allOutputs.length - 1))
  set({
    outputs: allOutputs,
    outputsTotal: Math.max(0, get().outputsTotal - 1),
    selectedOutput: newIdx,
  })
  // Load metadata for the new selection.
  const newFiltered = get().filteredOutputs()
  if (newFiltered[newIdx]) {
    get().loadOutputMetadata(newFiltered[newIdx].name, newFiltered[newIdx].workspace)
  } else {
    set({ selectedOutputMeta: null })
  }
}

/** The three actions the store spreads in: the question and its two answers. */
export function directorDeleteGuardActions<T extends BlockedDeleteHost>(
  set: (patch: BlockedDeletePatch) => void,
  get: () => T,
) {
  return {
    cancelBlockedDelete: () => {
      // Cancelling only ever means "keep the clip": nothing on this path touches
      // the file or the gallery, so closing the question cannot delete anything.
      set({ blockedDelete: null })
    },

    confirmBlockedDelete: async () => {
      const blocked = get().blockedDelete
      if (!blocked) return
      // Close the question first so a second click cannot queue a second delete.
      set({ blockedDelete: null })
      try {
        await api.deleteOutput(blocked.output.name, blocked.output.workspace, true)
        forgetDeletedOutput(set, get, blocked.output)
      } catch (e) {
        console.error('Failed to delete output:', e)
      }
    },

    _forgetDeletedOutput: (output: OutputFile) => forgetDeletedOutput(set, get, output),
  }
}
