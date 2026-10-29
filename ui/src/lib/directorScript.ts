/**
 * Parse a written script into the clip timeline and transcript Director plans from.
 *
 * A Director project normally starts from audio: the analysis produces the clips and
 * the transcript. When the user writes the script instead, there is nothing to
 * analyse, so this module produces the same two things deterministically:
 *
 *   - `clips` — one per spoken turn, timed at the shared 2.8 words/second speech
 *     pace the audio tools also use, so a written project and an audio project of
 *     the same length plan the same way.
 *   - `transcript` — `{ start, end, speaker, text }` rows, the shape the podcast
 *     planner already reads when a project comes from audio.
 *
 * Recognised script forms (the same ones the H3 story ledger locks as dialogue):
 *
 *   VALERIA: La dignidad humana no se negocia.
 *   RICARDO (pensativo): ¿Y qué propones para defenderla hoy?
 *   VALERIA: <d>[Spanish] Y no pienso firmar otra cosa.</d>
 *
 * The language tag is kept exactly as written. Nothing here invents one: a
 * screenplay row with no tag is left for the compiler's own detection, because
 * labelling Spanish text as English is what makes the model read it with English
 * phonetics.
 */

export interface ScriptTurn {
  speaker: string
  delivery: string
  text: string
}

export interface ScriptClip {
  start: number
  end: number
  duration_frames: number
  section_label: string
  energy: number
  suggested_prompt_hint: string
  beat_count: number
}

export interface ScriptTranscriptRow {
  start: number
  end: number
  speaker: string
  text: string
  /** The tone the row declares, as `NAME (tone): line`. Empty when it declares none. */
  delivery: string
}

export interface ParsedScript {
  turns: ScriptTurn[]
  clips: ScriptClip[]
  transcript: ScriptTranscriptRow[]
  /** Spoken words only, for the duration estimate. */
  wordCount: number
  duration: number
}

const WORDS_PER_SECOND = 2.8
const MIN_TURN_SECONDS = 1.6
const PAUSE_SECONDS = 0.4
/** Keep a clip inside one native H3 window; a long turn is split at its sentences. */
const MAX_CLIP_SECONDS = 12

