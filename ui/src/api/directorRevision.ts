/**
 * One turn of the correction conversation for a shot.
 *
 * The assistant reports what in the prompt causes the problem, asks when the note
 * is missing an intent, and returns a rewrite only when it keeps every spoken
 * line, carries exactly one shot and passes the prompt contract. Nothing is saved
 * here: the rewrite lands in the prompt editor for review.
 *
 * Ours, and kept out of `client.ts` on purpose; that file re-exports this module,
 * so importers keep using `../api/client`.
 */

export interface RevisionTurn {
  role: 'director' | 'assistant'
  text: string
}

export interface ShotDiagnosis {
  shots: number[]
  duration_seconds: number
  dialogue_blocks: number
  subject_positions: Array<{ subject: string; position: string }>
  behind_speaker: string[]
  errors: string[]
  findings: string[]
}

export interface RevisionAnswer {
  clip_index: number
  analysis: string
  question: string
  /** One-line choices: a prompt edit, or the change that has to happen outside it. */
  options: string[]
  /** What to say when there is no proposal and no question: never a bare refusal. */
  note: string
  /** Words in the proposal that look damaged while re-typing, not corrected. */
  warnings: string[]
  /** This shot's own text: the part that varies, and the only part an answer may change. */
  clip_prompt: string
  /** The same part of the proposal: what the comparison shows. */
  clip_proposed: string
  diagnosis: ShotDiagnosis
  rewritten: boolean
  errors: string[]
  video_prompt: string
}

export async function reviseClipPrompt(
  pid: string,
  clipIndex: number,
  instruction: string,
  prompt?: string,
  history: RevisionTurn[] = [],
): Promise<RevisionAnswer> {
  const res = await fetch(`/api/v1/director/pipelines/${encodeURIComponent(pid)}/clips/${clipIndex}/revise-prompt`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      instruction,
      prompt: prompt || undefined,
      history: history.length ? history : undefined,
    }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: 'Could not revise the prompt' }))
    throw new Error(err.error || err.detail || 'Could not revise the prompt')
  }
  return res.json()
}
