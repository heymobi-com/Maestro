/**
 * The writer's memory, and the two ways to move it.
 *
 * The writer holds what a render needs — measured on this machine, 32.7 GiB of RAM and 14915 MiB of
 * VRAM while loaded against 8.9 GiB and 5416 MiB while released. Maestro releases it by itself when
 * a render starts, and the server releases it when it has been idle; this is the manual switch, for
 * a director who knows a render is next, or who would rather not pay the reload on the first
 * planning call.
 *
 * The request goes to Maestro, not to the writer: a model server accepts its control endpoints only
 * from its own page, and a server-to-server call is the one that is allowed to ask.
 */

import { useCallback, useEffect, useState } from 'react'
import { Loader2, MemoryStick, RefreshCw } from 'lucide-react'
import { fetchWriterMemory, loadWriter, releaseWriter, type WriterMemory } from '../../api/writerRelease'

export function WriterReleaseControl({ url }: { url: string }) {
  const [memory, setMemory] = useState<WriterMemory | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')

  const read = useCallback(async () => {
    try {
      setMemory(await fetchWriterMemory())
    } catch (failure) {
      setNote(String(failure instanceof Error ? failure.message : failure))
    }
  }, [])

  useEffect(() => {
    void read()
  }, [read, url])

  const released = memory?.state === 'unloaded'
  const known = memory !== null && memory.state !== 'unknown'

  const act = async (action: 'release' | 'load') => {
    setBusy(true)
    setNote(action === 'load' ? 'Loading the model; this takes 20-50 s…' : '')
    try {
      if (action === 'load') {
        const answer = await loadWriter()
        setMemory(answer)
        setNote(answer.loaded ? 'Loaded.' : 'The writer did not load.')
      } else {
        const answer = await releaseWriter()
        setMemory(answer)
        setNote(
          answer.released
            ? 'Memory given back. The next planning call loads it again (20-50 s).'
            : 'Nothing to release.',
        )
      }
    } catch (failure) {
      setNote(String(failure instanceof Error ? failure.message : failure))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">
        Writer memory
      </label>
      <div className="flex items-center gap-2">
        <span className="flex items-center gap-1 text-xs text-text-secondary">
          <MemoryStick size={12} />
          {!known ? 'unknown' : released ? 'released' : 'loaded'}
        </span>
        {known && (
          <button
            onClick={() => act(released ? 'load' : 'release')}
            disabled={busy}
            className="flex items-center gap-1 px-2 py-1 text-[11px] border border-border rounded-lg text-text-secondary hover:text-text-primary disabled:opacity-40 transition-colors"
          >
            {busy && <Loader2 size={11} className="animate-spin" />}
            {released ? 'Load now' : 'Release memory'}
          </button>
        )}
        <button
          onClick={() => void read()}
          disabled={busy}
          title="Read the writer's memory again"
          className="text-text-muted hover:text-text-primary disabled:opacity-40 transition-colors"
        >
          <RefreshCw size={11} />
        </button>
      </div>
      <p className="text-[10px] text-text-muted mt-1">
        {note ||
          `Released while a render runs: the writer holds ~20 GiB of RAM and ~6 GiB of VRAM that the video model needs.${memory?.address ? ` (${memory.address})` : ''}`}
      </p>
    </div>
  )
}
