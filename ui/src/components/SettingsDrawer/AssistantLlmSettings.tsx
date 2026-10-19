/**
 * The assistant's own endpoint, model and key.
 *
 * A chat is interactive and may be used while clips are rendering, so it gets its own provider
 * instead of sharing the pipeline's: the shared local path would compete for the GPU the video
 * model is holding, and a hosted choice through `llm_service` would unload the pipeline's model
 * and make the next planning pass pay the reload. Choosing a provider here touches neither.
 *
 * The endpoint is the assistant's own field rather than the shared one, because the case this
 * exists for -- a llama.cpp on this machine or on the LAN -- is usually not the endpoint the
 * pipeline uses. Leaving it empty falls back to the shared one.
 *
 * Entering an address is enough: on the way out of that field the endpoint is asked what it
 * serves, and a server that publishes exactly one model has it filled in without a second
 * question. Two or more is a choice, so the list is offered and nothing is written. The probe
 * runs on leaving the field rather than on every keystroke -- a URL being typed is ten addresses,
 * and none of them is worth a request.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import {
  fetchAssistantModels,
  fetchAssistantSettings,
  saveAssistantSettings,
  type AssistantModel,
  type AssistantSettings,
} from '../../api/assistant'
import { ApiKeyField } from '../shared/ApiKeyField'

const PROVIDER_OPTIONS: Array<[string, string]> = [
  ['', 'Same as the LLM above'],
  ['remote', 'Remote OpenAI-Compatible (llama.cpp, LM Studio, Ollama)'],
  ['openai', 'OpenAI API'],
  ['local', 'Local (llama-server, shared with the pipeline)'],
]

const HOSTED = new Set(['remote', 'openai'])

/** What the last probe found, so the field can say it rather than stay silent. */
type Probe =
  | { state: 'idle' }
  | { state: 'probing'; url: string }
  | { state: 'answered'; source: 'endpoint' | 'catalogue'; endpoint: string; count: number }
  | { state: 'failed'; detail: string }

