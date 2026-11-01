import { useState } from 'react'
import { AlertTriangle, Check, Info, Loader2, Sparkles, X } from 'lucide-react'
import { reviewDirectorBrief, structureDirectorBrief, type BriefReferenceEntry, type BriefReport } from '../../api/client'

/**
 * The brief is the one input a person writes freely, and it is also the input the
 * pipeline reads structurally: the SUBJECT LOCK numbers the participants, the `Stage N`
 * beats decide where a clip sits in the story, the camera language reaches the planner
 * once, and the cast list decides which faces the model draws. A brief written as prose
 * therefore used to cost a whole planning run.
 *
 * This is the friction removed: check the text without loading a model, then let the
 * assistant reorganise it into the shape the selected workflow reads. Nothing is
 * replaced until the person presses Apply, and the proposal is shown in full first.
 */
export function DirectorBriefAssistant({
  brief,
  pipelineType,
  clipCount,
  transcriptSpeakers,
  characters,
  references,
  hasAudio,
  onApply,
  disabled,
}: {
  brief: string
  pipelineType: string
  clipCount: number
  transcriptSpeakers: string[]
  characters: string[]
  references?: unknown[]
  hasAudio: boolean
  onApply: (brief: string) => void
  disabled?: boolean
}) {
  const [report, setReport] = useState<BriefReport | null>(null)
  const [proposal, setProposal] = useState<string | null>(null)
  const [method, setMethod] = useState<'llm' | 'normalized' | null>(null)
  const [resolved, setResolved] = useState<string[]>([])
  const [looks, setLooks] = useState<{ name: string; look: string }[]>([])
  const [roster, setRoster] = useState<BriefReferenceEntry[]>([])
  const [checking, setChecking] = useState(false)
  const [structuring, setStructuring] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const canRun = Boolean(brief.trim()) && !disabled

  const check = async () => {
    setChecking(true); setError(null); setProposal(null)
    try {
      setReport(await reviewDirectorBrief({
        brief, pipelineType, clipCount, transcriptSpeakers, hasAudio,
      }))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Brief check failed')
    } finally {
      setChecking(false)
    }
  }

  const structure = async () => {
    setStructuring(true); setError(null)
    try {
      const result = await structureDirectorBrief({
        brief, pipelineType, clipCount, transcriptSpeakers, characters, hasAudio,
        references: references || [],
      })
      setProposal(result.brief)
      setReport(result.after)
      setMethod(result.method)
      setResolved(result.resolved)
      setLooks(result.looks || [])
      setRoster(result.roster || [])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Brief structuring failed')
    } finally {
      setStructuring(false)
    }
  }

  const errors = (report?.findings || []).filter(f => f.severity === 'error')
  const warnings = (report?.findings || []).filter(f => f.severity === 'warning')
  const infos = (report?.findings || []).filter(f => f.severity === 'info')

  return (
    <div className="min-w-0 space-y-2" data-testid="brief-assistant">
      <div className="flex flex-wrap items-center gap-1.5">
        <button
          type="button"
          onClick={() => void check()}
          disabled={!canRun || checking || structuring}
          className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[10px] text-text-secondary hover:bg-bg-hover hover:text-text-primary disabled:opacity-40"
          title="Check this brief against the shape the selected workflow reads. No model is loaded."
        >
          {checking ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />}
          Check brief
        </button>
        <button
          type="button"
          onClick={() => void structure()}
          disabled={!canRun || checking || structuring}
          className="flex items-center gap-1 rounded-md border border-accent-blue/40 bg-accent-blue/10 px-2 py-1 text-[10px] text-accent-blue hover:bg-accent-blue/20 disabled:opacity-40"
          title="Reorder this brief into SUBJECT LOCK / CHARACTERS / CAMERA / AMBIENCE / DESCRIPTION / NEGATIVE and number its beats, so the planner reads it. Your ideas and names are kept."
        >
          {structuring ? <Loader2 size={11} className="animate-spin" /> : <Sparkles size={11} />}
          {structuring ? 'Structuring…' : 'Structure for this workflow'}
        </button>
        {(report || proposal) && (
          <button
            type="button"
            onClick={() => { setReport(null); setProposal(null); setError(null); setLooks([]); setRoster([]) }}
            className="rounded-md p-1 text-text-muted hover:bg-bg-hover hover:text-text-primary"
            title="Dismiss"
          >
            <X size={11} />
          </button>
        )}
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-2 py-1.5 text-[10px] text-red-300">
          {error}
        </div>
      )}

      {report && (
        <div className="space-y-1" data-testid="brief-assistant-report">
          <div className="text-[10px] text-text-muted">
            {report.label}: {report.chars} chars, {report.stages} beat{report.stages === 1 ? '' : 's'},{' '}
            {report.cast.length} cast row{report.cast.length === 1 ? '' : 's'}
            {(report.speakers?.length || 0) > 0 && (
              <>
                {' · audio voice'}
                {report.speakers!.length === 1 ? ' ' : 's '}
                {report.speakers!.join(', ')}
                {report.speaker_map && Object.keys(report.speaker_map).length > 0 && (
                  <span className="text-text-muted/70">
                    {' '}({Object.entries(report.speaker_map).map(([raw, label]) => `${raw}→${label}`).join(', ')})
                  </span>
                )}
              </>
            )}
            {errors.length === 0 && warnings.length === 0 && ' — no structural fault found'}
          </div>
          {[...errors, ...warnings, ...infos].map(finding => (
            <div
              key={finding.code + finding.message}
              className={`rounded-lg border px-2 py-1.5 text-[10px] leading-snug ${
                finding.severity === 'error'
                  ? 'border-red-500/30 bg-red-500/10 text-text-primary'
                  : finding.severity === 'warning'
                    ? 'border-amber-500/30 bg-amber-500/10 text-text-primary'
                    : 'border-border bg-bg-secondary text-text-secondary'
              }`}
            >
              <div className="flex items-start gap-1">
                {finding.severity === 'info'
                  ? <Info size={10} className="mt-0.5 shrink-0" />
                  : <AlertTriangle size={10} className="mt-0.5 shrink-0" />}
                <span>{finding.message}</span>
              </div>
              <div className="mt-0.5 pl-3.5 text-text-muted">{finding.fix}</div>
            </div>
          ))}
        </div>
      )}

      {proposal && (
        <div className="space-y-1" data-testid="brief-assistant-proposal">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[10px] text-text-muted">
              Proposal ({method === 'llm' ? 'reorganised by the local model' : 'repaired mechanically'})
              {resolved.length > 0 && ` · fixes ${resolved.join(', ')}`}
            </span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => { onApply(proposal); setProposal(null) }}
                className="rounded-md bg-accent-blue px-2 py-0.5 text-[10px] text-white hover:bg-accent-blue-hover"
              >
                Apply
              </button>
              <button
                type="button"
                onClick={() => setProposal(null)}
                className="rounded-md border border-border px-2 py-0.5 text-[10px] text-text-secondary hover:bg-bg-hover"
              >
                Discard
              </button>
            </div>
          </div>
          <pre className="max-h-56 overflow-y-auto whitespace-pre-wrap rounded-lg border border-border bg-bg-secondary p-2 text-[10px] leading-snug text-text-primary scrollbar-visible">
            {proposal}
          </pre>
          {roster.length > 0 && (
            <div className="rounded-lg border border-border bg-bg-secondary px-2 py-1.5 text-[10px] leading-snug text-text-muted">
              <div className="text-text-secondary">
                Bound to the pictures you supplied (this is what stops the model inventing them):
              </div>
              {roster.map(item => (
                <div key={item.number} className="mt-0.5">
                  <span className="text-text-muted">{item.number}.</span>{' '}
                  <span className="text-text-primary">{item.name}</span>
                  {item.cue && <span className="text-accent-blue"> ({item.cue})</span>}
                  {!item.look && <span className="text-amber-400"> — no appearance read</span>}
                </div>
              ))}
            </div>
          )}
          {looks.length > 0 && (
            <div className="rounded-lg border border-border bg-bg-secondary px-2 py-1.5 text-[10px] leading-snug text-text-muted">
              <div className="text-text-secondary">
                Read from the reference pictures (the vision model, not the text):
              </div>
              {looks.map(item => (
                <div key={item.name} className="mt-0.5">
                  <span className="text-text-primary">{item.name}</span>: {item.look}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
