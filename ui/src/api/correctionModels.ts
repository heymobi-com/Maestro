/**
 * The models offered by the endpoint a correction will actually use.
 *
 * `/api/v1/llm/models` answers for the *configured* provider, which is the pipeline's,
 * so that list is the wrong one for an editing stage pointed somewhere else. Passing the
 * correction's own provider returns the models that endpoint serves -- and the backend
 * resolves the matching credential itself, so this needs no key on the client.
 */

import type { LlmModelOption } from '../types'

export async function fetchCorrectionModels(provider: string): Promise<LlmModelOption[]> {
  const res = await fetch(
    `/api/v1/llm/models?provider=${encodeURIComponent(provider)}`,
    { cache: 'no-store' },
  )
  if (!res.ok) throw new Error(`Failed to list the models of ${provider}`)
  const data = await res.json().catch(() => null)
  return Array.isArray(data?.models) ? (data.models as LlmModelOption[]) : []
}
