/**
 * Content formats for spoken writing, beyond the two-host podcast.
 *
 * A podcast, a documentary, a narrated story and an audio drama are not the same
 * script with a different label: they differ in structure (cold open vs. hook vs.
 * act), in who speaks (two hosts, a narrator, characters in conflict), and in what
 * holds a listener (segments, promises, escalating stakes, revealed secrets).
 *
 * Two rules apply to every format:
 *
 *   - the delivery syntax belongs to the engine (`PodcastTagStyle`): Fish reads
 *     `[whisper]` and `<emphasis>` as direction, while Kugel and Qwen reject any
 *     markup, so the brief carries the engine's own dialect;
 *   - a format states who speaks. Dialogue needs a label the engine can bind to a
 *     voice slot; narration must not carry one, or the label is read aloud.
 */

import type { PodcastTagStyle } from './podcastWriter'

export type SpeechMode = 'podcast' | 'interview' | 'documentary' | 'story' | 'drama'

/** How the format is spoken, which decides the label rule and the writer mode. */
export type SpeechVoiceShape = 'dialogue' | 'narration'

export interface SpeechModeSpec {
  id: SpeechMode
  label: string
  /** The one-line description the menu shows under the label. */
  hint: string
  shape: SpeechVoiceShape
  /** Minutes the format needs when the brief states no length. */
  minutes: number
  /** Structure and retention devices, in the writer's own words. */
  structure: string[]
}

export const SPEECH_MODES: SpeechModeSpec[] = [
  {
    id: 'podcast',
    label: 'Write Podcast',
    hint: 'Two hosts, segments and an outro',
    shape: 'dialogue',
    minutes: 8,
    structure: [
      'Write this as a podcast episode for two hosts.',
      'Give each host a distinct, consistent personality and point of view.',
      'Open with a short cold open that states the episode question, then move through two to four clear segments.',
      'Let the hosts react to each other, ask follow-up questions, and hand off naturally; avoid long monologues.',
      'Close with a short recap and an outro.',
      'Never announce segment numbers, headings, or production labels inside the spoken lines: no "Segment one", no "Chapter two", no camera or scene directions. The hosts introduce each part naturally.',
      'Label every spoken line with Speaker 1: or Speaker 2:, one line per turn. When the attached voices name the hosts, use those exact names as the label instead.',
    ],
  },
  {
    id: 'interview',
    label: 'Write Interview',
    hint: 'One host, one guest, answers that reveal',
    shape: 'dialogue',
    minutes: 8,
    structure: [
      'Write this as a long-form interview between one host and one guest.',
      'The host asks short, concrete questions; the guest answers at length, in their own voice.',
      'Open with the single question the whole interview is trying to answer.',
      'Let the guest reveal something new every few turns: a number, a mistake, a reversal, a cost.',
      'Avoid flattery and generic praise; the tension is in specifics, not in compliments.',
      'Close by returning to the opening question with what the guest now knows.',
      'Never announce sections, question numbers, or directions inside the spoken lines.',
      'Label every spoken line with Speaker 1: for the host and Speaker 2: for the guest, one line per turn.',
    ],
  },
  {
    id: 'documentary',
    label: 'Write Documentary',
    hint: 'One narrator, chapters, evidence',
    shape: 'narration',
    minutes: 10,
    structure: [
      'Write this as a narrated audio documentary, spoken by a single narrator.',
      'Open with a concrete scene or a hard number in the first fifteen seconds; never open with a definition.',
      'Move through three to five chapters. Each chapter opens with a promise of what is about to be revealed and closes having paid it off.',
      'Ground every claim in something the listener can picture: a date, a place, a person, a measurement.',
      'Quote at most two short outside voices, and introduce them in the narration rather than switching speakers.',
      'Close with the question the evidence leaves open, not with a summary of what was already said.',
      'Write continuous narration without speaker labels, without headings, and without camera or scene directions.',
    ],
  },
  {
    id: 'story',
    label: 'Write Story',
    hint: 'One narrator, scenes, escalating stakes',
    shape: 'narration',
    minutes: 6,
    structure: [
      'Write this as a narrated story told by a single voice.',
      'Give the protagonist one concrete want and one concrete obstacle in the first paragraph.',
      'Tell it in scenes, not in summary: a place, someone acting, something changing.',
      'Escalate: every scene should make the previous decision cost more than the last.',
      'Use short sentences for tension and longer ones for reflection; let the rhythm carry the pace.',
      'End on a turn the listener did not see coming but can re-read as inevitable.',
      'Write continuous narration without speaker labels, without headings, and without camera or scene directions.',
    ],
  },
  {
    id: 'drama',
    label: 'Write Audio Drama',
    hint: 'Characters, conflict, a twist',
    shape: 'dialogue',
    minutes: 8,
    structure: [
      'Write this as an audio drama for two or three characters. Nothing is seen, so everything must be heard.',
      'Open in the middle of a conflict already in progress; no introductions, no setup.',
      'Every character wants something from another character in the scene, and says so indirectly.',
      'Let information arrive unevenly: one character knows more than they admit, and the listener learns it through what is said, not through narration.',
      'Keep scenes short. A scene ends when someone has lost ground.',
      'End with a reveal that re-frames the first scene.',
      'Never describe what a camera would see. Sound, breath and silence are the only stage.',
      'Label every spoken line with Speaker 1:, Speaker 2: or Speaker 3:, one line per turn.',
    ],
  },
]

