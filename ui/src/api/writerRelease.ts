// The writer's memory, in its own module for the same reason the Director additions have one:
// upstream edits client.ts every release.

export interface WriterMemory {
  state: 'loaded' | 'unloaded' | 'unknown'
  address: string
  detail?: string
}

/** What the writer is holding right now. */
export async function fetchWriterMemory(): Promise<WriterMemory> {
  const res = await fetch('/api/v1/llm/writer/memory')
  if (!res.ok) throw new Error('Writer memory unavailable')
  return res.json()
}

/** Give the writer's memory back now (the server loads it again on the next request). */
export async function releaseWriter(): Promise<WriterMemory & { released: boolean }> {
  const res = await fetch('/api/v1/llm/writer/memory/release', { method: 'POST' })
  if (!res.ok) throw new Error('Could not release the writer')
  return res.json()
}

/** Load the writer now, so the first call of the session is not the slow one (~20-50 s). */
export async function loadWriter(): Promise<WriterMemory & { loaded: boolean }> {
  const res = await fetch('/api/v1/llm/writer/memory/load', { method: 'POST' })
  if (!res.ok) throw new Error('Could not load the writer')
  return res.json()
}
