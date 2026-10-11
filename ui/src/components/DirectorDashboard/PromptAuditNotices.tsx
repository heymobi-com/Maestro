/**
 * What the prompt audit found, and the way to fix it, where the decision is made.
 *
 * The audit runs inside the pipeline on the compiled prompts, and until now its findings reached
 * the server log only — a terminal is not where GPU hours get approved. The same audit is read
 * back here from the saved plan, so a contradiction between two shots is on screen before either
 * shot is paid for.
 *
 * Fixing is offered, never applied on its own: a finding that is a leftover piece of text comes
 * with the corrected text as a suggestion, the others open the shot's prompt to be edited, and
 * either way the director saves. Nothing is blocked, and nothing is rewritten behind their back.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertOctagon,
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  ClipboardCopy,
  Pencil,
  RefreshCw,
  Save,
  Wand2,
  X,
} from 'lucide-react'
import { fetchPromptAudit, type PromptAudit } from '../../api/promptAudit'
import { updateClipPrompt } from '../../api/client'
import { useStore } from '../../stores/useStore'

/** What has been read for which pipeline: a record keeps a stale audit off a new selection. */
interface Loaded {
  pid: string | null
  audit: PromptAudit | null
  failed: boolean
}

/** The shot being corrected, and the text the director is looking at. */
interface Editing {
  clipIndex: number
  shot: number
  text: string
  suggested: boolean
}

const NOTHING_LOADED: Loaded = { pid: null, audit: null, failed: false }

/** A cheap fingerprint of the plan's prompts, so an edit made anywhere reloads this panel. */
function fingerprints(clip: { index: number; video_prompt?: string }): string {
  const text = clip.video_prompt || ''
  let hash = 0
  for (let position = 0; position < text.length; position += 1) {
    hash = (hash * 31 + text.charCodeAt(position)) | 0
  }
  return `${clip.index}:${text.length}:${hash}`
}

