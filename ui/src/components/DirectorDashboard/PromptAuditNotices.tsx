/**
 * What the prompt audit found, where the decision to render is made.
 *
 * The audit runs inside the pipeline on the compiled prompts, and until now its findings reached
 * the server log only — a terminal is not where GPU hours get approved. The same audit is read
 * back here from the saved plan, so a contradiction between two shots is on screen before either
 * shot is paid for, and a re-planned project re-reads it.
 */

import { useCallback, useEffect, useState } from 'react'
import {
  AlertOctagon,
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  ClipboardCopy,
  RefreshCw,
} from 'lucide-react'
import { fetchPromptAudit, type PromptAudit } from '../../api/promptAudit'

/** What has been read for which pipeline: a record keeps a stale audit off a new selection. */
interface Loaded {
  pid: string | null
  audit: PromptAudit | null
  failed: boolean
}

const NOTHING_LOADED: Loaded = { pid: null, audit: null, failed: false }

export function PromptAuditNotices({ pid, status }: { pid: string | null; status?: string }) {
  const [loaded, setLoaded] = useState<Loaded>(NOTHING_LOADED)
  const [open, setOpen] = useState(true)
  const [nonce, setNonce] = useState(0)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!pid) return
    let live = true
    // Re-read whenever the plan moves on, so the findings shown are the ones the plan has now.
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
  }, [pid, status, nonce])

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

      {open && total > 0 && (
        <ul className="mt-2 space-y-1.5">
          {shots
            .filter(shot => shot.findings.length > 0)
            .map(shot => (
              <li key={shot.index} className="text-[11px]">
                <span className="text-text-muted">Shot {shot.shot}</span>
                <ul className="mt-0.5 space-y-0.5">
                  {shot.findings.map((finding, position) => (
                    <li
                      key={`${finding.code}-${position}`}
                      className={
                        finding.severity === 'error'
                          ? 'flex gap-1.5 text-red-400'
                          : 'flex gap-1.5 text-indicator-warning'
                      }
                    >
                      <span className="shrink-0 font-medium">{finding.code}</span>
                      <span className="text-text-secondary">{finding.message}</span>
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
