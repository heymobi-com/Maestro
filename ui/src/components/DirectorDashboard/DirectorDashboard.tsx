import { useState, useEffect, useRef, Component, type ReactNode } from 'react'
import { X, ChevronDown, ChevronRight, Play, ImageIcon, Check, AlertTriangle, Clock, Brain, Sparkles, Loader2, Camera, Film, Combine, Pencil, Trash2, Maximize2, Eye, FileVideo, Volume2 } from 'lucide-react'
import { FloatingPanel } from './FloatingPanel'
import { useStore } from '../../stores/useStore'
import { fetchModelOptions, fetchWriterDrafts, getFileUrl, reviseClipPrompt, updateClipPrompt, type RevisionAnswer, type RevisionTurn } from '../../api/client'
import { createDirectorFinal, inspectDirectorShot, selectDirectorShotTake, type DirectorShotInspection, type DirectorTake } from './directorDraftFinalApi'
import { LoadWriterDraftHistory } from '../WriterDraftHistory'
import type { PipelineClipState, SavedPipelineState } from '../../types'
import type { ModelOptions, ResolutionPreset } from '../../types'
import { multipleShotWarning } from '../../lib/h3Prompt'
import { HighlightedTextarea } from './HighlightedTextarea'
import { PromptDiffView } from './PromptDiffView'
import { splitRevisionOptions } from '../../lib/revisionOptions'
import { PromptAuditNotices } from './PromptAuditNotices'

/** Safely coerce any value to a displayable string */
function safeStr(val: unknown): string {
  if (val == null) return ''
  if (typeof val === 'string') return val
  if (typeof val === 'object') return JSON.stringify(val)
  return String(val)
}

/** Error boundary to prevent dashboard crash from bad data */
class DashboardErrorBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null }
  static getDerivedStateFromError(err: Error) { return { error: err.message } }
  render() {
    if (this.state.error) {
      return (
        <div className="p-4 text-center">
          <p className="text-red-400 text-sm mb-2">Dashboard render error: {this.state.error}</p>
          <button onClick={() => this.setState({ error: null })}
            className="text-xs text-accent-blue hover:underline">Try again</button>
        </div>
      )
    }
    return this.props.children
  }
}

function formatTime(sec: number | null): string {
  if (!sec) return '--'
  if (sec < 60) return `${Math.round(sec)}s`
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return `${m}m ${s}s`
}