// A row may open with the scene's timecode; the dialogue itself starts after it.
// Both shapes a written podcast script uses are accepted: ``0:00-0:05 S1: line``
// and ``[00:05] S1: line``.
const _TIMECODE_PREFIX = /^\s*\[?\d{1,2}:\d{2}(?::\d{2})?(?:\s*[-–—]\s*\[?\d{1,2}:\d{2}(?::\d{2})?)?\]?\s*(?:[|·•]\s*)?[-–—:]?\s*/u
// A line that is only a timecode marks the scene, not a spoken turn. Keeping it
// used to add a silent 1.6 s clip per timestamp, which then merged the real turns
// into one shot.
const _TIMECODE_ONLY = /^\s*\[?\d{1,2}:\d{2}(?::\d{2})?(?:\s*[-–—]\s*\[?\d{1,2}:\d{2}(?::\d{2})?)?\]?\s*$/u
// ``S1:`` and ``SPEAKER 1:`` are how a two-host brief names its cast. Requiring
// letters only turned every one of those rows into scene direction, so the
// planner planned a dialogue-free video and H3 vocalized gibberish.
const _SPEAKER_ROW = /^\s*([\p{Lu}][\p{L}\p{N}'’.\- ]{0,29}?)\s*(?:\(([^)\n]{0,40})\))?\s*:\s*(.+)$/u
// A cast label may use a dash or a column separator instead of a colon. The
// label is restricted so "JEAN-LUC" is still one name, not "JEAN" plus a line.
const _SPEAKER_ROW_DASH = /^\s*(S\d{1,2}|(?:SPEAKER|HOST|NARRATOR|VOICE)\s*\d{1,2})\s*(?:\(([^)\n]{0,40})\))?\s*[-–—|]\s*(.+)$/u
const _SPEAKER_LABEL_LIKE = /^(?:S\d{1,2}|(?:SPEAKER|HOST|NARRATOR|VOICE)\s*\d{1,2}|[\p{Lu}][\p{Lu}\p{N}'’.\- ]{0,29})$/u
// Uppercase stage directions end in a colon too. They are not speakers.
const _SCENE_HEADINGS = new Set([
  'SETUP', 'SCENE', 'SHOT', 'CAMERA', 'ACTION', 'LIGHTING', 'SOUND',
  'MUSIC', 'SFX', 'B-ROLL', 'NOTE', 'NOTA', 'TITLE', 'INT', 'EXT', 'CUT',
  'TRANSITION', 'LOCATION', 'CONTINUITY', 'STYLE', 'VISUAL', 'AUDIO',
  'DIALOGUE', 'SUBTITLES',
])
const _TAG = /\[([^[\]]{1,40})\]/

export function _countWords(text: string): number {
  return (text.match(/[\p{L}\p{N}]+(?:['’][\p{L}]+)?/gu) || []).length
}

function _cleanText(raw: string): string {
  return raw.replace(/\s+/g, ' ').trim()
}

function _turnSeconds(turn: ScriptTurn): number {
  return Math.max(MIN_TURN_SECONDS, _countWords(turn.text) / WORDS_PER_SECOND)
}

/** Split one over-long turn at sentence ends so it fits a single H3 window. */
function _splitTurn(turn: ScriptTurn): ScriptTurn[] {
  const total = _turnSeconds(turn)
  if (total <= MAX_CLIP_SECONDS) return [turn]
  const sentences = turn.text.match(/[^.!?¿¡]+[.!?]*\s*/gu) || [turn.text]
  const parts: ScriptTurn[] = []
  let buffer = ''
  for (const sentence of sentences) {
    const candidate = (buffer + sentence).trim()
    if (buffer && _countWords(candidate) / WORDS_PER_SECOND > MAX_CLIP_SECONDS) {
      parts.push({ ...turn, text: buffer.trim() })
      buffer = sentence
    } else {
      buffer = candidate
    }
  }
  if (buffer.trim()) parts.push({ ...turn, text: buffer.trim() })
  return parts.length > 0 ? parts : [turn]
}

/**
 * Match one speaker row, tolerating a leading scene timecode.
 *
 * A timecode makes a title-case label ambiguous ("Cold open:" is staging, not a
 * host), so after a timecode only a cast label or an explicit `<d>` line counts.
 */
function _speakerRow(line: string): RegExpMatchArray | null {
  const direct = line.match(_SPEAKER_ROW) || line.match(_SPEAKER_ROW_DASH)
  if (direct) return direct
  const withoutTimecode = line.replace(_TIMECODE_PREFIX, '').trim()
  if (!withoutTimecode || withoutTimecode === line) return null
  const match = withoutTimecode.match(_SPEAKER_ROW) || withoutTimecode.match(_SPEAKER_ROW_DASH)
  if (!match) return null
  return _SPEAKER_LABEL_LIKE.test(match[1].trim()) || /<d>/i.test(match[3])
    ? match
    : null
}

/**
 * Read a script written as speaker rows.
 *
 * Lines that are not speaker rows are scene direction: they are kept in `turns`
 * order as a turn of their own with no speaker so the planner still sees the
 * staging, but they contribute no spoken words. A line that is only a timecode
 * is a scene marker and contributes nothing.
 */
export function parseDirectorScript(script: string): ParsedScript {
  const turns: ScriptTurn[] = []
  for (const line of String(script || '').split(/\r?\n/)) {
    const trimmed = line.trim()
    if (!trimmed) continue
    if (_TIMECODE_ONLY.test(trimmed)) continue
    const match = _speakerRow(trimmed)
    if (match) {
      const speaker = _cleanText(match[1])
      if (_SCENE_HEADINGS.has(speaker.toUpperCase())) {
        turns.push({ speaker: '', delivery: '', text: trimmed })
        continue
      }
      const text = _cleanText(match[3])
      if (text) turns.push({ speaker, delivery: _cleanText(match[2] || ''), text })
      continue
    }
    // Scene direction: keep it so the shot has staging, but it is not spoken.
    turns.push({ speaker: '', delivery: '', text: trimmed })
  }

  const expanded = turns.flatMap(turn => (turn.speaker ? _splitTurn(turn) : [turn]))
  const clips: ScriptClip[] = []
  const transcript: ScriptTranscriptRow[] = []
  let cursor = 0
  let wordCount = 0
  for (const turn of expanded) {
    const seconds = turn.speaker ? _turnSeconds(turn) : MIN_TURN_SECONDS
    const start = cursor
    const end = start + seconds
    clips.push({
      start,
      end,
      duration_frames: Math.round(seconds * 24),
      section_label: turn.speaker || 'scene',
      energy: 0.5,
      suggested_prompt_hint: turn.text.slice(0, 160),
      beat_count: 0,
    })
    if (turn.speaker) {
      transcript.push({ start, end, speaker: turn.speaker, text: turn.text, delivery: turn.delivery })
      wordCount += _countWords(turn.text)
    }
    cursor = end + PAUSE_SECONDS
  }
  return { turns, clips, transcript, wordCount, duration: clips.length > 0 ? clips[clips.length - 1].end : 0 }
}

/**
 * Re-time a parsed script onto a real audio duration.
 *
 * A timeline estimated from words per second and the episode audio recorded from
 * those same rows never match exactly. H3 slices each shot's window out of the
 * supplied track, so a timeline longer than the track asks for samples that do
 * not exist, and a shorter one leaves the tail unheard. Scaling every turn onto
 * the measured duration keeps the order, the pauses and each turn's share of the
 * episode, which is all a lip-synced shot needs: the words it must perform come
 * from the track, not from the estimate.
 */
export function rescaleScriptTimeline(parsed: ParsedScript, targetSeconds: number): ParsedScript {
  const current = parsed.duration
  if (!(targetSeconds > 0) || !(current > 0)) return parsed
  const factor = targetSeconds / current
  if (Math.abs(1 - factor) < 0.01) return parsed
  const scale = (seconds: number) => Math.round(seconds * factor * 1000) / 1000
  return {
    ...parsed,
    clips: parsed.clips.map(clip => ({
      ...clip,
      start: scale(clip.start),
      end: scale(clip.end),
      duration_frames: Math.max(1, Math.round((clip.end - clip.start) * factor * 24)),
    })),
    transcript: parsed.transcript.map(row => ({
      ...row,
      start: scale(row.start),
      end: scale(row.end),
    })),
    duration: targetSeconds,
  }
}

/** The language a turn declares, if it declares one. */
export function scriptTurnLanguage(text: string): string {
  const tagged = text.match(/<d>\s*\[([^\]]{1,40})\]/i)
  if (tagged) return tagged[1].trim()
  const inline = text.match(_TAG)
  return inline ? inline[1].trim() : ''
}

/** How the model should receive a written turn: the tagged words stay as authored. */
export function scriptUtterance(turn: ScriptTurn): string {
  const text = turn.text.trim()
  if (/<d>/i.test(text)) return text
  const language = scriptTurnLanguage(text)
  const words = language ? text.replace(/^\s*\[[^\]]{1,40}\]\s*/, '') : text
  return language ? `<d>[${language}] ${words}</d>` : `<d>${words}</d>`
}