/** Whether an endpoint is this machine itself, rather than another one on the network. */
function isOnThisMachine(endpoint: string): boolean {
  const host = (endpoint || '').trim().toLowerCase()
    .replace(/^[a-z]+:\/\//, '').replace(/\/.*$/, '').replace(/:\d+$/, '').replace(/^\[|\]$/g, '')
  return host === 'localhost' || host.startsWith('127.') || host === '::1' || host === '0.0.0.0'
}

const FIELD = 'w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm '
  + 'text-text-primary focus:outline-none focus:border-accent-blue'
const LABEL = 'text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block'
const NOTE = 'text-[10px] text-text-muted mt-1'

export function AssistantLlmSettings() {
  const [saved, setSaved] = useState<AssistantSettings | null>(null)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [models, setModels] = useState<AssistantModel[]>([])
  const [probe, setProbe] = useState<Probe>({ state: 'idle' })
  // The two free-text fields are edited locally and written on the way out, so a keystroke is
  // never a save and the probe never sees a half-typed address.
  const [endpointText, setEndpointText] = useState('')
  const [modelText, setModelText] = useState('')
  const [tick, setTick] = useState(0)

  const provider = saved?.configured_provider ?? ''
  const hosted = HOSTED.has(provider)

  // Read by callbacks that must not restart on a keystroke, and dropped when an answer that
  // arrived late is no longer the answer to the question being asked.
  const latest = useRef<AssistantSettings | null>(null)
  latest.current = saved
  const probeSeq = useRef(0)
  const noteTimer = useRef<number | undefined>(undefined)

  useEffect(() => () => window.clearTimeout(noteTimer.current), [])

  useEffect(() => {
    let live = true
    fetchAssistantSettings()
      .then(settings => { if (live) { setSaved(settings); setError('') } })
      .catch(err => { if (live) setError(err instanceof Error ? err.message : 'Unavailable') })
    return () => { live = false }
  }, [tick])

  const save = useCallback(async (
    patch: Parameters<typeof saveAssistantSettings>[0],
  ): Promise<AssistantSettings | null> => {
    setError('')
    try {
      const next = await saveAssistantSettings(patch)
      setSaved(next)
      setNote('Saved')
      window.clearTimeout(noteTimer.current)
      noteTimer.current = window.setTimeout(() => setNote(''), 2500)
      return next
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save.')
      return null
    }
  }, [])

  /**
   * Ask an endpoint what it serves, and fill in what only it can know.
   *
   * The autofill is deliberately conditional: one model means the server has already answered the
   * question, two or more is the user's choice, and a model already chosen is never overwritten.
   */
  const detect = useCallback(async (
    url: string, providerName: string, options: { autoFill: boolean },
  ) => {
    const seq = ++probeSeq.current
    setProbe({ state: 'probing', url })
    try {
      const listed = await fetchAssistantModels({ url, provider: providerName })
      if (seq !== probeSeq.current) return
      setModels(listed.models)
      if (listed.source === 'endpoint') {
        setProbe({
          state: 'answered', source: 'endpoint', endpoint: listed.endpoint, count: listed.models.length,
        })
      } else if (listed.source === 'catalogue') {
        setProbe({ state: 'answered', source: 'catalogue', endpoint: '', count: listed.models.length })
      } else {
        setProbe({ state: 'failed', detail: listed.detail || 'This endpoint did not answer.' })
      }
      const chosen = (latest.current?.configured_model || '').trim()
      if (options.autoFill && listed.suggested_model && !chosen) {
        setModelText(listed.suggested_model)
        await save({ configured_model: listed.suggested_model })
      }
    } catch (err) {
      if (seq !== probeSeq.current) return
      setModels([])
      setProbe({ state: 'failed', detail: err instanceof Error ? err.message : 'Unavailable' })
    }
  }, [save])

  const providerOnDisk = saved?.configured_provider
  const endpointOnDisk = saved?.configured_endpoint
  useEffect(() => {
    if (!saved) return
    setEndpointText(saved.configured_endpoint || '')
    setModelText(saved.configured_model || '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [endpointOnDisk, saved?.configured_model])

  useEffect(() => {
    if (!saved) return
    const own = saved.configured_provider
    if (own === 'remote' || own === 'openai' || own === 'local') {
      void detect(saved.configured_endpoint, own, { autoFill: true })
    } else {
      probeSeq.current += 1
      setModels([])
      setProbe({ state: 'idle' })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providerOnDisk, endpointOnDisk, tick, detect])

  if (error && !saved) {
    return (
      <div className="space-y-2">
        <p className={LABEL}>Assistant</p>
        <p className="text-[11px] text-text-muted">{error}</p>
      </div>
    )
  }
  if (!saved) {
    return <p className="text-[11px] text-text-muted">Reading the assistant settings…</p>
  }

  /** Leaving the address is the moment to save it, and to ask it what it serves. */
  const commitEndpoint = async () => {
    const typed = endpointText.trim()
    if (typed === (saved.configured_endpoint || '')) {
      if (typed) void detect(typed, provider, { autoFill: true })
      return
    }
    await save({ configured_endpoint: typed })
  }

  const commitModel = async () => {
    const typed = modelText.trim()
    if (typed !== (saved.configured_model || '')) await save({ configured_model: typed })
  }

  const probeNote = () => {
    if (probe.state === 'probing') {
      return `Asking ${probe.url || 'the address'} what it serves…`
    }
    if (probe.state === 'answered' && probe.source === 'endpoint') {
      return `${probe.count} model(s) reported by ${probe.endpoint}`
        + (probe.count > 1 ? ' — pick one; more than one is a choice, so none was filled in.' : '.')
    }
    if (probe.state === 'answered') {
      return `${probe.count} model(s) from the pipeline's own catalogue.`
    }
    if (probe.state === 'failed') {
      return `${probe.detail} Type the name the server expects.`
    }
    return ''
  }

  return (
    <div className="space-y-3">
      <div>
        <label className={LABEL}>Assistant LLM Provider (the chat)</label>
        <select
          value={provider}
          onChange={e => void save({ configured_provider: e.target.value })}
          className={FIELD}
        >
          {PROVIDER_OPTIONS.map(([value, label]) => (
            <option key={value || 'same'} value={value}>{label}</option>
          ))}
        </select>
        <p className={NOTE}>
          {hosted
            ? 'The chat talks straight to this endpoint, so it answers while a render is running and the pipeline keeps its own model loaded.'
            : provider === 'local'
              ? 'The chat uses the pipeline\u2019s llama-server: one model in memory, and a chat turn waits for a render to finish before it can answer.'
              : 'The chat uses the pipeline\u2019s LLM.'}
        </p>
      </div>

      {hosted && (
        <div>
          <label className={LABEL}>
            {provider === 'remote' ? 'Assistant Server URL' : 'Assistant API Base URL'}
          </label>
          <input
            type="text"
            value={endpointText}
            onChange={e => setEndpointText(e.target.value)}
            onBlur={() => void commitEndpoint()}
            placeholder={provider === 'remote'
              ? 'http://127.0.0.1:8080'
              : provider === 'openai' ? 'https://api.openai.com' : ''}
            className={FIELD}
          />
          <p className={NOTE}>
            The address the server was started with, as you would open it (a trailing <code>/v1</code>
            {' '}or <code>/</code> is trimmed, not kept). Empty uses the pipeline&apos;s endpoint.
          </p>
        </div>
      )}

      {hosted && (
        <ApiKeyField
          label="Assistant API Key"
          maskedValue={saved.api_key_masked}
          isSet={saved.api_key_set}
          onSave={async value => {
            await save({ chat_llm_api_key: value })
            setTick(current => current + 1)
          }}
        />
      )}

      <div>
        <label className={LABEL}>Assistant Model</label>
        <input
          type="text"
          list="assistant-model-options"
          value={modelText}
          onChange={e => setModelText(e.target.value)}
          onBlur={() => void commitModel()}
          placeholder={hosted
            ? (models[0]?.id || 'gemma-4-E4B-it')
            : 'Same as the LLM above'}
          disabled={!hosted && provider !== 'local'}
          className={FIELD}
        />
        <datalist id="assistant-model-options">
          {models.map(model => (
            <option key={model.id} value={model.id}>
              {model.label || model.id}{model.size_hint ? ` (${model.size_hint})` : ''}
            </option>
          ))}
        </datalist>
        <p className={NOTE}>{probeNote() || 'Anything you type is kept as written.'}</p>
      </div>

      <div>
        <label className={LABEL}>Answer length (tokens)</label>
        <input
          type="number"
          min={256}
          max={16384}
          step={256}
          value={saved.configured_max_tokens}
          onChange={e => setSaved({ ...saved, configured_max_tokens: Number(e.target.value) })}
          onBlur={() => void save({ configured_max_tokens: saved.configured_max_tokens })}
          className={FIELD}
        />
      </div>

      <div className="rounded-lg border border-border bg-bg-tertiary/50 p-2.5 space-y-1">
        <p className="text-[10px] text-text-muted">
          <span className="uppercase tracking-wider">In force:</span> {saved.provider}
          {saved.model ? ` · ${saved.model}` : ''}
          {saved.endpoint ? ` · ${saved.endpoint}` : ''}
        </p>
        {saved.needs_model && (
          <p className="text-[10px] text-amber-300">
            This endpoint has no model chosen for it, so a chat turn has nothing to ask. Its own
            list is offered in the field above; a server that publishes one model is filled in
            automatically.
          </p>
        )}
        {saved.shares_pipeline_writer && (
          <p className="text-[10px] text-amber-300">
            The chat is answered by the writer the render uses. Every turn occupies it, so a
            question competes with whatever is generating. Give the assistant its own endpoint
            above to keep them apart.
          </p>
        )}
        <p className="text-[10px] text-text-muted">
          {saved.vision
            ? 'Images are sent with a question.'
            : 'This endpoint reports text only: an attached image is dropped rather than failing the turn.'}
          {' '}
          {saved.routes_off_machine
            ? 'Your text and any image leave this machine: they go to the endpoint above.'
            : isOnThisMachine(saved.endpoint)
              ? 'They stay on this machine.'
              : 'They go to another machine on your network.'}
        </p>
      </div>

      {error && <p className="text-[10px] text-red-400">{error}</p>}
      {!error && note && <p className="text-[10px] text-emerald-400">{note}</p>}
    </div>
  )
}
