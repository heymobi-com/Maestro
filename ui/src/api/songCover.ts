/**
 * Reading a recorded song so it can be covered.
 *
 * The endpoint is ours (`app/services/song_cover.py`): the voice is separated
 * from the mix, transcribed with the Whisper medium already on disk, and the
 * lines come back labelled with the song's sections — ready for the Lyrics
 * field. Nothing here downloads a model, and nothing is generated.
 */

const BASE = '' // same origin in production; Vite proxies /api in dev

export interface CoverSection {
  start: number
  end: number
  label: string
  energy?: number
}

export interface CoverLyrics {
  lyrics: string
  language: string
  duration: number | null
  bpm: number | null
  sections: CoverSection[]
  segments: Array<{ start: number; end: number; text: string }>
  vocals_path: string | null
  source_path: string
}

export async function readCoverLyrics(params: {
  audio_path: string
  language?: string
}): Promise<CoverLyrics> {
  const res = await fetch(`${BASE}/api/v1/music/cover-lyrics`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    // Read the body as text first. The server's `detail` is the useful part
    // when it is there, but it is not always a string: a validation error
    // carries a list, and an unrouted request is answered by the static mount
    // with plain text. Both used to collapse into one useless sentence.
    const raw = await res.text().catch(() => '')
    let detail: unknown
    try {
      detail = (JSON.parse(raw) as { detail?: unknown } | null)?.detail
    } catch {
      detail = undefined
    }
    const message = typeof detail === 'string'
      ? detail
      : Array.isArray(detail)
        ? detail.map(entry => (entry as { msg?: string })?.msg).filter(Boolean).join('; ')
        : res.status === 404
          // The endpoint itself is missing, which means the app is still
          // running the backend it started with.
          ? 'The Maestro backend does not have this endpoint yet — restart the app so it loads it.'
          : ''
    throw new Error(message || `Reading the recording failed (HTTP ${res.status})`)
  }
  return res.json()
}