export function PromptAuditNotices({ pid, status }: { pid: string | null; status?: string }) {
  const pipeline = useStore(state => state.dashboardSelectedPipeline)
  const loadPipeline = useStore(state => state.loadSavedPipeline)

  const [loaded, setLoaded] = useState<Loaded>(NOTHING_LOADED)
  const [editing, setEditing] = useState<Editing | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savedShot, setSavedShot] = useState<number | null>(null)
  const [open, setOpen] = useState(true)
  const [nonce, setNonce] = useState(0)
  const [copied, setCopied] = useState(false)

  // The plan itself, not the status: a prompt edited in a clip card changes neither the status
  // nor the id, and this panel is about the prompts.
  const signature = useMemo(
    () => (pipeline && pipeline.pipeline_id === pid ? pipeline.clips.map(fingerprints).join(',') : ''),
    [pipeline, pid],
  )

  useEffect(() => {
    if (!pid) return
    let live = true
    fetchPromptAudit(pid)
      .then(audit => {
        if (live) setLoaded({ pid, audit, failed: false })
      })
      .catch(() => {
        if (live) setLoaded({ pid, audit: null, failed: true })
      })
    return () => {
      live = false
    }
  }, [pid, status, signature, nonce])

  const copyReport = useCallback((report: string | undefined) => {
    if (!report) return
    navigator.clipboard?.writeText(report).then(
      () => {
        setCopied(true)
        setTimeout(() => setCopied(false), 1500)
      },
      () => setCopied(false),
    )
  }, [])

  const save = useCallback(async () => {
    if (!editing || !pid) return
    setSaving(true)
    setError(null)
    try {
      await updateClipPrompt(pid, editing.clipIndex, { video_prompt: editing.text })
      await loadPipeline(pid)
      setSavedShot(editing.shot)
      setEditing(null)
    } catch (failure) {
      setError(String(failure instanceof Error ? failure.message : failure))
    } finally {
      setSaving(false)
    }
  }, [editing, pid, loadPipeline])

  if (!pid) return null

  const current = loaded.pid === pid
  const audit = current ? loaded.audit : null
  const failed = current && loaded.failed
  const busy = !current

  const shots = audit?.findings || []
  const errors = shots.reduce(
    (total, shot) => total + shot.findings.filter(f => f.severity === 'error').length,
    0,
  )
  const warnings = shots.reduce(
    (total, shot) => total + shot.findings.filter(f => f.severity !== 'error').length,
    0,
  )
  const total = errors + warnings

  return (
    <div className="bg-bg-secondary rounded-lg border border-border p-3">
      <div className="flex items-center gap-2">
        <button
          onClick={() => setOpen(value => !value)}
          className="flex items-center gap-1 text-[11px] text-text-secondary uppercase tracking-wider font-medium hover:text-text-primary transition-colors"
        >
          {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
          Prompt audit
        </button>
        <span className="flex-1" />
        {savedShot !== null && (
          <span className="flex items-center gap-1 text-[10px] text-indicator-success">
            <Check size={10} /> shot {savedShot} saved
          </span>
        )}
        {failed ? (
          <span className="text-[10px] text-red-400">audit unavailable</span>
        ) : busy ? (
          <span className="text-[10px] text-text-muted">reading&hellip;</span>
        ) : total === 0 && audit ? (
          <span className="flex items-center gap-1 text-[10px] text-indicator-success">
            <Check size={10} /> no contradictions
          </span>
        ) : (
          <span className="flex items-center gap-2 text-[10px]">
            {errors > 0 && (
              <span className="flex items-center gap-0.5 text-red-400">
                <AlertOctagon size={10} /> {errors}
              </span>
            )}
            {warnings > 0 && (
              <span className="flex items-center gap-0.5 text-indicator-warning">
                <AlertTriangle size={10} /> {warnings}
              </span>
            )}
          </span>
        )}
        {audit?.report && (
          <button
            onClick={() => copyReport(audit?.report)}
            className="text-text-muted hover:text-text-primary transition-colors"
            title="Copy the audit report"
          >
            {copied ? <Check size={11} /> : <ClipboardCopy size={11} />}
          </button>
        )}
        <button
          onClick={() => setNonce(value => value + 1)}
          className="text-text-muted hover:text-text-primary transition-colors"
          title="Re-read the audit"
        >
          <RefreshCw size={11} className={busy ? 'animate-spin' : undefined} />
        </button>
      </div>

      {error && <p className="mt-2 text-[10px] text-red-400">{error}</p>}

      {open && total > 0 && (
        <ul className="mt-2 space-y-1.5">
          {shots
            .filter(shot => shot.findings.length > 0)
            .map(shot => (
              <li key={shot.index} className="text-[11px]">
                <span className="text-text-muted">Shot {shot.shot}</span>
                <ul className="mt-0.5 space-y-1">
                  {shot.findings.map((finding, position) => (
                    <li key={`${finding.code}-${position}`}>
                      <div
                        className={
                          finding.severity === 'error'
                            ? 'flex flex-wrap items-baseline gap-1.5 text-red-400'
                            : 'flex flex-wrap items-baseline gap-1.5 text-indicator-warning'
                        }
                      >
                        <span className="shrink-0 font-medium">{finding.code}</span>
                        <span className="flex-1 text-text-secondary">{finding.message}</span>
                        {finding.in_prompt === false ? (
                          <span className="text-[10px] text-text-muted">
                            lives in the pre-polish draft
                          </span>
                        ) : (
                          <>
                            {finding.fix && (
                              <button
                                onClick={() => {
                                  setError(null)
                                  setSavedShot(null)
                                  setEditing({
                                    clipIndex: shot.index,
                                    shot: shot.shot,
                                    text: finding.fix || '',
                                    suggested: true,
                                  })
                                }}
                                className="flex items-center gap-0.5 rounded border border-border px-1.5 py-0.5 text-[10px] text-text-muted hover:text-text-primary transition-colors"
                                title="Put the corrected text in the editor, to review before saving"
                              >
                                <Wand2 size={9} /> suggest the fix
                              </button>
                            )}
                            <button
                              onClick={() => {
                                setError(null)
                                setSavedShot(null)
                                setEditing({
                                  clipIndex: shot.index,
                                  shot: shot.shot,
                                  text: shot.prompt || '',
                                  suggested: false,
                                })
                              }}
                              className="flex items-center gap-0.5 rounded border border-border px-1.5 py-0.5 text-[10px] text-text-muted hover:text-text-primary transition-colors"
                            >
                              <Pencil size={9} /> fix
                            </button>
                          </>
                        )}
                      </div>
                      {editing && editing.shot === shot.shot && (
                        <div className="mt-1 space-y-1">
                          <textarea
                            value={editing.text}
                            onChange={event =>
                              setEditing(value =>
                                value ? { ...value, text: event.target.value, suggested: false } : value,
                              )
                            }
                            rows={8}
                            spellCheck={false}
                            className="w-full rounded border border-border bg-bg-primary p-2 font-mono text-[10px] text-text-primary"
                          />
                          <div className="flex flex-wrap items-center gap-2">
                            <button
                              onClick={save}
                              disabled={saving}
                              className="flex items-center gap-1 rounded bg-accent-blue/20 px-2 py-0.5 text-[10px] text-accent-blue hover:bg-accent-blue/30 disabled:opacity-40 transition-colors"
                            >
                              <Save size={10} /> {saving ? 'saving…' : `save shot ${editing.shot}`}
                            </button>
                            <button
                              onClick={() => {
                                setEditing(null)
                                setError(null)
                              }}
                              className="flex items-center gap-1 rounded border border-border px-2 py-0.5 text-[10px] text-text-muted hover:text-text-primary transition-colors"
                            >
                              <X size={10} /> cancel
                            </button>
                            <span className="text-[10px] text-text-muted">
                              {editing.suggested
                                ? 'suggested text — review it before saving'
                                : 'the shot renders this text as written'}
                            </span>
                          </div>
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
        </ul>
      )}
    </div>
  )
}
