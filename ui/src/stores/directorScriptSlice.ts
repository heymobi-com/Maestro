/**
 * A written script as the source of a project's words.
 *
 * Choosing the script source hides the audio step, so the clips, the transcript
 * and the language tags all come from the authored rows instead of from a
 * diarized analysis. The timeline and the transcript are derived when the text
 * changes, not at send time, so the review steps show the clips the render will
 * actually use.
 *
 * The state and the patches are ours, so they live here rather than in the store
 * upstream edits every release. The store spreads the defaults and calls the
 * patches, which keeps its side of the seam to one line each.
 */

import { parseDirectorScript, type ScriptTranscriptRow } from '../lib/directorScript'
import type { LyricSegment, PlannedClip } from '../types'

export type DirectorScriptSource = 'audio' | 'script'

export interface DirectorScriptState {
  /** Where a project's words come from: recorded audio, or a written script. */
  directorScriptSource: DirectorScriptSource | null
  directorScriptText: string
  directorScriptClips: PlannedClip[]
  directorScriptTranscript: ScriptTranscriptRow[]
}

/** One definition for the initial state and for the project reset. */
export const directorScriptDefaults: DirectorScriptState = {
  directorScriptSource: null,
  directorScriptText: '',
  directorScriptClips: [],
  directorScriptTranscript: [],
}

/**
 * A written script needs no audio pass, so choosing it lands on the step that
 * already collects the scene description. The story path does the same.
 */
export function directorScriptSourcePatch(
  state: Pick<DirectorScriptState, 'directorScriptSource'> & { directorStep?: string },
  source: DirectorScriptSource,
): Partial<DirectorScriptState> & { directorStep?: 'style' } {
  return {
    directorScriptSource: source,
    ...(source === 'script' && state.directorStep === 'upload' ? { directorStep: 'style' as const } : {}),
  }
}

/** The timeline and the transcript, derived as soon as the text changes. */
export function directorScriptTextPatch(text: string): Partial<DirectorScriptState> {
  const parsed = parseDirectorScript(text)
  return {
    directorScriptText: text,
    directorScriptClips: parsed.clips as unknown as PlannedClip[],
    directorScriptTranscript: parsed.transcript,
  }
}

/** True when the authored rows are what this project plans and renders. */
export function directorScriptIsActive(state: DirectorScriptState): boolean {
  return state.directorScriptSource === 'script' && state.directorScriptClips.length > 0
}

/** The timeline the planner reads: the authored clips, or the analysed ones. */
export function directorScriptTimeline<T>(state: DirectorScriptState, analysed: T): PlannedClip[] | T {
  return directorScriptIsActive(state) ? state.directorScriptClips : analysed
}

/**
 * The planning request fields a script adds: its lines replace the analysed
 * lyrics, and the authored rows travel as the source document so the H3 ledger
 * locks what was written instead of accepting a paraphrase.
 */
export function directorScriptPlanFields(
  state: DirectorScriptState,
  analysedLyrics: LyricSegment[] | null | undefined,
): { lyrics: LyricSegment[] | ScriptTranscriptRow[] | undefined; story_description?: string } {
  if (!directorScriptIsActive(state)) return { lyrics: analysedLyrics ?? undefined }
  return {
    lyrics: state.directorScriptTranscript,
    story_description: state.directorScriptText,
  }
}

/** The generation parameters a script changes, spread into the pipeline request. */
export function directorScriptPipelineFields(
  state: DirectorScriptState,
  soundtrack: string | null | undefined,
  analysedClips: PlannedClip[],
  analysedLyrics: LyricSegment[] | null | undefined,
): {
  audio_path: string | null | undefined
  planned_clips: PlannedClip[]
  lyrics: LyricSegment[] | ScriptTranscriptRow[] | '' | undefined
} {
  if (!directorScriptIsActive(state)) {
    return { audio_path: soundtrack, planned_clips: analysedClips, lyrics: analysedLyrics || '' }
  }
  // A written script has no soundtrack: H3 generates the voices for its lines.
  return {
    audio_path: undefined,
    planned_clips: state.directorScriptClips,
    lyrics: state.directorScriptTranscript,
  }
}
