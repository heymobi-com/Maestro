/**
 * The writer's memory and the writer's process, from the place the director already is.
 *
 * Three states matter, and each has its own action:
 *
 *   loaded     it holds ~20 GiB of RAM and ~6 GiB of VRAM a render needs  -> Release memory
 *   released   it loads itself again on the next request, in 20-50 s      -> Load now
 *   down       no server at all: the model list is empty and planning does nothing, because
 *              there is nothing to list                                   -> Start now
 *
 * The third one is the chicken and egg reported from use: "si no esta funcionando strata maestro no
 * muestra el selector de model qwen3.8... es hasta que se inicia strata que lo puede uno
 * seleccionar". So a writer that is down is started here, once, with the command the operator saved,
 * and the panel is asked to refresh the model list when it comes up.
 *
 * The request goes to Maestro, not to the writer: a model server accepts its control endpoints only
 * from its own page, and a server-to-server call is the one that is allowed to ask.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { Loader2, MemoryStick, Play, RefreshCw, Save, Square } from 'lucide-react'
import {
  fetchWriterAutostart,
  fetchWriterMemory,
  loadWriter,
  releaseWriter,
  saveWriterAutostart,
  startWriter,
  type WriterAutostart,
  type WriterMemory,
} from '../../api/writerRelease'

export function WriterReleaseControl({ url, onStarted }: { url: string; onStarted?: () => void }) {
  const [memory, setMemory] = useState<WriterMemory | null>(null)
  const [autostart, setAutostart] = useState<WriterAutostart | null>(null)
  const [command, setCommand] = useState('')
  const [showCommand, setShowCommand] = useState(false)
  const [busy, setBusy] = useState('')
  const [note, setNote] = useState('')
  const tried = useRef(false)

  const read = useCallback(async () => {
    try {
      setMemory(await fetchWriterMemory())
    } catch (failure) {
      setNote(String(failure instanceof Error ? failure.message : failure))
    }
    try {
      const saved = await fetchWriterAutostart()
      setAutostart(saved)
      setCommand(current => current || saved.start)
    } catch {
      // The command is a convenience; the state above is what matters.
    }
  }, [])

  useEffect(() => {
    void read()
  }, [read, url])

  const down = memory?.state === 'unknown'
  const released = memory?.state === 'unloaded'
  const hasCommand = Boolean((autostart?.start || '').trim())

  const start = useCallback(async () => {
    setBusy('start')
    setNote('Starting the writer; this takes 40-90 s…')
    try {
      const answer = await startWriter()
      setMemory(answer)
      setNote(answer.started ? 'Writer started.' : 'The writer did not start.')
      if (answer.started) onStarted?.()
    } catch (failure) {
      setNote(String(failure instanceof Error ? failure.message : failure))
    } finally {
      setBusy('')
    }
  }, [onStarted])

  // A writer that is down offers nothing to select and nothing to plan with, so it is started once
  // per visit to this panel -- not on a timer, and only when a command was saved.
  useEffect(() => {
    if (tried.current || !down || !hasCommand) return
    tried.current = true
    void start()
  }, [down, hasCommand, start])

  const act = async (action: 'release' | 'load') => {
    setBusy(action)
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
      setBusy('')
    }
  }

  const save = async () => {
    setBusy('cmd')
    try {
      const saved = await saveWriterAutostart(command)
      setAutostart(saved)
      setNote(saved.start ? 'Start command saved.' : 'Cleared: Maestro will not start the writer itself.')
    } catch (failure) {
      setNote(String(failure instanceof Error ? failure.message : failure))
    } finally {
      setBusy('')
    }
  }

  const state = memory === null ? 'checking…' : down ? 'down' : released ? 'released' : 'loaded'

  return (
    <div>
      <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">
        Writer memory
      </label>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="flex items-center gap-1 text-xs text-text-secondary">
          <MemoryStick size={12} />
          {state}
        </span>
        {down ? (
          hasCommand && (
            <button
              onClick={start}
              disabled={Boolean(busy)}
              className="flex items-center gap-1 px-2 py-1 text-[11px] border border-border rounded-lg text-text-secondary hover:text-text-primary disabled:opacity-40 transition-colors"
            >
              {busy === 'start' ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />}
              Start now
            </button>
          )
        ) : (
          <button
            onClick={() => act(released ? 'load' : 'release')}
            disabled={Boolean(busy)}
            className="flex items-center gap-1 px-2 py-1 text-[11px] border border-border rounded-lg text-text-secondary hover:text-text-primary disabled:opacity-40 transition-colors"
          >
            {busy === 'load' || busy === 'release' ? <Loader2 size={11} className="animate-spin" /> : <Square size={11} />}
            {released ? 'Load now' : 'Release memory'}
          </button>
        )}
        <button
          onClick={() => setShowCommand(value => !value)}
          className="text-[10px] text-text-muted hover:text-text-primary transition-colors"
        >
          {showCommand ? 'hide start command' : 'start command'}
        </button>
        <button
          onClick={() => void read()}
          disabled={Boolean(busy)}
          title="Read the writer again"
          className="text-text-muted hover:text-text-primary disabled:opacity-40 transition-colors"
        >
          <RefreshCw size={11} />
        </button>
      </div>

      {memory?.arena?.short && (
        <div className="mt-1.5 rounded-lg border border-chip-orange/30 bg-orange-500/10 px-2 py-1 text-[10px] leading-relaxed text-chip-orange">
          {memory.arena.warning}
        </div>
      )}

      {showCommand && (
        <div className="flex items-center gap-2 mt-1.5">
          <input
            value={command}
            onChange={event => setCommand(event.target.value)}
            placeholder="Command that starts the writer (empty = never start it)"
            spellCheck={false}
            className="flex-1 bg-bg-tertiary border border-border rounded-lg px-2 py-1 text-[10px] font-mono text-text-primary focus:outline-none focus:border-accent-blue"
          />
          <button
            onClick={save}
            disabled={Boolean(busy)}
            className="flex items-center gap-1 px-2 py-1 text-[10px] border border-border rounded-lg text-text-secondary hover:text-text-primary disabled:opacity-40 transition-colors"
          >
            {busy === 'cmd' ? <Loader2 size={10} className="animate-spin" /> : <Save size={10} />}
            Save
          </button>
        </div>
      )}

      <p className="text-[10px] text-text-muted mt-1">
        {note ||
          (down
            ? hasCommand
              ? 'The writer is not running. Maestro starts it when it is needed.'
              : 'The writer is not running, and no start command is saved: set one here, or start it yourself.'
            : `Released while a render runs: the writer holds ~20 GiB of RAM and ~6 GiB of VRAM that the video model needs.${memory?.address ? ` (${memory.address})` : ''}`)}
      </p>
    </div>
  )
}
