/**
 * The interactive planning pass that is running right now.
 *
 * A long timeline is planned in batches that can take ten minutes. Before this
 * existed the only sign of progress was the planner's raw text output, so a
 * reloaded window could not tell that anything was happening at all, let alone
 * offer a way to stop it.
 *
 * Ours, and kept out of `client.ts` on purpose; that file re-exports this module,
 * so importers keep using `../api/client`.
 */

export interface DirectorPlanOperation {
  id: string
  kind: string
  label: string
  stage: string
  message: string
  current: number
  total: number
  cancelling: boolean
  elapsed_seconds: number
  updated_at: number
}

/** Read the pass that is running, or null when nothing is being planned. */
export async function fetchDirectorPlanOperation(): Promise<DirectorPlanOperation | null> {
  const res = await fetch('/api/v1/director/plan-operation', { cache: 'no-store' })
  if (!res.ok) throw new Error('Could not read the Director planning status')
  const data = await res.json().catch(() => null)
  return data && data.id ? (data as DirectorPlanOperation) : null
}

/** Ask the running planning pass to stop after the batch it is planning now. */
export async function cancelDirectorPlanOperation(operationId: string): Promise<void> {
  const res = await fetch('/api/v1/director/plan-operation/cancel', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ operation_id: operationId }),
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: 'Could not stop the planning pass' }))
    throw new Error(detail.detail || 'Could not stop the planning pass')
  }
}
