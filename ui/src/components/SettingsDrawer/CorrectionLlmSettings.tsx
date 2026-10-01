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
 * The model list is the one the panel already loaded for the selected provider; the
 * hosted endpoint and its key are the ones configured above, so this needs one choice.
 */

import type { LlmModelOption, ServicesConfig } from '../../types'

type Patch = Record<string, unknown>

const PROVIDER_OPTIONS: Array<[string, string]> = [
  ['', 'Same as the LLM above'],
  ['local', 'Local (llama-server)'],
  ['remote', 'Remote OpenAI-Compatible (LM Studio, etc.)'],
  ['openai', 'OpenAI API'],
  ['anthropic', 'Anthropic API'],
]

export function CorrectionLlmSettings({
  servicesConfig,
  updateConfig,
  llmModels,
}: {
  servicesConfig: ServicesConfig
  updateConfig: (patch: Patch) => void
  llmModels: LlmModelOption[]
}) {
  const provider = servicesConfig.revision_llm_provider || ''
  const hosted = provider === 'remote' || provider === 'openai' || provider === 'anthropic'

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

      <div>
        <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">
          Correction LLM Model (shot edits)
        </label>
        <select
          value={servicesConfig.revision_llm_model_id || ''}
          onChange={e => updateConfig({ revision_llm_model_id: e.target.value })}
          className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-blue"
        >
          <option value="">Same as the LLM above</option>
          {llmModels.map(m => <option key={m.id} value={m.id}>{m.label} ({m.size_hint})</option>)}
        </select>
        <p className="text-[10px] text-text-muted mt-1">
          {servicesConfig.revision_llm_model_id ? 'Separate, smaller LLM for \u201cCorrect this shot\u201d \u2014 faster, and it never re-types the project text.' : 'Using the LLM above for shot corrections (slower, and it has the project text to protect).'}
        </p>
      </div>
    </div>
  )
}
