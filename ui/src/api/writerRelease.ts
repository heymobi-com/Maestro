// The writer's memory, in its own module for the same reason the Director additions have one:
// upstream edits client.ts every release.

export interface ArenaForecast {
  known: boolean
  free_gib: number
  arena_gib: number
  /** True only when the writer is down and starting it now would cap its cache. */
  short: boolean
  warning: string
}

export interface WriterMemory {
  state: 'loaded' | 'unloaded' | 'unknown'
  address: string
  detail?: string
  /** What a writer started right now would be able to hold resident. */
  arena?: ArenaForecast
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

/** What Maestro would run to start a writer that is not running at all. */
export interface WriterAutostart {
  start: string
  wait_seconds: number
}

export async function fetchWriterAutostart(): Promise<WriterAutostart> {
  const res = await fetch('/api/v1/llm/writer/autostart')
  if (!res.ok) throw new Error('Writer autostart unavailable')
  return res.json()
}

/** Set or clear the command. Empty means Maestro never starts the writer by itself. */
export async function saveWriterAutostart(start: string): Promise<WriterAutostart> {
  const res = await fetch('/api/v1/llm/writer/autostart', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ start }),
  })
  if (!res.ok) throw new Error('Could not save the start command')
  return res.json()
}

/** Start a stopped writer and wait for it (this is the call that ends the chicken and egg). */
export async function startWriter(): Promise<WriterMemory & { started: boolean }> {
  const res = await fetch('/api/v1/llm/writer/start', { method: 'POST' })
  if (!res.ok) throw new Error('Could not start the writer')
  return res.json()
}
