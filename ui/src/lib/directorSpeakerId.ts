/**
 * Stable Maestro/H3 speaker labels.
 *
 * Diarization answers with pyannote's own names (`SPEAKER_00`), while the H3
 * prompt format speaks in `(S1)` / `(S2)` and the cast the user names is keyed by
 * those labels. Normalizing where the analysis lands keeps the raw names out of
 * the UI and out of the prompts, and lets the pitch measurement address the same
 * speakers the transcript does.
 *
 * Ours: it lives here rather than in the store upstream edits every release.
 */

import type { AudioAnalysisResult, LyricSegment, SpeakerMapping } from '../types'

export function normalizeDirectorSpeakerId(value: string | null | undefined): string {
  const text = (value ?? '').toString().trim()
  if (!text) return '(S1)'
  const upper = text.toUpperCase()
  if (upper.startsWith('(S') && upper.endsWith(')')) return text
  if (upper.startsWith('SPEAKER_') || upper.startsWith('SPEAKER')) {
    const match = /\d+/.exec(text)
    const speakerIndex = match ? Number(match[0]) + 1 : 1
    return `(S${speakerIndex})`
  }
  if (/^S\d+$/i.test(text)) return `(${text})`
  return text
}

/**
 * The cast a diarized analysis implies, ready for the speaker-mapping rows.
 *
 * The two call sites are not identical and must stay that way: a music video
 * names its voices from the measured pitch, while a short film's cast speaks and
 * is left for the user to name.
 */
export function speakersFromAnalysis(
  analysis: Pick<AudioAnalysisResult, 'lyrics'> & { voice_profiles?: AudioAnalysisResult['voice_profiles'] },
  options: { role?: SpeakerMapping['role']; prefillNames?: boolean } = {},
): { speakers: string[]; speakerMappings: SpeakerMapping[] } {
  const role = options.role ?? ''
  const prefillNames = options.prefillNames ?? true

  const speakers: string[] = []
  const seen = new Set<string>()
  for (const segment of (analysis.lyrics ?? []) as LyricSegment[]) {
    const speakerId = normalizeDirectorSpeakerId(segment.speaker)
    if (seen.has(speakerId)) continue
    seen.add(speakerId)
    speakers.push(speakerId)
  }

  const speakerMappings: SpeakerMapping[] = speakers.map(speakerId => ({
    speakerId,
    // Pre-fill from the pitch this same analysis measured, so the user never has
    // to run diarization again just to name the voices.
    name: prefillNames ? (analysis.voice_profiles?.[speakerId]?.gender || '') : '',
    role,
  }))
  return { speakers, speakerMappings }
}