function formatDate(ts: number): string {
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

type ReviewablePipelineClip = PipelineClipState & {
  takes?: DirectorTake[]
  selected_take_id?: string | null
}

type DraftFinalPipeline = SavedPipelineState & {
  render_stage?: 'draft' | 'final' | string | null
  draft_final?: {
    stage?: 'draft' | 'final' | string
    source_pipeline_id?: string
    final_resolution?: string
    final_resolution_preset?: string
  } | null
  clip_takes?: DirectorTake[][]
  clip_selected_take_ids?: Record<string, string>
}

type FinalPresetOption = {
  value: string
  label: string
  hint?: string
}

function getFinalPresetOptions(options: ModelOptions | null, aspect: string): FinalPresetOption[] {
  if (!options) return []
  const order = options.resolution_preset_order || Object.keys(options.resolution_presets || {}) as ResolutionPreset[]
  const presets = options.resolution_presets || {}
  const mapped = order.flatMap(name => {
    if (name === 'auto') return []
    const item = presets[name]
    if (!item) return []
    const values = item.values as Record<string, string | undefined>
    const canvas = values[aspect] || values.auto
    return typeof canvas === 'string' && canvas
      ? [{ value: name, label: item.label || name, hint: item.hint || undefined }]
      : []
  })
  if (mapped.length) return mapped
  const fixed = (options as ModelOptions & { resolutions?: Array<{ label?: string; value: string } | string> }).resolutions || []
  return fixed.flatMap(item => {
    const value = typeof item === 'string' ? item : item.value
    if (!/^\d{2,5}x\d{2,5}$/.test(value)) return []
    return [{ value, label: typeof item === 'string' ? item : item.label || value }]
  })
}

function ShotReviewPanel({
  pipelineId,
  clip,
  pipeline,
  busy,
  onTakeSelected,
}: {
  pipelineId: string
  clip: ReviewablePipelineClip
  pipeline: DraftFinalPipeline
  busy: boolean
  onTakeSelected: () => Promise<void>
}) {
  const [open, setOpen] = useState(false)
  const [inspection, setInspection] = useState<DirectorShotInspection | null>(null)
  const [inspectionBusy, setInspectionBusy] = useState(false)
  const [takeBusy, setTakeBusy] = useState<string | null>(null)
  const [error, setError] = useState('')
  const takes = clip.takes || pipeline.clip_takes?.[clip.index] || []
  const selectedTakeId = clip.selected_take_id || pipeline.clip_selected_take_ids?.[String(clip.index)]
  const canSelect = ['completed', 'failed', 'cancelled', 'crashed'].includes(pipeline.status)
    && !clip.video_stale

  const inspect = async () => {
    setInspectionBusy(true)
    setError('')
    try {
      const result = await inspectDirectorShot(pipelineId, clip.index)
      setInspection(result)
      setOpen(true)
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : String(requestError))
      setOpen(true)
    } finally {
      setInspectionBusy(false)
    }
  }

  const selectTake = async (takeId: string) => {
    setTakeBusy(takeId)
    setError('')
    try {
      await selectDirectorShotTake(pipelineId, clip.index, takeId)
      setInspection(null)
      setOpen(false)
      await onTakeSelected()
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : String(requestError))
    } finally {
      setTakeBusy(null)
    }
  }

  const workspace = pipeline.workspace
  const technical = inspection?.review.technical
  const refs = inspection?.reference_evidence

  return (
    <div className="border border-border rounded-md bg-bg-tertiary/50 p-2 space-y-2">
      <div className="flex items-center gap-2">
        <button
          onClick={() => { if (open) setOpen(false); else void inspect() }}
          disabled={inspectionBusy || busy || !clip.video_filename || clip.video_stale}
          className="flex items-center gap-1.5 px-2 py-1 text-[10px] rounded border border-accent-blue/30 bg-accent-blue/10 text-accent-blue hover:bg-accent-blue/20 disabled:opacity-40"
          title={clip.video_stale ? 'Regenerate this shot before reviewing it' : 'Inspect the selected rendered shot'}
        >
          {inspectionBusy ? <Loader2 size={11} className="animate-spin" /> : <Eye size={11} />}
          {inspectionBusy ? 'Inspecting…' : open ? 'Hide review' : 'Review shot'}
        </button>
        {takes.length > 0 && <span className="text-[9px] text-text-muted">{takes.length} saved take{takes.length === 1 ? '' : 's'}</span>}
        {pipeline.draft_final?.stage === 'final' && (
          <span className="ml-auto text-[9px] text-chip-purple">Draft / Final comparison</span>
        )}
      </div>

      {takes.length > 1 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          {takes.map(take => (
            <div key={take.id} className={`rounded border p-1.5 space-y-1 ${take.id === selectedTakeId ? 'border-accent-blue/60 bg-accent-blue/5' : 'border-border'}`}>
              <div className="flex items-center justify-between gap-2">
                <span className="text-[10px] font-medium text-text-primary">{take.label}</span>
                <div className="flex items-center gap-1.5">
                  {take.approved && <span className="text-[8px] text-indicator-success">Approved Draft</span>}
                  {take.id === selectedTakeId && <span className="text-[8px] text-accent-blue">Selected for timeline</span>}
                </div>
              </div>
              <video
                src={getFileUrl(take.filename, workspace)}
                className="w-full aspect-video rounded bg-black"
                controls
                preload="metadata"
                aria-label={`Shot ${clip.index + 1} ${take.label} take`}
              />
              <div className="flex items-center justify-between gap-2 text-[8px] text-text-muted">
                <span>{take.resolution || 'Resolution unavailable'}{take.seed != null ? ` · seed ${take.seed}` : ''}</span>
                {canSelect && take.id !== selectedTakeId && (
                  <button onClick={() => void selectTake(take.id)} disabled={!!takeBusy || busy}
                    className="px-1.5 py-0.5 rounded border border-border hover:border-accent-blue/50 text-text-secondary disabled:opacity-40">
                    {takeBusy === take.id ? 'Selecting…' : 'Use this take'}
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {open && inspection && (
        <div className="space-y-2">
          <video
            src={getFileUrl(inspection.filename, inspection.workspace)}
            className="w-full max-h-[360px] aspect-video rounded bg-black"
            controls
            preload="metadata"
            aria-label={`Shot ${clip.index + 1} selected take review`}
          />
          {inspection.artifact_filenames.contact_sheet && (
            <img src={getFileUrl(inspection.artifact_filenames.contact_sheet, inspection.workspace)}
              alt={`Shot ${clip.index + 1} sampled frames`} className="w-full rounded border border-border" />
          )}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-1.5 text-[9px] text-text-secondary">
            <div className="rounded bg-bg-tertiary p-1.5">Probe: {technical?.probe.status || 'unknown'}</div>
            <div className="rounded bg-bg-tertiary p-1.5">Duration: {formatTime(technical?.duration_seconds ?? null)}</div>
            <div className="rounded bg-bg-tertiary p-1.5">Canvas: {technical?.geometry?.width || '?'} × {technical?.geometry?.height || '?'}</div>
            <div className="rounded bg-bg-tertiary p-1.5">Software decode: {technical?.software_decode.status || 'unknown'}</div>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-[9px] text-text-muted">
            <span className="flex items-center gap-1"><Volume2 size={10} /> Audio stream: {technical?.audio?.present == null ? 'unknown' : technical.audio.present ? 'present' : 'not present'}</span>
            {technical?.container_format && <span>Container: {technical.container_format}</span>}
            {inspection.artifact_filenames.audio_preview && (
              <audio controls preload="none" className="h-7 max-w-full" src={getFileUrl(inspection.artifact_filenames.audio_preview, inspection.workspace)} aria-label={`Shot ${clip.index + 1} audio preview`} />
            )}
          </div>
          {refs && (
            <div className="rounded border border-border p-2 space-y-1">
              <div className="text-[9px] uppercase tracking-wider text-text-muted">Attached reference checks</div>
              {refs.location_references.length ? refs.location_references.map((item, index) => (
                <div key={`${item.filename}-${index}`} className="text-[9px] text-text-secondary">
                  Location: {item.label} — {item.status === 'available' ? 'file available' : 'file missing'}
                </div>
              )) : <div className="text-[9px] text-text-muted">No location references attached.</div>}
              <div className="text-[9px] text-text-secondary">
                Voice reference: {refs.voice_reference.status === 'available' ? 'file available' : refs.voice_reference.status === 'missing' ? 'file missing' : 'not attached'}
              </div>
              <div className="text-[8px] text-text-muted">{refs.voice_verification.message} Location checks report file presence; they do not assess scene matching.</div>
            </div>
          )}
          <div className="text-[8px] text-text-muted">Technical media inspection only. Creative quality, prompt adherence, and continuity are not assessed.</div>
        </div>
      )}
      {open && error && <p role="alert" className="text-[9px] text-red-400">{error}</p>}
    </div>
  )
}

function PipelineProgressBar({ pipeline }: { pipeline: SavedPipelineState }) {
  const phases = [
    { key: 'planning', label: 'LLM Planning', time: pipeline.llm_log?.planning_time_sec },
    { key: 'images', label: 'Image Gen', time: pipeline.clips.reduce((sum, c) => sum + (c.image_gen_time_sec || 0), 0) || null },
    { key: 'video', label: 'Video Gen', time: pipeline.clips.reduce((sum, c) => sum + (c.video_gen_time_sec || 0), 0) || null },
  ]
  const total = phases.reduce((s, p) => s + (p.time || 0), 0) || 1
  const isComplete = pipeline.status === 'completed'

  return (
    <div className="space-y-1">
      <div className="flex h-2 rounded-full overflow-hidden bg-bg-tertiary">
        {phases.map((phase, i) => {
          const pct = (phase.time || 0) / total * 100
          const colors = ['bg-purple-500', 'bg-blue-500', 'bg-green-500']
          return pct > 0 ? (
            <div key={i} className={`${colors[i]} transition-all`} style={{ width: `${Math.max(pct, 3)}%` }} />
          ) : null
        })}
      </div>
      <div className="flex justify-between text-[9px] text-text-muted">
        {phases.map((phase, i) => (
          <span key={i} className="flex items-center gap-1">
            <span className={`w-1.5 h-1.5 rounded-full ${['bg-purple-500', 'bg-blue-500', 'bg-green-500'][i]}`} />
            {phase.label}: {formatTime(phase.time ?? null)}
          </span>
        ))}
        <span className="flex items-center gap-1">
          {isComplete ? <Check size={9} className="text-indicator-success" /> : <Clock size={9} />}
          Total: {formatTime(pipeline.total_time_sec)}
        </span>
      </div>
    </div>
  )
}

function LlmPassView({ pass: p, index }: { pass: { pass: string; system_prompt: string; user_prompt?: string; response_text: string; thinking_text?: string | null }; index: number }) {
  const [showSystem, setShowSystem] = useState(false)
  const [showUser, setShowUser] = useState(false)
  const [showResponse, setShowResponse] = useState(false)
  const [showThinking, setShowThinking] = useState(false)
  const label = p.pass.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())

  return (
    <div className="border border-border rounded p-2 space-y-1.5">
      <div className="text-[10px] font-medium text-text-primary">Pass {index + 1}: {label}</div>

      <button onClick={() => setShowSystem(!showSystem)}
        className="flex items-center gap-1 text-[9px] text-text-secondary hover:text-text-primary w-full text-left">
        {showSystem ? <ChevronDown size={9} /> : <ChevronRight size={9} />}
        System Prompt ({p.system_prompt?.length || 0} chars)
      </button>
      {showSystem && (
        <pre className="text-[8px] text-text-muted bg-bg-tertiary rounded p-2 max-h-48 overflow-auto whitespace-pre-wrap font-mono">
          {p.system_prompt || '(empty)'}
        </pre>
      )}

      {/* User Prompt — the actual story description / screenplay sent
          alongside the system prompt. Renders only when present so old
          pipeline JSON files (captured before user_prompt was tracked)
          don't show an "(empty)" row. */}
      {p.user_prompt !== undefined && p.user_prompt !== null && (
        <>
          <button onClick={() => setShowUser(!showUser)}
            className="flex items-center gap-1 text-[9px] text-text-secondary hover:text-text-primary w-full text-left">
            {showUser ? <ChevronDown size={9} /> : <ChevronRight size={9} />}
            User Prompt ({p.user_prompt?.length || 0} chars)
          </button>
          {showUser && (
            <pre className="text-[8px] text-text-muted bg-bg-tertiary rounded p-2 max-h-48 overflow-auto whitespace-pre-wrap font-mono">
              {p.user_prompt || '(empty)'}
            </pre>
          )}
        </>
      )}

      {p.thinking_text && (
        <>
          <button onClick={() => setShowThinking(!showThinking)}
            className="flex items-center gap-1 text-[9px] text-indicator-warning hover:text-indicator-warning/80 w-full text-left">
            {showThinking ? <ChevronDown size={9} /> : <ChevronRight size={9} />}
            <Sparkles size={8} /> Thinking ({p.thinking_text.length} chars)
          </button>
          {showThinking && (
            <pre className="text-[8px] text-text-muted bg-bg-tertiary rounded p-2 max-h-48 overflow-auto whitespace-pre-wrap font-mono">
              {p.thinking_text}
            </pre>
          )}
        </>
      )}

      <button onClick={() => setShowResponse(!showResponse)}
        className="flex items-center gap-1 text-[9px] text-text-secondary hover:text-text-primary w-full text-left">
        {showResponse ? <ChevronDown size={9} /> : <ChevronRight size={9} />}
        Response ({p.response_text?.length || 0} chars)
      </button>
      {showResponse && (
        <pre className="text-[8px] text-text-muted bg-bg-tertiary rounded p-2 max-h-48 overflow-auto whitespace-pre-wrap font-mono">
          {p.response_text || '(empty)'}
        </pre>
      )}
    </div>
  )
}

function LlmLogPanel({ pipeline }: { pipeline: SavedPipelineState }) {
  const log = pipeline.llm_log
  if (!log) return <p className="text-xs text-text-muted italic">No LLM log captured</p>

  const passes = log.passes

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-[10px] text-text-muted">
        <Brain size={12} className="text-chip-purple" />
        <span>{log.provider}/{log.model_id || 'unknown'}</span>
        <span className="ml-1 text-text-muted">({passes?.length || 1} pass{(passes?.length || 1) > 1 ? 'es' : ''})</span>
        <span className="ml-auto">{formatTime(log.planning_time_sec)}</span>
      </div>

      {passes && passes.length > 0 ? (
        <div className="space-y-2">
          {passes.map((p, i) => (
            <LlmPassView key={i} pass={p} index={i} />
          ))}
        </div>
      ) : (
        /* Fallback: show flat log (backward compat) */
        <LlmPassView pass={{
          pass: 'planning',
          system_prompt: log.system_prompt || '',
          response_text: log.response_text || '',
          thinking_text: log.thinking_text,
        }} index={0} />
      )}
    </div>
  )
}

function ClipCard({ clip, pipeline, busy = false, onTag, onRerunImage, onRerunVideo, onSavePrompt, onTakeSelected }: {
  clip: ReviewablePipelineClip
  pipeline: SavedPipelineState
  busy?: boolean
  onTag: (tag: 'good' | 'needs_work' | null) => void
  onRerunImage: (clipIndex: number, prompt?: string) => Promise<void>
  onRerunVideo: (clipIndex: number, prompt?: string) => Promise<void>
  onSavePrompt: (clipIndex: number, update: { image_prompt?: string; video_prompt?: string; window_prompts?: string[] }) => Promise<void>
  onTakeSelected: () => Promise<void>
}) {
  const [expandImage, setExpandImage] = useState(false)
  const [expandVideo, setExpandVideo] = useState(false)
  const [showPolish, setShowPolish] = useState(false)
  const [editingImage, setEditingImage] = useState(false)
  const [editingVideo, setEditingVideo] = useState(false)
  const [editWindowPrompts, setEditWindowPrompts] = useState<string[]>(clip.window_prompts || [])
  const [editImagePrompt, setEditImagePrompt] = useState(clip.image_prompt || '')
  const [editVideoPrompt, setEditVideoPrompt] = useState(clip.video_prompt || '')
  const [savingPrompt, setSavingPrompt] = useState(false)
  const [saveError, setSaveError] = useState('')
  const draftFinalPipeline = pipeline as DraftFinalPipeline
  const [fixNote, setFixNote] = useState('')
  const [fixing, setFixing] = useState(false)
  const [fixError, setFixError] = useState('')
  // The correction is a short conversation, bounded per shot: the director's
  // notes and what the assistant answered, so a second note builds on the first
  // instead of starting again from the same symptom.
  const [fixTurns, setFixTurns] = useState<RevisionTurn[]>([])
  const [fixAnswer, setFixAnswer] = useState<RevisionAnswer | null>(null)
  const [showPromptWindow, setShowPromptWindow] = useState(false)
  // The window shows either the prompt itself or the comparison between it and a
  // proposal: a correction is accepted on what it changes, which only reading the
  // two side by side answers.
  const [promptView, setPromptView] = useState<'editor' | 'diff'>('editor')
  const [showPreview, setShowPreview] = useState(false)
  const [loadPreview, setLoadPreview] = useState(false)
  // Which regeneration this shot has in flight. A clip rerun takes minutes, and
  // the button used to give no sign at all that the click had landed, so it was
  // impossible to tell a slow job from a missed click.
  const [rerunKind, setRerunKind] = useState<null | 'image' | 'video'>(null)
  const [rerunNotice, setRerunNotice] = useState('')
  const requiresShotImage = !pipeline.shot_image_policy
    || pipeline.shot_image_policy === 'generate'

  // hasPolish is true if ANY of the four polish snapshots was captured.
  // Window-prompt polish only fires for ≥21s shots; keyframe polish
  // only fires when the planner emitted keyframes. video_prompt polish
  // is skipped entirely on windowed shots (its content is unused at
  // generation time), so for those we rely on the window snapshot.
  const hasPolish =
    clip.image_prompt_pre_polish != null ||
    clip.video_prompt_pre_polish != null ||
    (clip.window_prompts_pre_polish != null && clip.window_prompts_pre_polish.length > 0) ||
    (clip.keyframe_prompts_pre_polish != null && clip.keyframe_prompts_pre_polish.length > 0)
  const tagColor = clip.tag === 'good' ? 'border-green-500 bg-green-500/5'
    : clip.tag === 'needs_work' ? 'border-amber-500 bg-amber-500/5'
    : 'border-border'

  const windowed = (clip.window_prompts?.length || 0) > 1

  const runRerun = async (kind: 'image' | 'video', prompt?: string) => {
    // One job at a time per shot: a second click while this is running is a
    // duplicate job, and the backend would only answer 409 Conflict anyway.
    if (rerunKind) return
    setRerunKind(kind)
    setRerunNotice('')
    try {
      if (kind === 'image') {
        await onRerunImage(clip.index, prompt)
        setRerunNotice('Start image regenerated.')
      } else {
        await onRerunVideo(clip.index, prompt)
        setRerunNotice('Shot regenerated.')
      }
    } catch (error) {
      setRerunNotice(error instanceof Error ? error.message : String(error))
    } finally {
      setRerunKind(null)
    }
  }

  const beginPromptEdit = () => {
    setEditingVideo(true)
    setEditVideoPrompt(clip.video_prompt || '')
    setEditWindowPrompts(clip.window_prompts || [])
    setSaveError('')
  }

  const cancelPromptEdit = () => {
    setShowPromptWindow(false)
    setEditingVideo(false)
    setEditVideoPrompt(clip.video_prompt || '')
    setEditWindowPrompts(clip.window_prompts || [])
    setSaveError('')
  }

  // One save path for the inline box and the floating window, so the two can
  // never disagree about which text is authoritative.
  const saveVideoPrompt = async () => {
    setSavingPrompt(true)
    setSaveError('')
    try {
      await onSavePrompt(
        clip.index,
        windowed
          ? { window_prompts: editWindowPrompts }
          : { video_prompt: editVideoPrompt },
      )
      setEditingVideo(false)
      setShowPromptWindow(false)
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : String(error))
    } finally {
      setSavingPrompt(false)
    }
  }

  const runFixWithAi = async (overrideNote?: string) => {
    // An option chip is an instruction: it arrives here instead of landing in the
    // note box, where it used to sit waiting for a second click and looked like a
    // button that did nothing.
    const note = (overrideNote ?? fixNote).trim()
    if (!note) return
    setFixing(true)
    setFixError('')
    try {
      const windowed = (clip.window_prompts?.length || 0) > 1
      const answer = await reviseClipPrompt(
        pipeline.pipeline_id,
        clip.index,
        note,
        windowed
          ? clip.window_prompts.join('\n')
          : (editingVideo ? editVideoPrompt : clip.video_prompt || ''),
        fixTurns,
      )
      setFixAnswer(answer)
      // Keep the turn so the next note builds on what the assistant already said
      // instead of describing the same symptom from scratch.
      setFixTurns([
        ...fixTurns,
        { role: 'director', text: note },
        { role: 'assistant', text: answer.analysis || answer.question || 'No change suggested.' },
      ])
      setFixNote('')
      // Shown, never swapped in: the editor keeps the prompt that is on disk until
      // "Aplicar al editor" puts the proposal into it, so a rejected suggestion can
      // no longer look like an applied one.
      if (answer.rewritten && answer.video_prompt) {
        setShowPromptWindow(true)
        setPromptView('diff')
      }
    } catch (error) {
      setFixError(error instanceof Error ? error.message : String(error))
    } finally {
      setFixing(false)
    }
  }

  // The proposal waiting for a decision, if the gate accepted one.
  const proposalPrompt = fixAnswer?.rewritten && fixAnswer.video_prompt
    ? fixAnswer.video_prompt
    : ''

  const applyProposal = () => {
    if (!proposalPrompt) return
    setEditVideoPrompt(proposalPrompt)
    setEditingVideo(true)
  }

  return (
    <div className={`rounded-lg border-2 ${tagColor} bg-bg-secondary overflow-hidden`}>
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-bg-tertiary border-b border-border">
        <span className="text-xs font-medium text-text-primary">
          Shot {clip.index + 1}
          {(clip.planned_clip as unknown as Record<string, unknown> | null)?.duration_sec ? (
            <span className="text-text-muted font-normal ml-1">({Math.round((clip.planned_clip as unknown as Record<string, unknown>).duration_sec as number)}s)</span>
          ) : null}
          {clip.window_count > 1 && (
            <span className="text-chip-purple font-normal ml-1 text-[9px]">{clip.window_count}W</span>
          )}
        </span>
        <div className="flex items-center gap-1">
          {clip.image_gen_time_sec && (
            <span className="text-[9px] text-text-muted"><ImageIcon size={8} className="inline" /> {formatTime(clip.image_gen_time_sec)}</span>
          )}
          {clip.video_gen_time_sec && (
            <span className="text-[9px] text-text-muted ml-1"><Play size={8} className="inline" /> {formatTime(clip.video_gen_time_sec)}</span>
          )}
          {rerunKind && (
            <span className="ml-1.5 flex items-center gap-1 rounded bg-accent-blue/15 px-1 py-0.5 text-[9px] text-accent-blue whitespace-nowrap">
              <Loader2 size={9} className="animate-spin" />
              {rerunKind === 'image' ? 'Regenerating image…' : 'Regenerating shot…'}
            </span>
          )}
          {!rerunKind && rerunNotice && (
            <span
              role="status"
              className={`ml-1.5 rounded px-1 py-0.5 text-[9px] whitespace-nowrap ${/regenerated/i.test(rerunNotice) ? 'bg-green-500/15 text-indicator-success' : 'bg-amber-500/15 text-indicator-warning'}`}
              title={rerunNotice}
            >
              {rerunNotice.length > 42 ? `${rerunNotice.slice(0, 42)}…` : rerunNotice}
            </span>
          )}
          {clip.video_filename && (
            <button onClick={() => { setLoadPreview(false); setShowPreview(true) }}
              className="ml-1 p-0.5 rounded text-text-muted hover:text-accent-blue transition-colors"
              title={`Watch shot ${clip.index + 1} to be sure you are editing the right one`}
              aria-label={`Watch shot ${clip.index + 1}`}>
              <Play size={12} />
            </button>
          )}
          {/* Tag buttons */}
          <button onClick={() => onTag(clip.tag === 'good' ? null : 'good')}
            disabled={busy}
            className={`ml-2 p-0.5 rounded disabled:opacity-40 ${clip.tag === 'good' ? 'bg-green-500 text-white' : 'text-text-muted hover:text-indicator-success'}`}
            title="Mark as good">
            <Check size={12} />
          </button>
          <button onClick={() => onTag(clip.tag === 'needs_work' ? null : 'needs_work')}
            disabled={busy}
            className={`p-0.5 rounded disabled:opacity-40 ${clip.tag === 'needs_work' ? 'bg-amber-500 text-white' : 'text-text-muted hover:text-indicator-warning'}`}
            title="Needs work">
            <AlertTriangle size={12} />
          </button>
        </div>
      </div>

      <div className="p-2 space-y-2">
        <ShotReviewPanel
          pipelineId={pipeline.pipeline_id}
          clip={clip}
          pipeline={draftFinalPipeline}
          busy={busy}
          onTakeSelected={onTakeSelected}
        />
        {/* Image section */}
        <div className="flex gap-2">
          {/* Thumbnail */}
          {/* A grid of 150 shots must not mount 150 video elements: that is
              what had the browser holding tens of GB of media buffers. A card
              shows its start image when there is one, and otherwise says where
              the footage is and lets the user watch it on demand. */}
          <div className="relative w-20 h-20 shrink-0 rounded overflow-hidden bg-bg-tertiary border border-border">
            {clip.start_image_filename ? (
              <img src={getFileUrl(clip.start_image_filename, pipeline.workspace)} alt={`Shot ${clip.index + 1}`}
                className="w-full h-full object-cover" loading="lazy" />
            ) : clip.video_filename ? (
              <video
                src={`${getFileUrl(clip.video_filename, pipeline.workspace)}#t=0.1`}
                className="w-full h-full object-cover"
                muted
                playsInline
                preload="metadata"
                aria-label={`Shot ${clip.index + 1} video preview`}
              />
            ) : (
              <div className="w-full h-full flex flex-col items-center justify-center gap-0.5 text-text-muted">
                <ImageIcon size={16} />
                <span className="text-[7px] text-center leading-tight px-0.5">
                  {clip.video_filename ? 'clip ready\npress ▶' : 'no visual yet'}
                </span>
              </div>
            )}
            {/* Always offer a way to watch the shot: a start image alone does
                not prove which clip is about to be edited. */}
            {clip.video_filename && (
              <button onClick={() => { setLoadPreview(false); setShowPreview(true) }}
                className="absolute bottom-0 right-0 m-0.5 rounded bg-black/65 text-white p-0.5 hover:bg-black/85 transition-colors"
                title={`Review shot ${clip.index + 1} before editing it`}
                aria-label={`Review shot ${clip.index + 1}`}>
                <Play size={10} />
              </button>
            )}
          </div>
          {/* Image prompt */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center justify-between mb-0.5">
              <span className="text-[9px] text-text-muted uppercase tracking-wider">
                {requiresShotImage ? 'Image Prompt' : 'Planned Visual'}
              </span>
              {requiresShotImage && <div className="flex items-center gap-1">
                <button disabled={editingImage || savingPrompt} onClick={() => { setEditingImage(true); setEditImagePrompt(clip.image_prompt || ''); setSaveError('') }}
                  className={`p-0.5 rounded transition-colors disabled:cursor-default ${editingImage ? 'text-accent-blue' : 'text-text-muted hover:text-text-secondary'}`}
                  title="Edit prompt">
                  <Pencil size={9} />
                </button>
                {editingImage && <>
                  <button disabled={savingPrompt} title="Save image prompt" onClick={async () => {
                    setSavingPrompt(true); setSaveError('')
                    try { await onSavePrompt(clip.index, { image_prompt: editImagePrompt }); setEditingImage(false) }
                    catch (error) { setSaveError(error instanceof Error ? error.message : String(error)) }
                    finally { setSavingPrompt(false) }
                  }} className="p-0.5 rounded text-indicator-success disabled:opacity-40"><Check size={10} /></button>
                  <button disabled={savingPrompt} title="Cancel image prompt edit" onClick={() => { setEditingImage(false); setEditImagePrompt(clip.image_prompt || ''); setSaveError('') }} className="p-0.5 rounded text-text-muted disabled:opacity-40"><X size={10} /></button>
                </>}
                <button onClick={() => runRerun('image', editingImage ? editImagePrompt : undefined)}
                  disabled={busy || rerunKind !== null}
                  className={`p-0.5 rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${rerunKind === 'image' ? 'text-accent-blue' : 'text-text-muted hover:text-accent-blue'}`}
                  title={rerunKind ? 'A regeneration for this shot is already running' : 'Re-generate start image'}>
                  {rerunKind === 'image' ? <Loader2 size={10} className="animate-spin" /> : <Camera size={10} />}
                </button>
              </div>}
            </div>
            {editingImage ? (
              <textarea
                value={editImagePrompt}
                onChange={e => setEditImagePrompt(e.target.value)}
                className="w-full bg-bg-tertiary border border-accent-blue rounded px-1.5 py-1 text-[10px] text-text-primary resize-none focus:outline-none"
                rows={3}
              />
            ) : (
              <p className={`text-[10px] text-text-secondary ${expandImage ? '' : 'line-clamp-3'} cursor-pointer`}
                onClick={() => setExpandImage(!expandImage)}>
                {clip.image_prompt || <span className="italic text-text-muted">No image prompt</span>}
              </p>
            )}
          </div>
        </div>

        {/* Keyframes */}
        {(clip.keyframe_prompts?.length > 0 || clip.keyframe_filenames?.length > 0) && (
          <div>
            <div className="text-[9px] text-text-muted uppercase tracking-wider mb-0.5">
              Keyframes ({clip.keyframe_prompts?.length || clip.keyframe_filenames?.length || 0})
            </div>
            <div className="flex gap-1.5 overflow-x-auto">
              {clip.keyframe_filenames?.map((kf, ki) => (
                <div key={ki} className="shrink-0">
                  <img src={getFileUrl(kf, pipeline.workspace)} alt={`KF ${ki + 1}`}
                    className="w-14 h-14 object-cover rounded border border-border" loading="lazy" />
                  {clip.keyframe_prompts?.[ki] && (
                    <p className="text-[8px] text-text-muted mt-0.5 w-14 truncate" title={safeStr(clip.keyframe_prompts[ki])}>
                      {safeStr(clip.keyframe_prompts[ki])}
                    </p>
                  )}
                </div>
              ))}
              {/* Show prompts without images if more prompts than files */}
              {clip.keyframe_prompts?.slice(clip.keyframe_filenames?.length || 0).map((kp, ki) => (
                <div key={`p${ki}`} className="shrink-0 w-14 h-14 rounded border border-dashed border-border flex items-center justify-center">
                  <p className="text-[7px] text-text-muted p-1 line-clamp-3">{safeStr(kp)}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Video prompt */}
        <div>
          <div className="flex items-center justify-between mb-0.5">
            <span className="text-[9px] text-text-muted uppercase tracking-wider">
              Video Prompt{clip.window_prompts?.length > 1 ? ` (${clip.window_prompts.length} windows)` : ''}
            </span>
            <div className="flex items-center gap-1">
              <button disabled={editingVideo || savingPrompt} onClick={beginPromptEdit}
                className={`p-0.5 rounded transition-colors disabled:cursor-default ${editingVideo ? 'text-accent-blue' : 'text-text-muted hover:text-text-secondary'}`}
                title="Quick edit in the card">
                <Pencil size={9} />
              </button>
              {/* A compiled prompt runs to thousands of characters, which the
                  card cannot show. This opens it in a window the user sizes. */}
              <button disabled={savingPrompt} onClick={() => { beginPromptEdit(); setShowPromptWindow(true) }}
                className="p-0.5 rounded text-text-muted hover:text-accent-blue transition-colors disabled:opacity-40"
                title="Edit in a window you can move and resize">
                <Maximize2 size={9} />
              </button>
              {editingVideo && <>
                <button disabled={savingPrompt} title="Save video prompt" onClick={saveVideoPrompt}
                  className="p-0.5 rounded text-indicator-success disabled:opacity-40"><Check size={10} /></button>
                <button disabled={savingPrompt} title="Cancel video prompt edit" onClick={cancelPromptEdit}
                  className="p-0.5 rounded text-text-muted disabled:opacity-40"><X size={10} /></button>
              </>}
              <button onClick={() => {
                if (editingVideo && editWindowPrompts.length > 1) {
                  void runRerun('video', editWindowPrompts.join('\n'))
                } else {
                  void runRerun('video', editingVideo ? editVideoPrompt : undefined)
                }
              }}
                disabled={busy || rerunKind !== null || (requiresShotImage && !clip.start_image_filename)}
                className={`p-0.5 rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${rerunKind === 'video' ? 'text-indicator-success' : 'text-text-muted hover:text-indicator-success'}`}
                title={rerunKind === 'video'
                  ? 'Regenerating this shot — one job at a time'
                  : rerunKind
                    ? 'Another regeneration for this shot is already running'
                    : busy
                      ? 'Wait for pipeline repair to finish'
                      : requiresShotImage && !clip.start_image_filename
                        ? 'Generate the start image first'
                        : 'Re-generate video clip'}>
                {rerunKind === 'video' ? <Loader2 size={10} className="animate-spin" /> : <Film size={10} />}
              </button>
            </div>
          </div>
          {editingVideo ? (
            clip.window_prompts?.length > 1 ? (
              <div className="space-y-1.5">
                {editWindowPrompts.map((wp, wi) => (
                  <div key={wi}>
                    <div className="text-[8px] text-text-muted mb-0.5">Window {wi + 1}</div>
                    <textarea
                      value={wp}
                      onChange={e => {
                        const updated = [...editWindowPrompts]
                        updated[wi] = e.target.value
                        setEditWindowPrompts(updated)
                      }}
                      className="w-full bg-bg-tertiary border border-accent-blue/50 rounded px-1.5 py-1 text-[10px] text-text-primary resize-none focus:outline-none focus:border-accent-blue"
                      rows={3}
                    />
                  </div>
                ))}
              </div>
            ) : (
              <textarea
                value={editVideoPrompt}
                onChange={e => setEditVideoPrompt(e.target.value)}
                className="w-full bg-bg-tertiary border border-accent-blue rounded px-1.5 py-1 text-[10px] text-text-primary resize-none focus:outline-none"
                rows={4}
              />
            )
          ) : (
            clip.window_prompts?.length > 1 ? (
              <div className="space-y-0.5">
                {clip.window_prompts.map((wp, wi) => (
                  <p key={wi} className={`text-[10px] text-text-secondary pl-2 border-l-2 ${wi === 0 ? 'border-accent-blue/40' : 'border-border'} ${expandVideo ? '' : 'line-clamp-2'} cursor-pointer`}
                    onClick={() => setExpandVideo(!expandVideo)}>
                    <span className="text-[8px] text-text-muted mr-1">W{wi + 1}</span>
                    {safeStr(wp)}
                  </p>
                ))}
              </div>
            ) : (
              <p className={`text-[10px] text-text-secondary ${expandVideo ? '' : 'line-clamp-3'} cursor-pointer`}
                onClick={() => setExpandVideo(!expandVideo)}>
                {clip.video_prompt || <span className="italic text-text-muted">No video prompt</span>}
              </p>
            )
          )}
          {(() => {
            // Warned for the text on screen, whether it is being edited or only
            // read, so the message follows what the user is looking at.
            const warning = multipleShotWarning(editingVideo ? editVideoPrompt : clip.video_prompt || '')
            if (!warning) return null
            return (
              <p role="alert" className="mt-1 flex items-start gap-1 text-[9px] text-indicator-warning">
                <AlertTriangle size={9} className="mt-px shrink-0" />
                <span>{warning}</span>
              </p>
            )
          })()}
          {saveError && <p role="alert" className="mt-1 text-[9px] text-indicator-warning">{saveError}</p>}

          {/* A short correction conversation for this one shot. The assistant is
              handed the measurement of the saved shot, so it names the cause
              instead of guessing at a symptom it cannot see, and it may ask
              before rewriting. A rewrite reaches the editor only when it keeps
              every spoken line and carries a single shot. */}
          <div className="mt-1.5 rounded border border-border bg-bg-tertiary/60 p-1.5">
            <div className="flex items-center justify-between mb-1">
              <span className="text-[11px] text-text-muted uppercase tracking-wider">
                Correct this shot
              </span>
              {fixTurns.length > 0 && (
                <button
                  type="button"
                  onClick={() => { setFixTurns([]); setFixAnswer(null); setFixError('') }}
                  className="text-[11px] text-text-muted hover:text-text-primary transition-colors">
                  Start over
                </button>
              )}
            </div>
            {fixTurns.length > 0 && (
              <div className="mb-1 max-h-40 space-y-1 overflow-y-auto">
                {fixTurns.map((turn, i) => (
                  <p key={i} className={`text-[12px] leading-snug ${turn.role === 'director' ? 'text-text-secondary' : 'text-accent-blue/90'}`}>
                    <span className="text-text-muted">{turn.role === 'director' ? 'You: ' : 'AI: '}</span>
                    {turn.text}
                  </p>
                ))}
              </div>
            )}
            <textarea
              value={fixNote}
              onChange={e => setFixNote(e.target.value)}
              placeholder="What is wrong? e.g. the man appears twice; they should sit facing each other"
              className="w-full bg-bg-tertiary border border-border rounded px-2 py-1.5 text-[13px] text-text-primary resize-none focus:outline-none focus:border-accent-blue"
              rows={2}
              disabled={fixing}
            />
            <div className="flex items-center gap-2 mt-1.5">
              <button
                onClick={() => void runFixWithAi()}
                disabled={fixing || !fixNote.trim() || busy}
                className="flex items-center gap-1 px-2 py-1 rounded text-[12px] bg-accent-blue/15 text-accent-blue hover:bg-accent-blue/25 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                title="The assistant reads the saved shot, says what causes the problem, asks when the note is missing an intent, and rewrites only what survives the checks">
                <Sparkles size={12} />
                {fixing ? 'Reading shot…' : fixTurns.length > 0 ? 'Send' : 'Fix with AI'}
              </button>
              {fixing && (
                <span className="text-[12px] text-text-muted">Reading shot {clip.index + 1}…</span>
              )}
            </div>
            {!!fixAnswer?.diagnosis?.findings?.length && (
              <details className="mt-1.5">
                <summary className="text-[12px] text-text-muted cursor-pointer">
                  What the measurement found
                </summary>
                <ul className="mt-1 space-y-0.5">
                  {fixAnswer.diagnosis.findings.map((finding, i) => (
                    <li key={i} className="text-[12px] leading-snug text-text-muted">· {finding}</li>
                  ))}
                </ul>
              </details>
            )}
            {/* The assistant's own reading, before anything it proposes. It was used to
                build the next turn and never shown, so the window went from a note to an
                error or to a diff with no account of what it found: "no se que esta
                haciendo o que cambio quiere hacer". */}
            {fixAnswer?.analysis && (
              <div className="mt-1.5 rounded border border-border bg-bg-tertiary p-1.5">
                <p className="text-[11px] text-text-muted uppercase tracking-wider">
                  Su lectura del shot
                </p>
                <p className="mt-0.5 text-[13px] leading-snug text-text-secondary whitespace-pre-wrap">
                  {fixAnswer.analysis}
                </p>
              </div>
            )}
            {fixAnswer?.question && (
              <p className="mt-1.5 rounded border border-accent-blue/30 bg-bg-tertiary p-1.5 text-[13px] leading-snug text-accent-blue">
                {fixAnswer.question}
              </p>
            )}
            {/* The assistant is asked for choices, not for a verdict: a note that
                cannot be answered inside the prompt has to come back as something
                the director can pick and send. */}
            {!!fixAnswer?.options?.length && (
              <div className="mt-1.5 rounded border border-accent-blue/30 bg-bg-tertiary p-1.5">
                <p className="text-[12px] text-accent-blue">Elige una: el asistente la aplica como la siguiente instrucción.</p>
                <div className="mt-1 flex flex-col gap-1">
                  {splitRevisionOptions(fixAnswer.options).map((option, i) => (
                    <button
                      key={i}
                      type="button"
                      onClick={() => { setFixNote(option); void runFixWithAi(option) }}
                      disabled={fixing}
                      title="Enviar esta opción al asistente"
                      className="text-left rounded px-1.5 py-1 text-[13px] leading-snug text-accent-blue/90 hover:bg-accent-blue/15 transition-colors disabled:opacity-40"
                    >{i + 1}. {option}</button>
                  ))}
                </div>
              </div>
            )}
            {fixAnswer?.note && (
              <p className="mt-1.5 text-[12px] leading-snug text-text-muted">{fixAnswer.note}</p>
            )}
            {/* Damage the contract cannot see: the prose is free, so a word mangled while
                re-typing (measured: "Vestuario" -> "Vestología", "tightens" -> "tights")
                would otherwise only be findable by reading six thousand characters. */}
            {!!fixAnswer?.warnings?.length && (
              <ul className="mt-1.5 space-y-0.5">
                {fixAnswer.warnings.slice(0, 4).map((warning, i) => (
                  <li key={i} className="text-[12px] leading-snug text-indicator-warning">
                    ⚠ {warning}
                  </li>
                ))}
              </ul>
            )}
            {!!fixAnswer?.errors?.length && (
              <div className="mt-1.5 rounded border border-indicator-warning/40 bg-bg-tertiary p-1.5">
                <p className="text-[12px] text-indicator-warning">
                  The rewrite was refused, so the prompt is unchanged:
                </p>
                <ul className="mt-1 space-y-0.5">
                  {fixAnswer.errors.map((problem, i) => (
                    <li key={i} className="text-[12px] leading-snug text-indicator-warning/90">· {problem}</li>
                  ))}
                </ul>
              </div>
            )}
            {fixAnswer?.rewritten && (
              <p className="mt-1.5 text-[12px] text-indicator-success">
                The rewrite is in the editor above — review it, then save.
              </p>
            )}
            {fixError && <p role="alert" className="mt-1.5 text-[12px] text-indicator-warning">{fixError}</p>}
          </div>

          {/* The prompt, in a window that can be moved and resized. Kept in
              sync with the inline box through the same edit state. */}
          {showPromptWindow && (
            <FloatingPanel
              storageKey={`prompt-${pipeline.pipeline_id}-${clip.index}`}
              title={`Shot ${clip.index + 1} — video prompt`}
              subtitle={windowed
                ? `${clip.window_prompts.length} window prompts, one per generation`
                : `${editVideoPrompt.length} characters`}
              onClose={() => setShowPromptWindow(false)}
              fill
              resizableFont
              search
              footer={<>
                <button onClick={() => setShowPromptWindow(false)}
                  className="px-2 py-1 rounded text-[12px] text-text-muted hover:text-text-primary transition-colors">Close</button>
                <button onClick={saveVideoPrompt} disabled={savingPrompt}
                  className="px-2 py-1 rounded text-[12px] bg-accent-blue/15 text-accent-blue hover:bg-accent-blue/25 transition-colors disabled:opacity-40">
                  {savingPrompt ? 'Saving…' : 'Save prompt'}
                </button>
              </>}
            >
              {promptView === 'diff' && proposalPrompt ? (
                <PromptDiffView
                  before={fixAnswer?.clip_prompt
                    || (windowed ? editWindowPrompts.join('\n') : editVideoPrompt)}
                  after={fixAnswer?.clip_proposed || proposalPrompt}
                  scope={fixAnswer?.clip_prompt
                    ? `${fixAnswer.clip_prompt.length} caracteres son de este clip; el resto es el texto del proyecto, que no cambia`
                    : undefined}
                  persistKey={`prompt-${pipeline.pipeline_id}-${clip.index}`}
                  applied={!windowed && editVideoPrompt === proposalPrompt}
                  canApply={!windowed}
                  note="Este clip usa varios prompts por ventana: aplica el cambio a mano en la ventana que corresponda."
                  onApply={applyProposal}
                  onBack={() => setPromptView('editor')}
                />
              ) : windowed ? (
                <div className="flex-1 min-h-0 overflow-auto space-y-2">
                  {editWindowPrompts.map((wp, wi) => (
                    <div key={wi}>
                      <div className="text-[12px] text-text-muted mb-1">Window {wi + 1}</div>
                      <textarea
                        value={wp}
                        onChange={e => {
                          const next = [...editWindowPrompts]
                          next[wi] = e.target.value
                          setEditWindowPrompts(next)
                        }}
                        rows={9}
                        className="w-full bg-bg-tertiary border border-border rounded px-2 py-1.5 leading-relaxed text-text-primary resize-y focus:outline-none focus:border-accent-blue"
                      />
                    </div>
                  ))}
                </div>
              ) : (
                <HighlightedTextarea
                  value={editVideoPrompt}
                  onChange={setEditVideoPrompt}
                  ariaLabel="Text of the video prompt"
                />
              )}
              {saveError && <p role="alert" className="mt-1.5 text-[12px] text-indicator-warning shrink-0">{saveError}</p>}

              {/* The assistant belongs in this window too: a compiled prompt is read
                  and corrected here, so asking must not mean closing the window. */}
              <div className="shrink-0 mt-1.5 border-t border-border pt-1.5 flex items-center gap-2 flex-wrap">
                <span className="text-[12px] text-text-muted shrink-0">Asistente IA</span>
                <input
                  value={fixNote}
                  onChange={e => setFixNote(e.target.value)}
                  onKeyDown={e => {
                    if (e.key !== 'Enter' || e.shiftKey) return
                    e.preventDefault()
                    if (!fixing && fixNote.trim()) void runFixWithAi()
                  }}
                  placeholder="Qué corregir… (ej. «la boca no se mueve en la línea de Valeria»)"
                  aria-label="Nota para el asistente"
                  className="flex-1 min-w-[200px] bg-bg-tertiary border border-border rounded px-2 py-1.5 text-[13px] text-text-primary focus:outline-none focus:border-accent-blue"
                />
                <button
                  onClick={() => void runFixWithAi()}
                  disabled={fixing || !fixNote.trim()}
                  className="px-2 py-1 rounded text-[12px] bg-accent-blue/15 text-accent-blue hover:bg-accent-blue/25 transition-colors disabled:opacity-40"
                >{fixing ? 'Consultando…' : 'Corregir con IA'}</button>
                {proposalPrompt && (
                  <button
                    onClick={() => setPromptView(promptView === 'diff' ? 'editor' : 'diff')}
                    className="px-2 py-1 rounded text-[12px] text-text-muted hover:text-text-primary transition-colors"
                  >{promptView === 'diff' ? 'Ver editor' : 'Ver comparación'}</button>
                )}
              </div>
              {fixError && <p role="alert" className="text-[12px] text-indicator-warning shrink-0">{fixError}</p>}
              {/* The same reading, in the window: what the assistant found and why, before
                  the comparison it proposes. */}
              {fixAnswer?.analysis && (
                <div className="shrink-0 max-h-40 overflow-auto rounded border border-border bg-bg-tertiary p-1.5">
                  <p className="text-[11px] text-text-muted uppercase tracking-wider">Su lectura del shot</p>
                  <p className="mt-0.5 text-[13px] leading-snug text-text-secondary whitespace-pre-wrap">{fixAnswer.analysis}</p>
                </div>
              )}
              {/* A refusal carries no proposal, so the window has to say why: an
                  answer that changes nothing used to look like nothing happened. */}
              {fixAnswer && !proposalPrompt && (
                <p className="text-[13px] leading-snug text-text-muted shrink-0">
                  {fixAnswer.question
                    ? `El asistente pregunta: ${fixAnswer.question}`
                    : fixAnswer.note
                      ? fixAnswer.note
                      : fixAnswer.errors?.length
                        ? `La propuesta fue rechazada: ${fixAnswer.errors[0]}`
                        : 'El asistente no propuso ningún cambio.'}
                </p>
              )}
              {!!fixAnswer?.options?.length && (
                <div className="shrink-0 flex items-center gap-1.5 flex-wrap">
                  <span className="text-[12px] text-text-muted shrink-0">Opciones:</span>
                  {splitRevisionOptions(fixAnswer.options).map((option, i) => (
                    <button
                      key={i}
                      type="button"
                      onClick={() => { setFixNote(option); void runFixWithAi(option) }}
                      disabled={fixing}
                      title="Enviar esta opción al asistente"
                      className="max-w-full text-left px-2 py-1 rounded text-[13px] leading-snug bg-accent-blue/10 text-accent-blue hover:bg-accent-blue/25 transition-colors disabled:opacity-40"
                    >{i + 1}. {option}</button>
                  ))}
                </div>
              )}
              {!!fixAnswer?.warnings?.length && (
                <ul className="shrink-0 text-[12px] leading-snug text-indicator-warning space-y-0.5">
                  {fixAnswer.warnings.slice(0, 4).map((warning, i) => (
                    <li key={i}>⚠ {warning}</li>
                  ))}
                </ul>
              )}
              {!!fixAnswer?.diagnosis?.findings?.length && (
                <ul className="text-[12px] leading-snug text-text-muted shrink-0 space-y-0.5 max-h-32 overflow-auto">
                  {fixAnswer.diagnosis.findings.slice(0, 4).map((finding, i) => (
                    <li key={i}>• {finding}</li>
                  ))}
                </ul>
              )}
              {proposalPrompt && promptView === 'editor' && (
                <p className="text-[12px] text-text-muted shrink-0">Hay una propuesta esperando: pulsa «Ver comparación».</p>
              )}
            </FloatingPanel>
          )}

          {/* Watch the shot before touching it. Nothing is fetched until the
              user asks: opening this window must never start a media load,
              because that is what froze the page. */}
          {showPreview && (
            <FloatingPanel
              storageKey={`clip-${pipeline.pipeline_id}-${clip.index}`}
              title={`Shot ${clip.index + 1}`}
              subtitle={clip.video_filename || 'no clip rendered yet'}
              onClose={() => { setShowPreview(false); setLoadPreview(false) }}
              initialWidth={880}
              initialHeight={580}
              fill
            >
              {clip.video_filename ? (
                loadPreview ? (
                  <video
                    src={getFileUrl(clip.video_filename, pipeline.workspace)}
                    className="w-full flex-1 min-h-0 bg-black rounded"
                    controls
                    preload="metadata"
                    playsInline
                    aria-label={`Shot ${clip.index + 1}`}
                  />
                ) : (
                  <div className="flex-1 min-h-0 flex flex-col items-center justify-center gap-2 text-center px-4">
                    <Film size={28} className="text-text-muted" />
                    <p className="text-[11px] text-text-secondary break-all">{clip.video_filename}</p>
                    <p className="text-[10px] text-text-muted">
                      The file loads only when you ask, so opening this window cannot stall the app.
                    </p>
                    <button onClick={() => setLoadPreview(true)}
                      className="flex items-center gap-1 px-2.5 py-1 rounded text-[11px] bg-accent-blue/15 text-accent-blue hover:bg-accent-blue/25 transition-colors">
                      <Play size={12} /> Load video
                    </button>
                  </div>
                )
              ) : clip.start_image_filename ? (
                <div className="flex-1 min-h-0 flex flex-col gap-2">
                  <img src={getFileUrl(clip.start_image_filename, pipeline.workspace)}
                    className="flex-1 min-h-0 w-full object-contain bg-black rounded"
                    alt={`Shot ${clip.index + 1} planned start image`} />
                  <p className="text-[10px] text-text-muted shrink-0">
                    No clip rendered yet — this is the planned start image for this shot.
                  </p>
                </div>
              ) : (
                <p className="text-[11px] text-text-muted italic">
                  This shot has no rendered clip and no start image yet, so there is nothing to show.
                </p>
              )}
            </FloatingPanel>
          )}
        </div>

        {/* Prompt polish diff */}
        {hasPolish && (
          <div>
            <button onClick={() => setShowPolish(!showPolish)}
              className="flex items-center gap-1 text-[9px] text-accent-blue hover:underline">
              <Sparkles size={8} />
              {showPolish ? 'Hide' : 'Show'} prompt polish diff
            </button>
            {showPolish && (() => {
              // Compute change flags so the "no changes from polish"
              // message only shows when literally nothing was modified
              // by Pass 3 (across image, video, all windows, all keyframes).
              const imageChanged = !!(clip.image_prompt_pre_polish && clip.image_prompt_pre_polish !== clip.image_prompt)
              const videoChanged = !!(clip.video_prompt_pre_polish && clip.video_prompt_pre_polish !== clip.video_prompt)
              const wpsPre = clip.window_prompts_pre_polish || []
              const wpsPost = clip.window_prompts || []
              const windowDiffs = wpsPre
                .map((pre, i) => ({ pre, post: wpsPost[i] || '', i }))
                .filter(d => d.pre && d.post && d.pre !== d.post)
              const kfsPre = clip.keyframe_prompts_pre_polish || []
              const kfsPost = clip.keyframe_prompts || []
              const keyframeDiffs = kfsPre
                .map((pre, i) => ({ pre, post: kfsPost[i] || '', i }))
                .filter(d => d.pre && d.post && d.pre !== d.post)
              const anyChange = imageChanged || videoChanged || windowDiffs.length > 0 || keyframeDiffs.length > 0
              return (
                <div className="mt-1 space-y-1.5 bg-bg-tertiary rounded p-2">
                  {imageChanged && (
                    <div>
                      <div className="text-[8px] text-text-muted uppercase">Image — Before Polish</div>
                      <p className="text-[9px] text-red-400/70 line-through">{clip.image_prompt_pre_polish}</p>
                      <div className="text-[8px] text-text-muted uppercase mt-0.5">After</div>
                      <p className="text-[9px] text-indicator-success/80">{clip.image_prompt}</p>
                    </div>
                  )}
                  {videoChanged && (
                    <div>
                      <div className="text-[8px] text-text-muted uppercase">Video — Before Polish</div>
                      <p className="text-[9px] text-red-400/70 line-through">{clip.video_prompt_pre_polish}</p>
                      <div className="text-[8px] text-text-muted uppercase mt-0.5">After</div>
                      <p className="text-[9px] text-indicator-success/80">{clip.video_prompt}</p>
                    </div>
                  )}
                  {windowDiffs.map(({ pre, post, i }) => (
                    <div key={`wp${i}`}>
                      <div className="text-[8px] text-text-muted uppercase">Window {i + 1} — Before Polish</div>
                      <p className="text-[9px] text-red-400/70 line-through">{pre}</p>
                      <div className="text-[8px] text-text-muted uppercase mt-0.5">After</div>
                      <p className="text-[9px] text-indicator-success/80">{post}</p>
                    </div>
                  ))}
                  {keyframeDiffs.map(({ pre, post, i }) => (
                    <div key={`kf${i}`}>
                      <div className="text-[8px] text-text-muted uppercase">Keyframe {i + 1} — Before Polish</div>
                      <p className="text-[9px] text-red-400/70 line-through">{pre}</p>
                      <div className="text-[8px] text-text-muted uppercase mt-0.5">After</div>
                      <p className="text-[9px] text-indicator-success/80">{post}</p>
                    </div>
                  ))}
                  {!anyChange && (
                    <p className="text-[9px] text-text-muted italic">No changes from polish</p>
                  )}
                </div>
              )
            })()}
          </div>
        )}
      </div>
    </div>
  )
}

export function DirectorDashboard() {
  const open = useStore(s => s.dashboardOpen)

  if (!open) return null

  return (
    <DashboardErrorBoundary>
      <DirectorDashboardInner />
    </DashboardErrorBoundary>
  )
}

function DirectorDashboardInner() {
  const setOpen = useStore(s => s.setDashboardOpen)
  const pipelineList = useStore(s => s.dashboardPipelineList)
  const selectedPipeline = useStore(s => s.dashboardSelectedPipeline)
  const loading = useStore(s => s.dashboardLoading)
  const loadPipeline = useStore(s => s.loadSavedPipeline)
  const tagClip = useStore(s => s.tagClip)
  const startPipelineRepair = useStore(s => s.startPipelineRepair)
  const cancelPipelineRepair = useStore(s => s.cancelPipelineRepair)
  const rerunClipImage = useStore(s => s.rerunClipImage)
  const rerunClipVideo = useStore(s => s.rerunClipVideo)
  const rejoinClips = useStore(s => s.rejoinPipelineClips)
  const resumePipeline = useStore(s => s.resumePipeline)
  const deletePipeline = useStore(s => s.deletePipeline)
  const loadDirectorFromPipeline = useStore(s => s.loadDirectorFromPipeline)
  const loadPipelineList = useStore(s => s.loadPipelineList)
  const [resuming, setResuming] = useState(false)
  const [modelOptions, setModelOptions] = useState<ModelOptions | null>(null)
  const [modelOptionsError, setModelOptionsError] = useState('')
  const [finalPreset, setFinalPreset] = useState('')
  const [finalizingPid, setFinalizingPid] = useState<string | null>(null)
  // Keyed by pipeline id — a bare boolean would let "arm on pipeline A,
  // switch to B, click once" delete B without a confirm.
  const [confirmDeletePid, setConfirmDeletePid] = useState<string | null>(null)
  const [deletingPipeline, setDeletingPipeline] = useState(false)
  const [repairStartingPid, setRepairStartingPid] = useState<string | null>(null)
  const [repairCancellingPid, setRepairCancellingPid] = useState<string | null>(null)
  const [regenErrors, setRegenErrors] = useState<Record<string, string>>({})
  const autoLoadAttemptedPid = useRef<string | null>(null)
  const selectedPid = selectedPipeline?.pipeline_id || null
  const selectedDraftFinal = selectedPipeline as DraftFinalPipeline | null
  const draftSizeLabel = selectedPipeline?.director_resolution_preset
    || safeStr(selectedPipeline?.video_params?.resolution)
    || 'size unavailable'
  const finalAspect = selectedPipeline?.director_aspect_ratio || 'auto'
  const finalPresetOptions = getFinalPresetOptions(modelOptions, finalAspect)
  const unapprovedShots = selectedPipeline?.clips.filter(clip => clip.tag !== 'good').length || 0
  const shouldPollFinal = selectedPipeline?.status === 'running'
    && selectedDraftFinal?.draft_final?.stage === 'final'
  const canShowFinalControls = !!selectedPipeline
    && selectedPipeline.status === 'completed'
    && !selectedPipeline.seamless
    && selectedDraftFinal?.draft_final?.stage !== 'final'
  const canCreateFinal = canShowFinalControls
    && unapprovedShots === 0
  const finalizing = finalizingPid === selectedPid
  const repairStarting = repairStartingPid === selectedPid
  const repairCancelling = repairCancellingPid === selectedPid
  const regenError = selectedPid ? regenErrors[selectedPid] || null : null
  const setRegenError = (message: string | null) => {
    const pid = selectedPid
    if (!pid) return
    setRegenErrors(current => {
      const next = { ...current }
      if (message) next[pid] = message
      else delete next[pid]
      return next
    })
  }

  useEffect(() => {
    setFinalPreset('')
    setModelOptions(null)
    setModelOptionsError('')
    const modelType = selectedPipeline?.video_model
    if (!modelType) return
    let active = true
    fetchModelOptions(modelType).then(options => {
      if (active) setModelOptions(options)
    }).catch(error => {
      if (active) setModelOptionsError(error instanceof Error ? error.message : String(error))
    })
    return () => { active = false }
  }, [selectedPipeline?.pipeline_id, selectedPipeline?.video_model])

  useEffect(() => {
    if (!selectedPid || !shouldPollFinal) return
    const pid = selectedPid
    const interval = window.setInterval(() => {
      void loadPipelineList()
      void loadPipeline(pid)
    }, 4000)
    return () => window.clearInterval(interval)
  }, [selectedPid, shouldPollFinal, loadPipelineList, loadPipeline])

  // Auto-load first pipeline when list loads
  useEffect(() => {
    if (selectedPipeline) {
      autoLoadAttemptedPid.current = null
      return
    }
    if (pipelineList.length > 0 && !selectedPipeline && !loading) {
      const active = pipelineList.find(p =>
        p.repair_status === 'queued'
        || p.repair_status === 'running'
        || p.repair_status === 'cancelling')
      const pid = (active || pipelineList[0]).id
      // A stale list entry (for example, a pipeline removed outside Maestro)
      // must not create an endless load/fail/render loop. Explicit selection
      // and closing/reopening the Dashboard still provide retry paths.
      if (autoLoadAttemptedPid.current === pid) return
      autoLoadAttemptedPid.current = pid
      void loadPipeline(pid)
    }
  }, [pipelineList, selectedPipeline, loading, loadPipeline])

  const goodCount = selectedPipeline?.clips.filter(c => c.tag === 'good').length || 0
  const needsWorkCount = selectedPipeline?.clips.filter(c => c.tag === 'needs_work').length || 0
  const totalClips = selectedPipeline?.clips.length || 0
  // Legacy projects predate this field and retain the original required-image
  // contract. New H3 prompt/direct-reference projects intentionally have no
  // image artifacts, so repair must judge only their videos.
  const requiresShotImages = !selectedPipeline?.shot_image_policy
    || selectedPipeline.shot_image_policy === 'generate'
  const missingImages = requiresShotImages
    ? selectedPipeline?.clips.filter(c => !c.start_image_filename).length || 0
    : 0
  // A video beside a missing/replaced start image is stale even if its file
  // still exists: it was generated from different visual conditioning.
  const missingVideos = selectedPipeline?.clips.filter(c =>
    !c.video_filename
    || c.video_stale
    || (requiresShotImages && !c.start_image_filename)).length || 0
  const incompleteClips = selectedPipeline?.clips.filter(c =>
    (requiresShotImages && !c.start_image_filename)
    || !c.video_filename
    || c.video_stale).length || 0
  const hasMissing = missingImages > 0 || missingVideos > 0
  const repair = selectedPipeline?.repair
  const repairActive = repair?.status === 'queued'
    || repair?.status === 'running'
    || repair?.status === 'cancelling'
  const repairRetryable = repair?.status === 'failed'
    || repair?.status === 'cancelled'
    || repair?.status === 'interrupted'
  const repairBusy = repairActive || repairStarting || repairCancelling
  const pipelineTerminal = !!selectedPipeline && [
    'completed', 'failed', 'crashed', 'cancelled',
  ].includes(selectedPipeline.status)
  const showRepairAction = hasMissing || repairActive || repairRetryable || pipelineTerminal

  const generateMissing = async () => {
    if (!selectedPipeline) return
    const pid = selectedPipeline.pipeline_id
    setRegenError(null)
    setRepairStartingPid(pid)
    try {
      await startPipelineRepair(pid)
    } catch (e) {
      setRegenError(String(e instanceof Error ? e.message : e))
    } finally {
      setRepairStartingPid(current => current === pid ? null : current)
    }
  }

  const createFinal = async () => {
    if (!selectedPipeline) return
    if (unapprovedShots > 0) {
      setRegenError(`Mark all shots Good before creating a Final (${unapprovedShots} still need approval).`)
      return
    }
    if (!finalPreset) {
      setRegenError('Choose an explicit Final size before creating the Final.')
      return
    }
    const sourcePid = selectedPipeline.pipeline_id
    setRegenError(null)
    setFinalizingPid(sourcePid)
    try {
      const result = await createDirectorFinal(sourcePid, finalPreset)
      await loadPipelineList()
      await loadPipeline(result.pipeline_id)
    } catch (error) {
      setRegenError(error instanceof Error ? error.message : String(error))
    } finally {
      setFinalizingPid(current => current === sourcePid ? null : current)
    }
  }

  return (
    <div className="fixed inset-0 z-[60] flex flex-col bg-bg-primary">
      {/* Header */}
      <div className="px-4 py-3 border-b border-border flex flex-wrap items-center gap-2 shrink-0">
        <h1 className="text-sm font-semibold text-text-primary shrink-0">Dashboard</h1>

        {/* Pipeline selector */}
        <select
          value={selectedPipeline?.pipeline_id || ''}
          onChange={e => { if (e.target.value) loadPipeline(e.target.value) }}
          className="flex-1 min-w-0 max-w-md bg-bg-tertiary border border-border rounded-lg px-3 py-1.5 text-xs text-text-primary focus:outline-none focus:border-accent-blue truncate"
        >
          <option value="">Select pipeline...</option>
          {pipelineList.map(p => (
            <option key={p.id} value={p.id}>
              {p.repair_status ? `[repair: ${p.repair_status}] ` : ''}
              {formatDate(p.created_at)} — {p.pipeline_type} ({p.clip_count} clips) [{p.status}]
              {p.scene_description ? ` — ${p.scene_description}` : ''}
            </option>
          ))}
        </select>

        {canShowFinalControls && (
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-chip-purple/30 bg-chip-purple/5 px-2 py-1.5">
            <div className="text-[9px] text-text-secondary">
              <span className="font-medium text-chip-purple">Draft → Final</span>
              <span className="ml-1">Draft {draftSizeLabel}</span>
              <span className="ml-1">{unapprovedShots ? `Approve ${unapprovedShots} shot${unapprovedShots === 1 ? '' : 's'} with Good` : 'All shots approved'}</span>
            </div>
            <select
              value={finalPreset}
              onChange={event => setFinalPreset(event.target.value)}
              disabled={!modelOptions || finalPresetOptions.length === 0 || finalizing}
              className="bg-bg-tertiary border border-border rounded px-2 py-1 text-[10px] text-text-primary focus:outline-none focus:border-chip-purple disabled:opacity-50"
              aria-label="Final resolution size"
              title="Choose the explicit resolution tier for Final renders"
            >
              <option value="">{modelOptions ? 'Choose Final size…' : 'Loading Final sizes…'}</option>
              {finalPresetOptions.map(option => (
                <option key={option.value} value={option.value} title={option.hint}>
                  {option.label}
                </option>
              ))}
            </select>
            <button
              onClick={() => void createFinal()}
              disabled={!canCreateFinal || !finalPreset || !finalPresetOptions.length || finalizing || loading}
              className="flex items-center gap-1 px-2 py-1 text-[10px] rounded bg-chip-purple/15 border border-chip-purple/30 text-chip-purple hover:bg-chip-purple/25 disabled:opacity-40"
              title={unapprovedShots ? 'Mark every shot Good after review' : 'Render this approved Draft at the selected size'}
            >
              {finalizing ? <Loader2 size={10} className="animate-spin" /> : <FileVideo size={10} />}
              {finalizing ? 'Starting…' : 'Create Final'}
            </button>
            {modelOptionsError && <span className="text-[9px] text-red-400" title={modelOptionsError}>Final sizes unavailable</span>}
            {!modelOptionsError && modelOptions && !finalPresetOptions.length && (
              <span className="text-[9px] text-text-muted">This model has no published explicit size presets.</span>
            )}
          </div>
        )}

        {selectedDraftFinal?.draft_final?.stage === 'final' && (
          <span className="flex items-center gap-1 rounded border border-chip-purple/30 bg-chip-purple/5 px-2 py-1 text-[9px] text-chip-purple">
            Final {selectedDraftFinal.draft_final.final_resolution_preset || ''}
            {selectedDraftFinal.draft_final.final_resolution ? ` · ${selectedDraftFinal.draft_final.final_resolution}` : ''}
          </span>
        )}

        {/* Summary badges */}
        {selectedPipeline && (
          <div className="flex items-center gap-2 text-[10px] shrink-0">
            <span className="flex items-center gap-0.5 text-indicator-success">
              <Check size={10} /> {goodCount}
            </span>
            <span className="flex items-center gap-0.5 text-indicator-warning">
              <AlertTriangle size={10} /> {needsWorkCount}
            </span>
            <span className="text-text-muted">
              / {totalClips} clips
            </span>
            {(selectedPipeline.status === 'crashed'
              || selectedPipeline.status === 'failed'
              || selectedPipeline.status === 'cancelled')
              && selectedDraftFinal?.draft_final?.stage !== 'final' && (
              <button
                onClick={async () => {
                  if (!selectedPipeline) return
                  setResuming(true); setRegenError(null)
                  try {
                    await resumePipeline(selectedPipeline.pipeline_id)
                  } catch (e) {
                    setRegenError(String(e instanceof Error ? e.message : e))
                  } finally {
                    setResuming(false)
                  }
                }}
                disabled={resuming || loading || repairBusy}
                className="flex items-center gap-1 px-2 py-1 text-[10px] bg-green-500/10 border border-green-500/30 rounded text-indicator-success hover:bg-green-500/20 disabled:opacity-40 transition-colors"
                title="Re-run this pipeline from where it stopped — reuses the planning, the start images, and the clip videos that already completed"
              >
                <Play size={10} />
                {resuming ? 'Resuming…' : 'Resume'}
              </button>
            )}
            {repairActive ? (
              <>
                <span
                  className="flex items-center gap-1 px-2 py-1 text-[10px] bg-orange-500/10 border border-orange-500/30 rounded text-chip-orange"
                  title={repair?.message || 'Repair running'}
                >
                  <Loader2 size={10} className="animate-spin" />
                  {repair?.message || 'Repairing'}
                  {repair && repair.total > 0 ? ` (${repair.current}/${repair.total})` : ''}
                </span>
                <button
                  onClick={async () => {
                    if (!selectedPipeline) return
                    const pid = selectedPipeline.pipeline_id
                    setRepairCancellingPid(pid); setRegenError(null)
                    try {
                      await cancelPipelineRepair(pid)
                    } catch (e) {
                      setRegenError(String(e instanceof Error ? e.message : e))
                    } finally {
                      setRepairCancellingPid(current => current === pid ? null : current)
                    }
                  }}
                  disabled={repairCancelling || repair?.status === 'cancelling'}
                  className="flex items-center gap-1 px-2 py-1 text-[10px] bg-red-500/10 border border-red-500/30 rounded text-red-400 hover:bg-red-500/20 disabled:opacity-40 transition-colors"
                  title="Stop after aborting the current model step"
                >
                  {repairCancelling ? <Loader2 size={10} className="animate-spin" /> : <X size={10} />}
                  {repair?.status === 'cancelling' ? 'Cancelling...' : 'Stop'}
                </button>
              </>
            ) : showRepairAction ? (
              <button
                onClick={generateMissing}
                disabled={loading || repairBusy}
                className="flex items-center gap-1 px-2 py-1 text-[10px] bg-orange-500/10 border border-orange-500/30 rounded text-chip-orange hover:bg-orange-500/20 disabled:opacity-40 transition-colors"
                title={hasMissing
                  ? requiresShotImages
                    ? `Repair ${missingImages} missing images + ${missingVideos} missing or stale videos, then join when possible`
                    : `Repair ${missingVideos} missing or stale videos, then join when possible`
                  : 'Check saved clip files and repair anything missing or invalid, then join when possible'}
              >
                {repairStarting ? <Loader2 size={10} className="animate-spin" /> : <Play size={10} />}
                {repairRetryable && !hasMissing
                  ? 'Retry repair'
                  : missingImages > 0
                    ? `Repair ${incompleteClips} clip${incompleteClips === 1 ? '' : 's'}`
                    : missingVideos > 0
                      ? `Repair ${missingVideos} video${missingVideos === 1 ? '' : 's'}`
                      : 'Check + repair'}
              </button>
            ) : null}
            {repair?.status === 'completed' && (
              repair.result_filename ? (
                <a
                  href={getFileUrl(repair.result_filename)}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-1 px-2 py-1 text-[10px] bg-green-500/10 border border-green-500/30 rounded text-indicator-success hover:bg-green-500/20 transition-colors"
                  title="Open the repaired joined video"
                >
                  <Check size={10} /> Repaired + joined
                </a>
              ) : (
                <span className="flex items-center gap-1 px-2 py-1 text-[10px] text-indicator-success">
                  <Check size={10} /> Repair complete
                </span>
              )
            )}
            <button
              onClick={() => {
                if (selectedPipeline) {
                  void loadDirectorFromPipeline(selectedPipeline.pipeline_id)
                }
              }}
              disabled={loading || repairBusy}
              className="flex items-center gap-1 px-2 py-1 text-[10px] bg-accent-blue/10 border border-accent-blue/30 rounded text-accent-blue hover:bg-accent-blue/20 disabled:opacity-40 transition-colors"
              title="Restore this project’s script, prompts, references, models, LoRAs, and Director controls as an editable new revision"
            >
              <Pencil size={10} />
              Open &amp; Edit
            </button>
            <button
              onClick={() => {
                if (!selectedPipeline) return
                setRegenError(null)
                // Surface failures in the existing error slot — a bare
                // rejected promise here looked like the button doing nothing.
                rejoinClips(selectedPipeline.pipeline_id).catch(e =>
                  setRegenError(e instanceof Error ? e.message : 'Rejoin failed'))
              }}
              disabled={loading || repairBusy || totalClips < 2}
              className="flex items-center gap-1 px-2 py-1 text-[10px] bg-accent-blue/10 border border-accent-blue/30 rounded text-accent-blue hover:bg-accent-blue/20 disabled:opacity-40 transition-colors"
              title="Re-join all clips into a new video"
            >
              <Combine size={10} />
              Re-join
            </button>
            <button
              onClick={async () => {
                if (!selectedPipeline) return
                const pid = selectedPipeline.pipeline_id
                if (confirmDeletePid !== pid) {
                  setConfirmDeletePid(pid)
                  setTimeout(() => setConfirmDeletePid(c => (c === pid ? null : c)), 4000)
                  return
                }
                setConfirmDeletePid(null)
                setDeletingPipeline(true)
                setRegenError(null)
                try {
                  await deletePipeline(pid)
                } catch (e) {
                  setRegenError(e instanceof Error ? e.message : 'Delete failed')
                } finally {
                  setDeletingPipeline(false)
                }
              }}
              disabled={loading || deletingPipeline || repairBusy}
              className={`flex items-center gap-1 px-2 py-1 text-[10px] border rounded transition-colors disabled:opacity-40 ${
                confirmDeletePid === selectedPipeline.pipeline_id
                  ? 'bg-red-500/20 border-red-500/50 text-red-400'
                  : 'bg-red-500/10 border-red-500/30 text-red-400/80 hover:bg-red-500/20'
              }`}
              title={confirmDeletePid === selectedPipeline.pipeline_id
                ? `Click again to permanently delete this pipeline and its ${selectedPipeline.output_files?.length ?? 0} media files (they disappear from the gallery too)`
                : 'Delete this pipeline and ALL media it generated'}
            >
              {deletingPipeline ? <Loader2 size={10} className="animate-spin" /> : <Trash2 size={10} />}
              {confirmDeletePid === selectedPipeline.pipeline_id ? 'Confirm?' : 'Delete'}
            </button>
            {(regenError || (repairRetryable ? repair?.error || repair?.message : null)) && (
              <span className="text-[9px] text-red-400 max-w-[200px] truncate" title={regenError || repair?.error || repair?.message || undefined}>
                {regenError || repair?.error || repair?.message}
              </span>
            )}
          </div>
        )}

        <button onClick={() => setOpen(false)}
          className="fixed top-3 right-4 z-[61] p-1.5 rounded-lg bg-bg-secondary hover:bg-bg-hover transition-colors shadow-md border border-border">
          <X size={16} className="text-text-muted" />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {loading && (
          <div className="flex items-center justify-center py-12 text-text-muted">
            <Loader2 size={20} className="animate-spin mr-2" />
            Loading pipeline...
          </div>
        )}

        {!loading && !selectedPipeline && pipelineList.length === 0 && (
          <div className="text-center py-12 text-text-muted">
            <p className="text-sm">No saved pipelines yet</p>
            <p className="text-xs mt-1">Run a Director pipeline and it will appear here</p>
          </div>
        )}

        {selectedPipeline && (
          <>
            {/* Pipeline info */}
            <div className="bg-bg-secondary rounded-lg border border-border p-3 space-y-2">
              <div className="flex items-center gap-2 text-xs">
                <span className={`px-2 py-0.5 rounded text-[10px] font-medium ${
                  selectedPipeline.status === 'completed' ? 'bg-green-500/20 text-indicator-success' :
                  selectedPipeline.status === 'failed' || selectedPipeline.status === 'crashed' ? 'bg-red-500/20 text-chip-red' :
                  'bg-blue-500/20 text-chip-blue'
                }`}>
                  {selectedPipeline.status === 'crashed' ? 'crashed (process died)' : selectedPipeline.status}
                </span>
                <span className="text-text-muted">{selectedPipeline.pipeline_type}</span>
                <span className="text-text-muted">|</span>
                <span className="text-text-muted">{selectedPipeline.image_model}</span>
                <span className="text-text-muted">+</span>
                <span className="text-text-muted">{selectedPipeline.video_model}</span>
              </div>
              {selectedPipeline.scene_description && (
                <p className="text-[11px] text-text-secondary">{selectedPipeline.scene_description}</p>
              )}
              <PipelineProgressBar pipeline={selectedPipeline} />
            </div>

            <PromptAuditNotices pid={selectedPid} status={selectedPipeline.status} />
            {/* LLM Log */}
            <div className="bg-bg-secondary rounded-lg border border-border p-3">
              <h3 className="text-[11px] text-text-secondary uppercase tracking-wider font-medium mb-2">LLM Planning Log</h3>
              <LlmLogPanel pipeline={selectedPipeline} />
              <LoadWriterDraftHistory key={selectedPipeline.pipeline_id}
                load={() => fetchWriterDrafts('director', selectedPipeline.pipeline_id)}/>
            </div>

            {/* Clip Grid */}
            <div>
              <h3 className="text-[11px] text-text-secondary uppercase tracking-wider font-medium mb-2">
                Clips ({totalClips})
              </h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {selectedPipeline.clips.map(clip => (
                  <ClipCard
                    key={clip.index}
                    clip={clip}
                    pipeline={selectedPipeline}
                    busy={repairBusy}
                    onTag={(tag) => tagClip(selectedPipeline.pipeline_id, clip.index, tag)}
                    onSavePrompt={async (idx, update) => {
                      setRegenError(null)
                      await updateClipPrompt(selectedPipeline.pipeline_id, idx, update)
                      await loadPipeline(selectedPipeline.pipeline_id)
                    }}
                    onRerunImage={async (idx, prompt) => {
                      setRegenError(null)
                      try {
                        await rerunClipImage(selectedPipeline.pipeline_id, idx, prompt)
                      } catch (e) {
                        const message = String(e instanceof Error ? e.message : e)
                        setRegenError(message)
                        // Rethrown so the card can show it too: the failure has
                        // to reach the shot the user clicked, not only a banner.
                        throw new Error(message)
                      }
                    }}
                    onRerunVideo={async (idx, prompt) => {
                      setRegenError(null)
                      try {
                        await rerunClipVideo(selectedPipeline.pipeline_id, idx, prompt)
                      } catch (e) {
                        const message = String(e instanceof Error ? e.message : e)
                        setRegenError(message)
                        throw new Error(message)
                      }
                    }}
                    onTakeSelected={async () => {
                      await loadPipeline(selectedPipeline.pipeline_id)
                      await loadPipelineList()
                    }}
                  />
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
