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

const _SPEAKER_ROW = /^\s*([\p{Lu}][\p{L}'’.\- ]{0,29}?)\s*(?:\(([^)\n]{0,40})\))?\s*:\s*(.+)$/u
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
 * Read a script written as speaker rows.
 *
 * Lines that are not speaker rows are scene direction: they are kept in `turns`
 * order as a turn of their own with no speaker so the planner still sees the
 * staging, but they contribute no spoken words.
 */
export function parseDirectorScript(script: string): ParsedScript {
  const turns: ScriptTurn[] = []
  for (const line of String(script || '').split(/\r?\n/)) {
    const trimmed = line.trim()
    if (!trimmed) continue
    const match = trimmed.match(_SPEAKER_ROW)
    if (match) {
      const text = _cleanText(match[3])
      if (text) turns.push({ speaker: _cleanText(match[1]), delivery: _cleanText(match[2] || ''), text })
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
      transcript.push({ start, end, speaker: turn.speaker, text: turn.text })
      wordCount += _countWords(turn.text)
    }
    cursor = end + PAUSE_SECONDS
  }
  return { turns, clips, transcript, wordCount, duration: clips.length > 0 ? clips[clips.length - 1].end : 0 }
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