export function speechModeSpec(mode: SpeechMode): SpeechModeSpec {
  return SPEECH_MODES.find(candidate => candidate.id === mode) ?? SPEECH_MODES[0]
}

/** The writer mode each format needs: a monologue guide or a dialogue guide. */
export function speechModeWriterMode(mode: SpeechMode): 'dialogue' | 'monologue' {
  return speechModeSpec(mode).shape === 'dialogue' ? 'dialogue' : 'monologue'
}

/** How many voice slots the format needs at minimum. */
export function speechModeVoices(mode: SpeechMode): number {
  return speechModeSpec(mode).shape === 'dialogue' ? 2 : 1
}

/** Fish Drama 3 hears direction in brackets and stress in paired tags. */
const TAGS_FISH = [
  'Use the engine\'s own delivery marks so the performance carries emotion: a bracketed cue before a phrase, such as [whisper], [laughs], [sighs], [pauses], [excited] or [softly], and <emphasis>...</emphasis> around a stressed word.',
  'Place a cue only where the delivery actually changes, at most one per line; everything inside them must still be words the speaker says out loud.',
]

/** KugelAudio and Qwen3 forbid markup: punctuation is the only prosody. */
const TAGS_PLAIN = [
  'This engine accepts no brackets, no emotion tags and no parentheses in the script: [tired], (laughing) and similar marks are read aloud or rejected. Convey emotion through word choice and punctuation only — commas and periods are the pauses, and a question mark is the intonation.',
]

const LANGUAGE_LINE = 'Write in the language of the brief.'

/** The complete writing brief for one format and one engine. */
export function speechModeBrief(mode: SpeechMode, style: PodcastTagStyle = 'plain'): string {
  const spec = speechModeSpec(mode)
  return [
    ...spec.structure,
    ...(style === 'fish' ? TAGS_FISH : TAGS_PLAIN),
    LANGUAGE_LINE,
  ].join('\n')
}

/** Keep the user's own instructions and append this format's brief once. */
export function speechModeInstructions(
  current: string,
  mode: SpeechMode,
  style: PodcastTagStyle = 'plain',
): string {
  const brief = speechModeBrief(mode, style)
  const existing = String(current || '').trim()
  if (!existing) return brief
  if (existing.includes(brief)) return existing
  return `${existing}\n\n${brief}`
}
