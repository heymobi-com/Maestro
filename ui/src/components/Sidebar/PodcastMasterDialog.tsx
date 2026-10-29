import { useMemo, useState } from 'react'
import { Download, Loader2, Podcast } from 'lucide-react'
import * as api from '../../api/client'
import { useStore } from '../../stores/useStore'
import { SidebarDialog } from './SidebarPanels'

function parseChapters(text: string): api.PodcastMasterChapter[] {
  const chapters: api.PodcastMasterChapter[] = []
  for (const raw of String(text || '').split(/\r?\n/)) {
    const line = raw.trim()
    if (!line) continue
    const match = line.match(/^(\d{1,2}):(\d{2})(?::(\d{2}))?\s+(.+)$/)
    if (!match) continue
    const start = match[3]
      ? Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3])
      : Number(match[1]) * 60 + Number(match[2])
    const title = match[4].trim()
    if (title) chapters.push({ start, title })
  }
  return chapters.sort((a, b) => a.start - b.start)
}

function FileField({
  label, accept, file, onChange, hint,
}: {
  label: string
  accept: string
  file: File | null
  onChange: (file: File | null) => void
  hint?: string
}) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs text-text-secondary">{label}</span>
      <input
        type="file"
        accept={accept}
        onChange={event => onChange(event.target.files?.[0] || null)}
        className="block w-full text-[11px] text-text-secondary file:mr-2 file:rounded-md file:border-0 file:bg-bg-hover file:px-2 file:py-1 file:text-[11px] file:text-text-primary"
      />
      {file ? <span className="block text-[10px] text-text-muted">{file.name}</span> : null}
      {hint ? <span className="block text-[10px] text-text-muted">{hint}</span> : null}
    </label>
  )
}

