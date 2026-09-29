/**
 * Lyrics are optional in Music mode.
 *
 * The panel collects three texts but only two of them travel: the Music Caption
 * (alt_prompt) and the Lyrics (prompt). "Describe your song" is a UI-only input
 * kept for the sidecar, so a user who described the song and pressed Generate
 * submitted an empty prompt -- and the backend refuses a music request whose
 * prompt is empty with a bare "prompt is required", before any job exists.
 * Measured in one session: four Generate clicks (YuE2, then MiniMax-Music3)
 * answered 400 and no song was ever queued.
 *
 * The description is a real input here: when the lyrics are missing the app
 * writes them with the same call the Write Song button makes, and leaves them in
 * the fields, so the user reads (and can edit) the words the song will sing.
 * Lyrics stay optional but are never invented silently: an Instrumental request
 * carries the models' own sentinel, and a request with neither lyrics nor a
 * description is refused with a reason instead of an opaque 400.
 *
 * The decision, the call and the message are ours, so they live here rather than
 * in the store upstream edits every release. The store's side of the seam is one
 * call plus its import.
 */

import * as api from '../api/client'
import { announceMaestroEvent } from '../lib/notifications'
import type { GenerateParams } from '../types'

/** The marker MiniMax-Music3's own validator names for an instrumental song. */
export const INSTRUMENTAL_LYRICS = '[Instrumental]'

/** Only what this decision needs, so the store can pass `get` unchanged. */
export interface MusicSongInputs {
  params: GenerateParams
  generationMode: string
  audioSubMode: string
  musicDescription: string
  musicInstrumental: boolean
  durationSeconds: number
}

type ReadMusicSongState = () => MusicSongInputs
type WriteMusicSongParams = (
  patch: (state: { params: GenerateParams }) => { params: GenerateParams },
) => void

/**
 * A toast is the only surface Music mode has: the prompt dock that renders
 * promptEnhanceError is not mounted while the Music panel is open, so an error
 * written there would never be seen.
 */
function announceFailure(title: string, body: string): void {
  announceMaestroEvent({
    key: `music-song-${Date.now().toString(36)}`,
    category: 'failure',
    title,
    body,
    system: false,
    sound: false,
  })
}

function needsLyrics(state: MusicSongInputs): boolean {
  if (state.generationMode !== 'audio' || state.audioSubMode !== 'music') return false
  return !String(state.params.prompt || '').trim()
}

/**
 * Write the lyrics over the *current* params (not the ones read before the
 * request went out), and only fill a Caption the user left empty.
 */
function applySong(lyrics: string, style: string) {
  return ({ params }: { params: GenerateParams }): { params: GenerateParams } => ({
    params: {
      ...params,
      prompt: lyrics,
      ...(String(params.alt_prompt || '').trim() || !style ? {} : { alt_prompt: style }),
    },
  })
}

/**
 * Make sure a Music request carries the lyrics the backend demands.
 *
 * Returns false when it cannot, after telling the user why: the caller stops
 * instead of submitting a request that would be refused anyway.
 */
export async function ensureMusicSong(
  read: ReadMusicSongState,
  write: WriteMusicSongParams,
): Promise<boolean> {
  const state = read()
  if (!needsLyrics(state)) return true

  const instrumental = state.musicInstrumental === true
  const caption = String(state.params.alt_prompt || '').trim()
  const description = String(state.musicDescription || '').trim()

  if (!description) {
    // An instrumental song needs no words, but it does need the Caption the
    // models sing around, so a caption-only request is already complete.
    if (instrumental && caption) {
      write(applySong(INSTRUMENTAL_LYRICS, ''))
      return true
    }
    announceFailure(
      'Music needs a song',
      'Write the lyrics, tick Instrumental, or describe the song: the app writes the words from your description before generating.',
    )
    return false
  }

  let written: { style: string; lyrics: string }
  try {
    written = await api.writeSong({
      description,
      instrumental,
      duration_seconds: state.durationSeconds,
      model_type: String(state.params.model_type || ''),
    })
  } catch (error) {
    announceFailure(
      'Song writing failed',
      error instanceof Error ? error.message : 'Song writing failed',
    )
    return false
  }

  const lyrics = instrumental ? INSTRUMENTAL_LYRICS : String(written.lyrics || '').trim()
  if (!lyrics) {
    announceFailure(
      'Song writing returned no lyrics',
      'Write the lyrics by hand, or describe the song again.',
    )
    return false
  }
  write(applySong(lyrics, String(written.style || '').trim()))
  return true
}
