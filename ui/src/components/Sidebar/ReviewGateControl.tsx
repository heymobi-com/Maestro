/**
 * The switch that stops a run after its images, and the way out of that stop.
 *
 * Reported from use: "The workflow stops after generating images, but I'm unsatisfied with the
 * result. The images have errors. I want to correct them by regenerating, but the system doesn't
 * allow it... it says 'pipeline is still active, try again shortly'."
 *
 * Three things were missing at once. The run never stopped where the images could be judged, because
 * auto mode (on by default) skipped the review entirely. A paused run refused every correction, so
 * even the gate that existed could not be acted behind. And nothing in the interface could leave the
 * pause: `continuePipeline` was defined in the store and called by no component, and the Dashboard's
 * Resume button is offered for crashed, failed and cancelled runs rather than paused ones.
 *
 * The setting is read from the server, which is where the pipeline asks for it, and the readout is
 * read from the saved plan, so a corrected prompt stops being reported the moment it is corrected.
 */

import { useCallback, useEffect, useState } from 'react'
import { useStore } from '../../stores/useStore'
import {
  fetchContinuity,
  fetchReviewGate,
  saveReviewGate,
  type ContinuityFinding,
  type ContinuityStep,
} from '../../api/reviewGate'

const REVIEW_PAUSES = ['review_images', 'review_prompts']

export function ReviewGateControl() {
  const pipelineId = useStore(s => s.pipelineId)
  const pipelineStatus = useStore(s => s.pipelineStatus)
  const continuePipeline = useStore(s => s.continuePipeline)
  const pollPipelineStatus = useStore(s => s.pollPipelineStatus)

  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [duplicates, setDuplicates] = useState<ContinuityFinding[]>([])
  const [steps, setSteps] = useState<ContinuityStep[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const parked =
    pipelineStatus?.status === 'paused' && REVIEW_PAUSES.includes(pipelineStatus.pause_reason || '')

  useEffect(() => {
    let cancelled = false
    fetchReviewGate()
      .then(setting => { if (!cancelled) setEnabled(setting.enabled) })
      .catch(() => { if (!cancelled) setEnabled(null) })
    return () => { cancelled = true }
  }, [])

  const readPlan = useCallback(async () => {
    if (!pipelineId) { setDuplicates([]); setSteps([]); return }
    try {
      const readout = await fetchContinuity(pipelineId)
      setDuplicates(readout.duplicates || [])
      setSteps(readout.steps || [])
    } catch {
      setDuplicates([])
      setSteps([])
    }
  }, [pipelineId])

  // Re-read at the review, which is the moment the answer can still change the run.
  useEffect(() => { void readPlan() }, [readPlan, pipelineStatus?.phase, parked])

  const toggle = useCallback(async (next: boolean) => {
    setBusy(true); setError(null)
    try {
      const setting = await saveReviewGate(next)
      setEnabled(setting.enabled)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [])

  const resume = useCallback(async () => {
    setBusy(true); setError(null)
    try {
      await continuePipeline()
      pollPipelineStatus()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [continuePipeline, pollPipelineStatus])

  return (
    <div className="space-y-1 rounded-lg border border-accent-blue/25 bg-accent-blue/10 px-3 py-2 text-[10px] leading-relaxed text-text-secondary">
      <label className="flex cursor-pointer items-start gap-2">
        <input
          type="checkbox"
          className="mt-0.5"
          checked={enabled !== false}
          disabled={busy || enabled === null}
          onChange={e => void toggle(e.target.checked)}
        />
        <span>
          Review the shot images before the video renders
          <span className="block text-text-muted">
            The run stops once, after it has generated a start image per shot: correct or regenerate
            any of them, then continue. Auto mode no longer skips this stop.
          </span>
        </span>
      </label>

      {duplicates.length > 0 && (
        <div className="border-t border-accent-blue/15 pt-1 text-chip-orange">
          {duplicates.map(finding => (
            <div key={finding.shot}>
              Shots {finding.after_shot} and {finding.shot} carry{' '}
              {Math.round(finding.similarity * 100)}% the same image prompt, so they render as the
              same frame. Correct one of them before continuing.
            </div>
          ))}
        </div>
      )}

      {steps.some(step => step.still) && (
        <div className="border-t border-accent-blue/15 pt-1 text-chip-orange">
          {steps.filter(step => step.still).map(step => (
            <div key={step.shot}>
              Shots {step.after_shot} and {step.shot} differ by {step.change}% of the frame, so the
              clip between them has almost nothing to move through. Regenerate one of them, or widen
              the change its prompt describes.
            </div>
          ))}
        </div>
      )}

      {steps.length > 0 && (
        <div className="border-t border-accent-blue/15 pt-1 text-text-muted">
          Where each clip travels from: {' '}
          {steps.map(step => `${step.after_shot}→${step.shot} ${step.change}%`).join(' · ')}
        </div>
      )}

      {parked && (
        <div className="flex items-center gap-2 border-t border-accent-blue/15 pt-1">
          <button
            type="button"
            onClick={() => void resume()}
            disabled={busy}
            className="rounded border border-indicator-success/30 bg-green-500/10 px-2 py-0.5 text-indicator-success hover:bg-green-500/20 disabled:opacity-40"
          >
            {busy ? 'Continuing…' : 'Continue to video'}
          </button>
          <span className="text-text-muted">
            {pipelineStatus?.pause_reason === 'review_images'
              ? 'Waiting for you: the images are generated and no video has been rendered.'
              : 'Waiting for you: the plan is ready and no image has been generated.'}
          </span>
        </div>
      )}

      {error && <div className="text-indicator-danger">{error}</div>}
    </div>
  )
}

export default ReviewGateControl
