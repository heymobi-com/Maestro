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

import { parseDirectorScript, rescaleScriptTimeline, type ScriptTranscriptRow } from '../lib/directorScript'
import type { LyricSegment, PlannedClip } from '../types'

export type DirectorScriptSource = 'audio' | 'script'

export interface DirectorScriptState {
  /** Where a project's words come from: recorded audio, or a written script. */
  directorScriptSource: DirectorScriptSource | null
  directorScriptText: string
  directorScriptClips: PlannedClip[]
  directorScriptTranscript: ScriptTranscriptRow[]
  /**
   * An episode spoken from these rows by a TTS engine. When set, the render no
   * longer asks the video model to invent the voices: the track owns them and H3
   * only moves the mouths and muxes the pristine audio.
   */
  directorScriptAudioPath: string | null
  /** The measured length of that track, used to re-time the authored rows. */
  directorScriptAudioDuration: number | null
}

/** One definition for the initial state and for the project reset. */
export const directorScriptDefaults: DirectorScriptState = {
  directorScriptSource: null,
  directorScriptText: '',
  directorScriptClips: [],
  directorScriptTranscript: [],
  directorScriptAudioPath: null,
  directorScriptAudioDuration: null,
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
function deriveScriptRows(text: string, audioDuration: number | null) {
  const parsed = parseDirectorScript(text)
  const timed = audioDuration ? rescaleScriptTimeline(parsed, audioDuration) : parsed
  return {
    directorScriptClips: timed.clips as unknown as PlannedClip[],
    directorScriptTranscript: timed.transcript,
  }
}

export function directorScriptTextPatch(
  state: DirectorScriptState,
  text: string,
): Partial<DirectorScriptState> {
  return {
    directorScriptText: text,
    ...deriveScriptRows(text, state.directorScriptAudioDuration),
  }
}

/**
 * Attach (or clear) the spoken episode for these rows. The rows are re-timed onto
 * the measured duration here, so the review steps show the clips the render will
 * actually slice out of the track.
 */
export function directorScriptAudioPatch(
  state: DirectorScriptState,
  path: string | null,
  duration: number | null,
): Partial<DirectorScriptState> {
  return {
    directorScriptAudioPath: path,
    directorScriptAudioDuration: duration,
    ...deriveScriptRows(state.directorScriptText, path ? duration : null),
  }
}

/** True when the authored rows are what this project plans and renders. */
export function directorScriptIsActive(state: DirectorScriptState): boolean {
  return state.directorScriptSource === 'script' && state.directorScriptClips.length > 0
}

/**
 * A script made only of scene direction still produces clips, but it has no
 * words for the video model to speak. Planning it used to succeed and render
 * silent shots whose voice references made H3 vocalize gibberish, so this state
 * is refused with a message that names the missing format.
 */
export function directorScriptMissingSpokenRows(state: DirectorScriptState): boolean {
  return state.directorScriptSource === 'script'
    && state.directorScriptTranscript.length === 0
}

export const DIRECTOR_SCRIPT_MISSING_SPOKEN_ROWS_ERROR =
  'The written script has no dialogue rows. Write at least one line as NAME: line '
  + '(S1: line also works) before planning or rendering.'

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
  _director_script_source?: boolean
} {
  if (!directorScriptIsActive(state)) {
    return { audio_path: soundtrack, planned_clips: analysedClips, lyrics: analysedLyrics || '' }
  }
  // A written script that has no audio yet asks H3 to invent the voices for its
  // lines, so the render must not ask for an uploaded track. Once a TTS episode
  // exists for those rows the track owns the voice instead: it travels as the
  // project's audio, H3 only lip-syncs and muxes it, and the rows keep their
  // (re-timed) timings so each shot still knows how long it is.
  return {
    audio_path: state.directorScriptAudioPath || undefined,
    planned_clips: state.directorScriptClips,
    lyrics: state.directorScriptTranscript,
    _director_script_source: true,
  }
}
