/**
 * The assistant's endpoints, called from one place.
 *
 * The assistant keeps its own settings rather than sharing the Services panel's: its provider,
 * model and endpoint are separate so a chat can point at a llama.cpp on the LAN while the
 * pipeline talks to something else. Reading and writing them goes through the assistant's own
 * routes, which is also why nothing here touches the shared services store.
 *
 * The image path reuses the app's own uploader, so an attached picture lands in the same
 * `uploads/` folder every other asset does rather than a second place with its own cleanup.
 */

import { uploadImage } from './client'

export type AssistantSettings = {
  /** The provider in force, after the pipeline fallback. */
  provider: string
  /** True when the assistant has no endpoint and the pipeline's LLM answers. */
  follows_pipeline: boolean
  model: string
  /** True when the assistant has its own endpoint and no model chosen for it yet. */
  needs_model: boolean
  endpoint: string
  /** Whether an image may be sent here. */
  vision: boolean
  /** True when the text and any image would leave this machine. */
  routes_off_machine: boolean
  /** True when the chat is answered by the same writer a render uses. */
  shares_pipeline_writer: boolean
  max_tokens: number
  configured_provider: string
  configured_model: string
  configured_endpoint: string
  configured_max_tokens: number
  api_key_set: boolean
  api_key_masked: string
}

export type AssistantModel = {
  id: string
  label?: string
  size_hint?: string
  provider?: string
}

/** What one endpoint answered, and which list the answer came from. */
export type AssistantModels = {
  models: AssistantModel[]
  provider: string
  /** The base address that was asked, once normalised. */
  endpoint: string
  /**
   * `endpoint` when the address itself answered, `catalogue` when the pipeline's own list is the
   * one that can be picked (`local`, or nothing configured yet), `none` when it did not answer.
   */
  source: 'endpoint' | 'catalogue' | 'none'
  /** True when the address answered; null when the question does not apply. */
  reachable: boolean | null
  /** The model to choose without asking, when the server publishes exactly one. */
  suggested_model: string
  /** Why no model was listed, for the field to say out loud. */
  detail: string
}

export type AssistantRead = {
  tool: string
  arguments?: Record<string, unknown>
  result: Record<string, unknown>
}

export type AssistantProposal = {
  kind: 'prompt' | 'tag'
  shot: number
  prompt?: string
  current?: string
  tag?: string
  acceptable?: boolean
  problems?: string[]
  error?: string
}

export type AssistantReply = {
  reply: string
  reads: AssistantRead[]
  proposals: AssistantProposal[]
}

export type AssistantTurn = { role: 'user' | 'assistant'; content: string }

/** Where the person is standing, so an answer can be about this screen. */
export type AssistantContext = {
  area?: string
  workspace?: string | null
  pipeline_id?: string | null
  shot?: number | null
}

async function jsonOrThrow<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const detail = (body as { detail?: string; error?: string } | null)?.detail
      ?? (body as { error?: string } | null)?.error
    throw new Error(detail || fallback)
  }
  return (await response.json()) as T
}

export async function fetchAssistantSettings(): Promise<AssistantSettings> {
  const res = await fetch('/api/v1/assistant/settings', { cache: 'no-store' })
  return jsonOrThrow<AssistantSettings>(res, 'Could not read the assistant settings.')
}

export async function saveAssistantSettings(
  patch: Partial<Pick<AssistantSettings, 'configured_provider' | 'configured_model'
    | 'configured_endpoint' | 'configured_max_tokens'>> & {
      chat_llm_api_key?: string
    },
): Promise<AssistantSettings> {
  const body: Record<string, unknown> = {}
  if ('configured_provider' in patch) body.chat_llm_provider = patch.configured_provider
  if ('configured_model' in patch) body.chat_llm_model_id = patch.configured_model
  if ('configured_endpoint' in patch) body.chat_llm_remote_url = patch.configured_endpoint
  if ('configured_max_tokens' in patch) body.chat_llm_max_tokens = patch.configured_max_tokens
  if ('chat_llm_api_key' in patch) body.chat_llm_api_key = patch.chat_llm_api_key
  const res = await fetch('/api/v1/assistant/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return jsonOrThrow<AssistantSettings>(res, 'Could not save the assistant settings.')
}

/**
 * The models of the endpoint the assistant will really use, or of one the form is still holding.
 *
 * `/api/v1/llm/models` answers for the pipeline's provider, which is the wrong list when the
 * assistant points somewhere else -- and its remote branch prepends the built-in catalogue, so an
 * address that never answered still comes back populated. This asks the address itself and says
 * where the list came from, and it takes the *typed* address so a server can be recognised the
 * moment its IP is entered, before anything is saved.
 */
export async function fetchAssistantModels(
  candidate?: { url?: string; provider?: string },
): Promise<AssistantModels> {
  const query = new URLSearchParams()
  if (candidate?.url) query.set('url', candidate.url)
  if (candidate?.provider) query.set('provider', candidate.provider)
  const suffix = query.toString()
  const res = await fetch(`/api/v1/assistant/models${suffix ? `?${suffix}` : ''}`, { cache: 'no-store' })
  if (!res.ok) throw new Error('Could not list the models of the assistant endpoint.')
  const data = await res.json().catch(() => null)
  const source = data?.source
  return {
    models: Array.isArray(data?.models) ? (data.models as AssistantModel[]) : [],
    provider: String(data?.provider || ''),
    endpoint: String(data?.endpoint || ''),
    source: source === 'endpoint' || source === 'catalogue' ? source : 'none',
    reachable: typeof data?.reachable === 'boolean' ? data.reachable : null,
    suggested_model: String(data?.suggested_model || ''),
    detail: String(data?.detail || ''),
  }
}

export async function askAssistant(
  messages: AssistantTurn[],
  context: AssistantContext,
  images: string[] = [],
): Promise<AssistantReply> {
  const res = await fetch('/api/v1/assistant/message', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages, images, ...context }),
  })
  return jsonOrThrow<AssistantReply>(res, 'The assistant did not answer.')
}

/** Upload one pasted or chosen image, and get back the path the assistant can read. */
export async function uploadAssistantImage(file: File): Promise<string> {
  const uploaded = await uploadImage(file)
  return uploaded.path
}

/** Apply a proposal. This is the only call here that writes to a project. */
export async function applyAssistantProposal(
  proposal: AssistantProposal,
  pipelineId: string,
  workspace?: string | null,
): Promise<void> {
  const res = await fetch('/api/v1/assistant/apply', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      kind: proposal.kind,
      shot: proposal.shot,
      prompt: proposal.prompt,
      tag: proposal.tag,
      pipeline_id: pipelineId,
      workspace: workspace || '',
    }),
  })
  await jsonOrThrow<{ applied: boolean }>(res, 'The edit could not be applied.')
}
