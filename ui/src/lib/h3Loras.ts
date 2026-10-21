/**
 * Adapters that cannot be stacked on the H3 Fused / baked-Turbo checkpoints.
 *
 * Director keeps its LoRA selection per mode, not per model, so switching the
 * video model leaves the previous model's acceleration adapter selected. The
 * backend refuses that request ("Turbo is already baked in; additional
 * acceleration recipes cannot be stacked") and every queued task fails
 * validation before any GPU work, which reads as "Task 8 failed validation".
 *
 * The names are the ones the model options publish as managed presets plus the
 * VDN default adapter. Header-based checks (DoRA, renamed PDD files) remain the
 * backend's job; this only stops a known-stale selection from travelling.
 */

export type LoraSelectionBlob = {
  activated_loras: string[]
  loras_multipliers: string
  loraWeights: Record<string, number[]>
  availableLoras: string[]
}

const BAKED_ACCELERATION_ADAPTER_RE =
  /(?:^|[^a-z0-9])(?:turbo|pdd|acc|acceleration|accelerator|vdn)(?:[^a-z0-9]|$)/i

export function isH3BakedAccelerationAdapter(filename: string): boolean {
  return BAKED_ACCELERATION_ADAPTER_RE.test(String(filename || ''))
}

/** Whether this model ships Turbo inside its transformer. */
export function isH3FusedTurboModel(
  modelType: string,
  options?: { minimax_h3_fused_turbo?: boolean } | null,
): boolean {
  if (options?.minimax_h3_fused_turbo === true) return true
  return String(modelType || '').toLowerCase().includes('fused_turbo')
}

/**
 * The selection with baked acceleration adapters removed.
 *
 * Returns the same object when nothing changes, so callers can compare
 * identity to skip an unnecessary save.
 */
export function withoutBakedAccelerationAdapters<T extends LoraSelectionBlob | undefined>(
  blob: T,
): T {
  if (!blob) return blob
  const activated = blob.activated_loras || []
  const kept = activated.filter(name => !isH3BakedAccelerationAdapter(name))
  if (kept.length === activated.length) return blob
  const savedWeights = blob.loraWeights || {}
  const weights: Record<string, number[]> = Object.fromEntries(
    kept.map(name => [name, savedWeights[name]?.length ? savedWeights[name] : [1]]),
  )
  const multipliers = kept
    .map(name => (weights[name] || [1]).map(value => value.toFixed(2)).join(';'))
    .join(' ')
  return {
    ...blob,
    activated_loras: kept,
    loras_multipliers: multipliers,
    loraWeights: weights,
  } as T
}
