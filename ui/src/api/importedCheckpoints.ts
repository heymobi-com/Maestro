// Removing an imported checkpoint, in its own module for the same reason the
// Director additions have one: upstream edits client.ts every release.

const BASE = ''  // same origin in production; Vite proxy handles /api in dev

export interface ImportedCheckpointRemoval {
  model_type: string
  registrations: string[]
  deleted_weights: string[]
  kept_weights: string[]
  deleted_sidecars: string[]
  cleared_preferences: string[]
}

export async function removeImportedCheckpoint(modelType: string): Promise<ImportedCheckpointRemoval> {
  const res = await fetch(`${BASE}/api/v1/checkpoints/${encodeURIComponent(modelType)}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to remove the imported checkpoint')
  return res.json()
}
