/**
 * The writing brief Maestro adds when a podcast dialogue is requested.
 *
 * The speech writer already writes two-person dialogue and long scripts. This
 * block gives it the podcast shape: a cold open, two hosts with distinct
 * perspectives, frequent hand-offs, segments, and an outro. It is guidance for
 * the writer only; it is never spoken and never replaces the user's own
 * instructions.
 *
 * Delivery syntax belongs to the engine, not to this brief. Fish Drama 3 asks for
 * `[whisper]` / `<emphasis>` marks; KugelAudio and Qwen3 forbid brackets and
 * emotion tags outright. One shared instruction therefore either suppresses the
 * tags one engine needs or injects the tags another will read aloud, so the brief
 * carries only the engine's own syntax.
 */

export type PodcastTagStyle = 'fish' | 'plain'

const PODCAST_STRUCTURE = [
  'Write this as a podcast episode for two hosts.',
  'Give each host a distinct, consistent personality and point of view.',
  'Open with a short cold open that states the episode question, then move through two to four clear segments.',
  'Let the hosts react to each other, ask follow-up questions, and hand off naturally; avoid long monologues.',
  'Close with a short recap and an outro.',
  // Read aloud, "Segment two:" becomes part of the episode. The hosts introduce
  // each part in their own words instead.
  'Never announce segment numbers, headings, or production labels inside the spoken lines: no "Segment one", no "Chapter two", no camera or scene directions. The hosts introduce each part naturally.',
  // The label is what assigns a voice. "HOST A:" is not a voice slot, so every
  // line would be read by the first speaker; "Speaker 1:" is the one convention
  // every speech engine maps onto its numbered slots.
  'Label every spoken line with Speaker 1: or Speaker 2:, one line per turn. When the attached voices name the hosts, use those exact names as the label instead.',
  'Write in the language of the brief.',
]

/** Fish Drama 3 hears direction in brackets and stress in paired tags. */
const PODCAST_TAGS_FISH = [
  'Use the engine\'s own delivery marks so the performance carries emotion: a bracketed cue before a phrase, such as [whisper], [laughs], [sighs], [pauses], [excited] or [softly], and <emphasis>...</emphasis> around a stressed word.',
  'Place a cue only where the delivery actually changes, at most one per line; everything inside them must still be words the host says out loud.',
]

/** KugelAudio and Qwen3 forbid markup: punctuation is the only prosody. */
const PODCAST_TAGS_PLAIN = [
  'This engine accepts no brackets, no emotion tags and no parentheses in the script: [tired], (laughing) and similar marks are read aloud or rejected. Convey emotion through word choice and punctuation only — commas and periods are the pauses, and a question mark is the intonation.',
]

/** Which delivery syntax the selected speech model accepts. */
export function podcastTagStyle(architecture: string | null | undefined): PodcastTagStyle {
  return /^fish/i.test(String(architecture || '')) ? 'fish' : 'plain'
}

export function podcastWriterBrief(style: PodcastTagStyle = 'plain'): string {
  return [
    ...PODCAST_STRUCTURE,
    ...(style === 'fish' ? PODCAST_TAGS_FISH : PODCAST_TAGS_PLAIN),
  ].join('\n')
}

/** Kept for callers that only want the engine-agnostic shape. */
export const PODCAST_WRITER_INSTRUCTIONS = podcastWriterBrief('plain')

/** Keep the user's instructions and append the podcast shape once. */
export function podcastWriterInstructions(
  current: string,
  style: PodcastTagStyle = 'plain',
): string {
  const brief = podcastWriterBrief(style)
  const existing = String(current || '').trim()
  if (!existing) return brief
  if (existing.includes(brief)) return existing
  return `${existing}\n\n${brief}`
}

/**
 * Most asks for "a podcast" do not say how long, and the writer then answers with
 * a trailer: a measured draft ran 111 words, about fifty seconds. A podcast needs
 * a length, so this is used when the brief states none.
 */
export const PODCAST_DEFAULT_MINUTES = 8

const DURATION_NUMBER = String.raw`\d+(?:[.,]\d+)?`
const DURATION_WORDS = (
  String.raw`one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty`
  + String.raw`|un|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|quince|veinte|treinta`
)
const DURATION_QUALIFIER = String.raw`about|around|roughly|approximately|approx\.?|some|unos|unas|aproximadamente|cerca\s+de`
/** Clause-final punctuation, or the end of the brief. */
const DURATION_CLAUSE_END = String.raw`(?=\s*(?:[.,;:!?)\]]|$))`
const DURATION_UNIT_MINUTES = String.raw`minutes?|mins?|minutos?`
const DURATION_UNIT_HOURS = String.raw`hours?|horas?`
const DURATION_UNIT_SECONDS = String.raw`seconds?|segundos?`

/**
 * The speech writer's own rules, so the default is only imposed when the brief
 * truly states no length. A bare length counts only where it ends a clause: "a
 * story about two travelers who waited ten minutes for a train" is narrative time.
 */
const DURATION_IN_BRIEF = [
  new RegExp(String.raw`\b(?:${DURATION_QUALIFIER})\s+(?:${DURATION_NUMBER}|${DURATION_WORDS})\s*(?:${DURATION_UNIT_MINUTES})\b`, 'i'),
  new RegExp(String.raw`\b(?:${DURATION_NUMBER}|${DURATION_WORDS})\s*(?:${DURATION_UNIT_MINUTES})\b${DURATION_CLAUSE_END}`, 'i'),
  new RegExp(String.raw`\b(?:${DURATION_QUALIFIER})\s+(?:${DURATION_NUMBER}|${DURATION_WORDS})\s*(?:${DURATION_UNIT_HOURS})\b`, 'i'),
  new RegExp(String.raw`\b(?:${DURATION_NUMBER}|${DURATION_WORDS})\s*(?:${DURATION_UNIT_HOURS})\b${DURATION_CLAUSE_END}`, 'i'),
  new RegExp(String.raw`\b(?:${DURATION_QUALIFIER})\s+(?:${DURATION_NUMBER})\s*(?:${DURATION_UNIT_SECONDS})\b`, 'i'),
  new RegExp(String.raw`\b(?:${DURATION_NUMBER})\s*(?:${DURATION_UNIT_SECONDS})\b${DURATION_CLAUSE_END}`, 'i'),
  new RegExp(String.raw`\b(?:${DURATION_NUMBER}|${DURATION_WORDS})\s*(?:words?|palabras?)\b`, 'i'),
]

/** True when the brief already asks for a length, so no default is imposed. */
export function briefStatesDuration(brief: string): boolean {
  const text = String(brief || '')
  return DURATION_IN_BRIEF.some(pattern => pattern.test(text))
}

/** The minutes a podcast brief asks for, or the podcast default when it says none. */
export function podcastTargetMinutes(brief: string): number {
  return briefStatesDuration(brief) ? 0 : PODCAST_DEFAULT_MINUTES
}
