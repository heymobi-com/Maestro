// The prompt audit for a saved pipeline, in its own module for the same reason the Director
// additions have one: upstream edits client.ts every release.

/** One audit finding, as the pipeline reports it. */
export interface PromptAuditFinding {
  code: string
  severity: string
  message: string
}

/** The findings that belong to one shot. */
export interface PromptAuditShot {
  shot: number
  index: number
  findings: PromptAuditFinding[]
}

/** The audit of one saved pipeline. */
export interface PromptAudit {
  findings: PromptAuditShot[]
  report?: string
}

/** The audit of one saved pipeline, or null when the pipeline has no saved plan yet. */
export async function fetchPromptAudit(pid: string): Promise<PromptAudit | null> {
  const res = await fetch(`/api/v1/director/pipelines/${encodeURIComponent(pid)}/prompt-audit`)
  if (res.status === 404) return null
  if (!res.ok) throw new Error('Prompt audit unavailable')
  return res.json()
}
