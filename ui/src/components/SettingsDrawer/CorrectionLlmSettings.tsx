/**
 * The LLM that answers "Correct this shot", separated from the pipeline's.
 *
 * A correction is interactive and happens while clips are rendering, and the
 * pipeline's LLM is a single resident model shared by every Director pass. Sending
 * corrections through it costs the pipeline: a hosted choice unloads the local model
 * (which the next planning pass reloads), and a local choice cannot come up while the
 * video model holds the GPU. Naming a hosted provider here gives the editing stage its
 * own endpoint, so it touches neither -- which is the whole reason this control exists.
 *
 * The model list follows the provider chosen here, not the pipeline's. Asking the
 * endpoint that will actually answer is what lets a hosted model be picked for
 * corrections while the pipeline keeps its local one loaded; a list built for the
 * pipeline's provider offers local models only, which made this choice unreachable.
 *
 * The panel above shows its endpoint fields only for its own remote providers, so when
 * this stage is pointed at an endpoint those fields are hidden and this block carries
 * them: a correction must not be sent to a URL the user cannot see or correct.
 */

import { useEffect, useState } from 'react'

import { fetchCorrectionModels } from '../../api/correctionModels'
import { ApiKeyField } from '../shared/ApiKeyField'
import type { LlmModelOption, ServicesConfig } from '../../types'

const PROVIDER_OPTIONS: Array<[string, string]> = [
  ['', 'Same as the LLM above'],
  ['local', 'Local (llama-server)'],
  ['remote', 'Remote OpenAI-Compatible (LM Studio, etc.)'],
  ['openai', 'OpenAI API'],
  ['anthropic', 'Anthropic API'],
]

const HOSTED_PROVIDERS = new Set(['remote', 'openai', 'anthropic'])

/** The provider the endpoint tags each model with; the shared type does not name it yet. */
function modelProvider(model: LlmModelOption): string {
  return (model as { provider?: string }).provider || 'local'
}

export function CorrectionLlmSettings({
  servicesConfig,
  updateConfig,
  llmModels,
}: {
  servicesConfig: ServicesConfig
  updateConfig: (patch: Partial<ServicesConfig>, options?: { throwOnError?: boolean }) => Promise<void>
  llmModels: LlmModelOption[]
}) {
  const provider = servicesConfig.revision_llm_provider || ''
  const hosted = HOSTED_PROVIDERS.has(provider)
  const model = servicesConfig.revision_llm_model_id || ''
  const pipelineProvider = servicesConfig.llm_provider || 'local'
  // Mirrors the panel's own condition, so the endpoint is rendered exactly once: here
  // when the block above hides it, and there when it does not.
  const endpointAbove = pipelineProvider === 'remote' || pipelineProvider === 'openai'
  const [endpointModels, setEndpointModels] = useState<LlmModelOption[]>([])
  const [listFailed, setListFailed] = useState(false)
  const [listTick, setListTick] = useState(0)

  useEffect(() => {
    if (!hosted) return
    let live = true
    setListFailed(false)
    fetchCorrectionModels(provider)
      .then(models => { if (live) setEndpointModels(models) })
      .catch(() => { if (live) { setEndpointModels([]); setListFailed(true) } })
    return () => { live = false }
  }, [hosted, provider, listTick])

  const offered = hosted ? endpointModels : llmModels.filter(m => modelProvider(m) === 'local')
  // A model already saved for this stage stays listed even when the endpoint does not
  // offer it, so the control never reads as unset while a stored value is in force.
  const options = model && !offered.some(option => option.id === model)
    ? [{ id: model, label: model, size_hint: 'saved' }, ...offered]
    : offered

  return (
    <div className="space-y-3">
      <div>
        <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">
          Correction LLM Provider (shot edits)
        </label>
        <select
          value={provider}
          onChange={e => updateConfig({ revision_llm_provider: e.target.value })}
          className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-blue"
        >
          {PROVIDER_OPTIONS.map(([value, label]) => (
            <option key={value || 'same'} value={value}>{label}</option>
          ))}
        </select>
        <p className="text-[10px] text-text-muted mt-1">
          {hosted
            ? 'Corrections go straight to this endpoint, so they run while a clip is rendering and the pipeline keeps its own model loaded.'
            : 'Corrections use the pipeline\u2019s LLM, which reloads it when the two differ.'}
        </p>
      </div>

      {hosted && !endpointAbove && (
        <div className="space-y-3">
          {provider !== 'anthropic' && (
            <div>
              <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">
                {provider === 'remote' ? 'Server URL' : 'API Base URL'}
              </label>
              <input
                type="text"
                value={servicesConfig.llm_remote_url}
                onChange={e => updateConfig({ llm_remote_url: e.target.value })}
                onBlur={() => setListTick(tick => tick + 1)}
                placeholder={provider === 'remote' ? 'http://192.168.1.100:1234' : 'https://api.openai.com'}
                className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-blue"
              />
            </div>
          )}
          {provider === 'remote' && (
            <ApiKeyField
              label="Server API Key"
              maskedValue={servicesConfig.llm_remote_api_key}
              isSet={servicesConfig.llm_remote_api_key_set}
              onSave={async value => {
                await updateConfig({ llm_remote_api_key: value }, { throwOnError: true })
                setListTick(tick => tick + 1)
              }}
            />
          )}
          <p className="text-[10px] text-text-muted">
            {provider === 'anthropic'
              ? 'Its key is the Anthropic API Key under API Keys below.'
              : 'The same endpoint and key as the LLM above: one place, so the two cannot drift apart.'}
          </p>
        </div>
      )}

      <div>
        <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">
          Correction LLM Model (shot edits)
        </label>
        {hosted ? (
          <>
            <input
              type="text"
              list="correction-llm-model-options"
              value={model}
              onChange={e => updateConfig({ revision_llm_model_id: e.target.value })}
              placeholder="Same as the LLM above"
              className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-blue"
            />
            <datalist id="correction-llm-model-options">
              {options.map(option => (
                <option key={option.id} value={option.id}>{option.label} ({option.size_hint})</option>
              ))}
            </datalist>
          </>
        ) : (
          <select
            value={model}
            onChange={e => updateConfig({ revision_llm_model_id: e.target.value })}
            className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-blue"
          >
            <option value="">Same as the LLM above</option>
            {options.map(option => (
              <option key={option.id} value={option.id}>{option.label} ({option.size_hint})</option>
            ))}
          </select>
        )}
        {hosted && (
          <p className="text-[10px] text-text-muted mt-1">
            {listFailed
              ? `Could not list ${provider} models. Type the name this endpoint expects.`
              : `${options.length} models from this endpoint, local ones included.`}
          </p>
        )}
        <p className="text-[10px] text-text-muted mt-1">
          {servicesConfig.revision_llm_model_id ? 'Separate, smaller LLM for \u201cCorrect this shot\u201d \u2014 faster, and it never re-types the project text.' : 'Using the LLM above for shot corrections (slower, and it has the project text to protect).'}
        </p>
      </div>
    </div>
  )
}
