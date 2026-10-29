import { useCallback, useEffect, useState } from 'react'
import { BookOpen, FileText, FolderOpen, Loader2, Trash2, Upload } from 'lucide-react'
import * as api from '../../api/client'
import { useStore } from '../../stores/useStore'
import { SidebarDialog } from './SidebarPanels'

/**
 * Documents a spoken script can be written from.
 *
 * A one-line brief leaves the writer nothing but improvisation; these passages
 * are what a long episode can quote. The panel is deliberately plain: add
 * documents, see how many passages each one yielded, clear them. The grounding
 * itself happens in the write action, which searches the index with the brief.
 */
function reportNotes(report: Array<Record<string, unknown>>): string[] {
  return report.map(row => {
    const name = String(row.name || 'document')
    const status = String(row.status || '')
    const chunks = Number(row.chunks || 0)
    return `${name}: ${status}`
      + (status === 'indexed' ? ` — ${chunks} passage${chunks === 1 ? '' : 's'}` : '')
  })
}

export function SourcesPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const workspace = useStore(state => state.activeWorkspace)
  const [sources, setSources] = useState<api.SpeechSourceRow[]>([])
  const [chunks, setChunks] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notes, setNotes] = useState<string[]>([])

  const refresh = useCallback(async () => {
    try {
      const result = await api.fetchSources(workspace)
      setSources(result.sources)
      setChunks(result.chunks)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Could not load the documents')
    }
  }, [workspace])

  useEffect(() => {
    if (open) void refresh()
  }, [open, refresh])

  const addDocuments = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setBusy(true)
    setError(null)
    setNotes([])
    try {
      const uploaded = await api.uploadSources(Array.from(files))
      const result = await api.ingestSources({ documents: uploaded.documents, workspace })
      setSources(result.sources)
      setChunks(result.chunks)
      setNotes(reportNotes(result.report))
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Could not index the documents')
    } finally {
      setBusy(false)
    }
  }

  /**
   * A folder is uploaded file by file through the same endpoint a file uses, so one
   * bad document cannot take the batch down: the server reports it per file.
   */
  const addFolder = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setBusy(true)
    setError(null)
    setNotes([])
    try {
      const selected = Array.from(files)
      const accepted = selected.filter(file =>
        /\.(md|markdown|txt|text|rst|html|htm|xhtml|docx|pdf)$/i.test(file.name))
      const rejected = selected.length - accepted.length
      if (accepted.length === 0) {
        setError('That folder has no Markdown, TXT, HTML, DOCX or PDF documents.')
        return
      }
      const uploaded = await api.uploadSources(accepted)
      const result = await api.ingestSources({ documents: uploaded.documents, workspace })
      setSources(result.sources)
      setChunks(result.chunks)
      setNotes([
        ...reportNotes(result.report),
        ...(rejected > 0 ? [`${rejected} file(s) ignored: unsupported type`] : []),
      ])
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Could not index that folder')
    } finally {
      setBusy(false)
    }
  }

  const clearAll = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.resetSources(workspace)
      setSources([])
      setChunks(0)
      setNotes([])
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Could not clear the documents')
    } finally {
      setBusy(false)
    }
  }

  return (
    <SidebarDialog
      open={open}
      title="Sources"
      onClose={onClose}
      footer={sources.length > 0 ? (
        <button
          type="button"
          onClick={() => { void clearAll() }}
          disabled={busy}
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-border bg-bg-tertiary px-3 py-2 text-xs text-text-secondary disabled:opacity-50"
        >
          <Trash2 size={13} /> Clear all sources
        </button>
      ) : null}
    >
      <div className="space-y-3">
        <p className="text-[11px] leading-relaxed text-text-muted">
          Everything written from now on quotes these documents. The writer retrieves the
          passages that match your brief and is told to use only those facts, so a long
          script stops inventing material. Markdown, TXT, HTML, DOCX and PDF.
        </p>

        <div className="grid grid-cols-2 gap-2">
          <label className="flex cursor-pointer items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-bg-tertiary/40 px-3 py-4 text-xs text-text-secondary hover:border-accent-blue">
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
            <span className="truncate">{busy ? 'Indexing…' : 'Add documents'}</span>
            <input
              type="file"
              multiple
              accept=".md,.markdown,.txt,.text,.rst,.html,.htm,.xhtml,.docx,.pdf"
              className="hidden"
              onChange={event => { void addDocuments(event.target.files) }}
            />
          </label>
          <label className="flex cursor-pointer items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-bg-tertiary/40 px-3 py-4 text-xs text-text-secondary hover:border-accent-blue">
            {busy ? <Loader2 size={14} className="animate-spin" /> : <FolderOpen size={14} />}
            <span className="truncate">{busy ? 'Indexing…' : 'Add a folder'}</span>
            <input
              type="file"
              multiple
              // Non-standard attributes React does not type; the browser uses them
              // to offer a folder picker instead of a file picker.
              {...{ webkitdirectory: '', directory: '' }}
              className="hidden"
              onChange={event => { void addFolder(event.target.files) }}
            />
          </label>
        </div>

        {sources.length > 0 && (
          <p className="text-[10px] text-text-muted">
            {sources.length} document{sources.length === 1 ? '' : 's'} · {chunks} passage
            {chunks === 1 ? '' : 's'} searchable
          </p>
        )}

        <div className="space-y-1.5">
          {sources.map(source => (
            <div key={source.id} className="flex items-center gap-2 rounded-lg border border-border px-2 py-1.5">
              <FileText size={12} className="shrink-0 text-text-muted" />
              <span className="min-w-0 flex-1 truncate text-[11px] text-text-primary" title={source.path}>
                {source.name}
              </span>
              <span className="shrink-0 text-[10px] text-text-muted">{source.chunks}</span>
            </div>
          ))}
        </div>

        {notes.length > 0 && (
          <ul className="space-y-0.5">
            {notes.map((note, index) => (
              <li key={index} className="text-[10px] text-text-muted">{note}</li>
            ))}
          </ul>
        )}
        {error ? <p className="text-[11px] text-red-400">{error}</p> : null}
      </div>
    </SidebarDialog>
  )
}

export function SourcesButton() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex w-full items-center justify-center gap-2 rounded-lg border border-border bg-bg-tertiary px-3 py-2 text-xs text-text-secondary hover:border-accent-blue hover:text-text-primary"
      >
        <BookOpen size={13} />
        Sources (document grounding)
      </button>
      <SourcesPanel open={open} onClose={() => setOpen(false)} />
    </>
  )
}
