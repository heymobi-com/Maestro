// The review gate's own module, for the same reason our other Director additions have one:
// upstream edits client.ts every release.

export interface ReviewGateSetting {
  enabled: boolean
}

/** Whether a run stops after its shot images and waits for the reviewer. */
export async function fetchReviewGate(): Promise<ReviewGateSetting> {
  const res = await fetch('/api/v1/director/review-gate')
  if (!res.ok) throw new Error('Review setting unavailable')
  return res.json()
}

/** Remember the choice on the server, which is where the pipeline asks for it. */
export async function saveReviewGate(enabled: boolean): Promise<ReviewGateSetting> {
  const res = await fetch('/api/v1/director/review-gate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!res.ok) throw new Error('Could not save the review setting')
  return res.json()
}

export interface ContinuityFinding {
  /** The shot that would render the same frame as the one before it. */
  shot: number
  after_shot: number
  similarity: number
}

export interface ContinuityReadout {
  shots: number
  duplicates: ContinuityFinding[]
  error?: string
}

/** What the saved plan says about the shots, read from the plan rather than remembered. */
export async function fetchContinuity(pid: string): Promise<ContinuityReadout> {
  const res = await fetch(`/api/v1/director/pipelines/${encodeURIComponent(pid)}/continuity`)
  if (!res.ok) throw new Error('Continuity readout unavailable')
  return res.json()
}