export function PodcastMasterDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const outputs = useStore(state => state.outputs)
  const selectedOutput = useStore(state => state.selectedOutput)
  const audioOutputs = useMemo(
    () => outputs.filter(output => output.type === 'audio' && output.path),
    [outputs],
  )
  const [source, setSource] = useState('')
  const [title, setTitle] = useState('Maestro Podcast')
  const [artist, setArtist] = useState('Maestro')
  const [album, setAlbum] = useState('Maestro Podcast')
  const [genre, setGenre] = useState('Podcast')
  const [chaptersText, setChaptersText] = useState('')
  const [introFile, setIntroFile] = useState<File | null>(null)
  const [outroFile, setOutroFile] = useState<File | null>(null)
  const [coverFile, setCoverFile] = useState<File | null>(null)
  const [loudness, setLoudness] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<{ filename: string; duration_seconds: number } | null>(null)

  const selected = audioOutputs[selectedOutput] || audioOutputs[0] || null
  const activeSource = source || selected?.path || ''

  const exportMaster = async () => {
    if (!activeSource) {
      setError('Generate or select an audio output first.')
      return
    }
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const intro = introFile ? (await api.uploadAudio(introFile)).path : undefined
      const outro = outroFile ? (await api.uploadAudio(outroFile)).path : undefined
      const cover = coverFile ? (await api.uploadImage(coverFile)).path : undefined
      const saved = await api.podcastMaster({
        path: activeSource,
        intro_path: intro,
        outro_path: outro,
        cover_path: cover,
        title,
        artist,
        album,
        genre,
        chapters: parseChapters(chaptersText),
        // The reviewed citations travel with the master so the file names them.
        citations: useStore.getState().params._speech_citations,
        loudness,
      })
      await useStore.getState().refreshOutputs()
      setResult(saved)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Podcast master failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <SidebarDialog
      open={open}
      title="Podcast master"
      onClose={onClose}
      footer={(
        <button
          type="button"
          onClick={() => { void exportMaster() }}
          disabled={busy || !activeSource}
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-accent-blue px-3 py-2 text-xs font-medium text-white disabled:opacity-50"
        >
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Podcast size={14} />}
          {busy ? 'Mastering…' : 'Export podcast MP3'}
        </button>
      )}
    >
      <div className="space-y-3">
        <p className="text-[11px] leading-relaxed text-text-muted">
          Joins an optional intro and outro with the episode, normalises to podcast
          loudness, embeds the cover and writes ID3 chapters. Chapter times refer to
          the final file, including the intro.
        </p>

        <label className="block space-y-1.5">
          <span className="text-xs text-text-secondary">Episode audio</span>
          <select
            value={activeSource}
            onChange={event => setSource(event.target.value)}
            disabled={audioOutputs.length === 0}
            className="w-full rounded-lg border border-border bg-bg-tertiary px-2 py-1.5 text-xs text-text-primary"
          >
            {audioOutputs.length === 0 ? <option value="">No audio output yet</option> : null}
            {audioOutputs.map(output => (
              <option key={output.path} value={output.path}>{output.name}</option>
            ))}
          </select>
        </label>

        <div className="grid grid-cols-2 gap-2">
          <label className="block space-y-1.5">
            <span className="text-xs text-text-secondary">Title</span>
            <input value={title} onChange={event => setTitle(event.target.value)}
              className="w-full rounded-lg border border-border bg-bg-tertiary px-2 py-1.5 text-xs text-text-primary" />
          </label>
          <label className="block space-y-1.5">
            <span className="text-xs text-text-secondary">Artist</span>
            <input value={artist} onChange={event => setArtist(event.target.value)}
              className="w-full rounded-lg border border-border bg-bg-tertiary px-2 py-1.5 text-xs text-text-primary" />
          </label>
          <label className="block space-y-1.5">
            <span className="text-xs text-text-secondary">Album</span>
            <input value={album} onChange={event => setAlbum(event.target.value)}
              className="w-full rounded-lg border border-border bg-bg-tertiary px-2 py-1.5 text-xs text-text-primary" />
          </label>
          <label className="block space-y-1.5">
            <span className="text-xs text-text-secondary">Genre</span>
            <input value={genre} onChange={event => setGenre(event.target.value)}
              className="w-full rounded-lg border border-border bg-bg-tertiary px-2 py-1.5 text-xs text-text-primary" />
          </label>
        </div>

        <FileField label="Intro music (optional)" accept="audio/*" file={introFile} onChange={setIntroFile} />
        <FileField label="Outro music (optional)" accept="audio/*" file={outroFile} onChange={setOutroFile} />
        <FileField label="Cover image (optional)" accept="image/*" file={coverFile} onChange={setCoverFile} />

        <label className="block space-y-1.5">
          <span className="text-xs text-text-secondary">Chapters (optional)</span>
          <textarea
            value={chaptersText}
            onChange={event => setChaptersText(event.target.value)}
            rows={3}
            placeholder={'00:00 Cold open\n01:20 Segment 1\n08:45 Closing'}
            className="w-full resize-y rounded-lg border border-border bg-bg-tertiary px-2 py-1.5 font-mono text-[11px] text-text-primary"
          />
          <span className="block text-[10px] text-text-muted">One chapter per line: mm:ss Title.</span>
        </label>

        <label className="flex items-center gap-2 text-xs text-text-secondary">
          <input type="checkbox" checked={loudness} onChange={event => setLoudness(event.target.checked)} />
          Normalise to podcast loudness (-16 LUFS)
        </label>

        {error ? <p className="text-[11px] text-red-400">{error}</p> : null}
        {result ? (
          <a
            href={api.getFileUrl(result.filename)}
            download={result.filename}
            className="flex items-center gap-1.5 text-[11px] text-emerald-400 hover:underline"
          >
            <Download size={12} />
            Saved {result.filename} ({Math.round(result.duration_seconds)}s). Download.
          </a>
        ) : null}
      </div>
    </SidebarDialog>
  )
}

export function PodcastMasterButton() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex w-full items-center justify-center gap-2 rounded-lg border border-border bg-bg-tertiary px-3 py-2 text-xs text-text-secondary hover:border-accent-blue hover:text-text-primary"
      >
        <Podcast size={13} />
        Podcast master (MP3)
      </button>
      <PodcastMasterDialog open={open} onClose={() => setOpen(false)} />
    </>
  )
}
