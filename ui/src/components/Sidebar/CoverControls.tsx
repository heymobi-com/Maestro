import { useRef, useState } from 'react'
import { Disc3, Loader2, Mic2 } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import { uploadAudio } from '../../api/client'
import { readCoverLyrics } from '../../api/songCover'

/**
 * Cover from a recording: the words of an existing song, and a source audio for
 * the engine that can actually follow it.
 *
 * A cover needs the original's words and a new style. The style is the user's;
 * this panel supplies the other half, and which half that is depends on the
 * engine, because the two engines available cover in completely different ways:
 *
 * - **YuE2** re-sings a *score*: SheetSage2/MERT2 transcribes the recording to
 *   notation and YuE2 performs that, with no access to the audio at all. Every
 *   transcription mistake becomes the cover's mistake, which is why a dense mix
 *   makes it drift out of tune. Its composer mode must be a planning mode
 *   (Direct generation cannot use a source song), and its duration is a ceiling.
 * - **ACE-Step 1.5** conditions on the *audio itself*: `audio_prompt_type` "A"
 *   with `audio_guide` sets the pipeline's `has_src_audio`, `audio_scale` becomes
 *   the cover strength (`cover_steps = num_steps * strength`, so 1 follows the
 *   recording and lower values free the last steps), and the source's own BPM
 *   can be handed over instead of guessed. Its cover mode asks for the original
 *   lyrics, which is exactly what the reading button produces.
 *
 * So the section adapts to the selected model instead of assuming one. It owns
 * no source of its own: the recording is `audio_guide`, the same parameter
 * YuE2's own picker writes, so a file chosen anywhere is the same file.
 */
export function CoverControls() {
  const params = useStore(s => s.params)
  const setParam = useStore(s => s.setParam)
  const modelOptions = useStore(s => s.modelOptions)
  const [reading, setReading] = useState(false)
  const [error, setError] = useState('')
  const [summary, setSummary] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const timbreInput = useRef<HTMLInputElement>(null)

  const yue2 = params.model_type === 'yue2' || modelOptions?.yue2_composition === true
  const taskConfig = modelOptions?.audio_prompt_type_sources
  const taskChoices = (taskConfig?.selection || []).filter(mode => mode !== '')
  // "A" is the source audio in both engines' letters; "B" adds a reference
  // timbre on ACE-Step 1.5.
  const takesSource = taskChoices.length === 0 || taskChoices.some(mode => mode.includes('A'))
  const takesTimbre = taskChoices.some(mode => mode.includes('B'))
  const task = String(params.audio_prompt_type || '')
  const source = task.includes('B') && !task.includes('A')
    ? String(params.audio_guide2 || '')
    : String(params.audio_guide || '')
  const sourceName = source ? source.split(/[\\/]/).pop() : ''
  const strength = Number(params.audio_scale ?? 1)

  const read = async (path: string) => {
    setReading(true)
    setError('')
    setSummary('')
    try {
      const result = await readCoverLyrics({ audio_path: path })
      setParam('prompt', result.lyrics)
      if (result.duration) {
        // YuE2 treats the duration as a ceiling and cuts a song off when it is
        // too low, so it gets room to breathe. ACE-Step is asked for a length,
        // and a cover of a recording is the length of that recording.
        setParam('duration_seconds', yue2 ? Math.ceil(result.duration) + 10 : Math.round(result.duration))
      }
      if (yue2) {
        if (Number(params.model_mode ?? 2) === 2) setParam('model_mode', 0)
      } else if (result.bpm) {
        // Hand over the source's own tempo rather than letting the pipeline
        // guess it: a cover that disagrees with its source about the beat is
        // the first thing the ear notices.
        setParam('custom_settings', { ...(params.custom_settings || {}), bpm: Math.round(result.bpm) })
      }
      const parts = [
        `${result.segments.length} lines`,
        result.language || 'language not detected',
        result.bpm ? `${result.bpm} BPM` : '',
        `${result.sections.length} sections`,
      ].filter(Boolean)
      setSummary(parts.join(' · '))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Reading the recording failed')
    } finally {
      setReading(false)
    }
  }

  const uploadInto = async (file: File, slot: 'audio_guide' | 'audio_guide2') => {
    setReading(true)
    setError('')
    setSummary('')
    try {
      const uploaded = await uploadAudio(file)
      setParam(slot, uploaded.path)
      if (slot === 'audio_guide') {
        // The letter is what lets the pipeline see the file at all: without
        // "A" the source is ignored and the cover becomes a new song.
        if (!task) setParam('audio_prompt_type', 'A')
        await read(uploaded.path)
      } else {
        setReading(false)
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Upload failed')
      setReading(false)
    }
  }

  return (
    <div className="space-y-2 border-b border-border pb-3">
      <label className="text-[11px] text-text-muted uppercase tracking-wider flex items-center gap-1.5">
        <Disc3 size={12} /> Cover from a recording
      </label>

      {takesSource && taskChoices.length > 1 && (
        <label className="block text-[10px] text-text-muted">
          {taskConfig?.label || 'Audio task'}
          <select
            value={task}
            disabled={reading}
            onChange={event => setParam('audio_prompt_type', event.target.value)}
            className="mt-1 w-full rounded-lg border border-border bg-bg-tertiary p-2 text-xs text-text-primary"
          >
            {taskChoices.map(choice => (
              <option key={choice} value={choice}>
                {taskConfig?.labels?.[choice] || choice}
              </option>
            ))}
          </select>
        </label>
      )}

      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={reading}
          onClick={() => input.current?.click()}
          className={`flex-1 px-3 py-2 rounded-lg text-xs font-medium border transition-all ${
            reading
              ? 'bg-bg-tertiary text-text-muted border-border cursor-not-allowed'
              : 'border-border hover:border-accent-blue text-text-primary'
          }`}
        >
          {reading ? 'Reading…' : source ? 'Choose another recording' : 'Choose a recording'}
        </button>
        <button
          type="button"
          disabled={reading || !source}
          title={source ? 'Read the lyrics from this recording' : 'Choose a recording first'}
          onClick={() => { void read(source) }}
          className={`px-3 py-2 rounded-lg text-xs font-medium border transition-all ${
            reading || !source
              ? 'bg-bg-tertiary text-text-muted border-border cursor-not-allowed'
              : 'border-border hover:border-accent-blue text-text-primary'
          }`}
        >
          <Mic2 size={13} className="inline" />
        </button>
      </div>

      {takesTimbre && (
        <button
          type="button"
          disabled={reading}
          onClick={() => timbreInput.current?.click()}
          className={`w-full px-3 py-2 rounded-lg text-xs font-medium border transition-all ${
            reading
              ? 'bg-bg-tertiary text-text-muted border-border cursor-not-allowed'
              : 'border-border hover:border-accent-blue text-text-secondary'
          }`}
        >
          {params.audio_guide2 ? 'Replace reference timbre' : 'Add reference timbre (voice)'}
        </button>
      )}

      {modelOptions?.audio_scale_name && takesSource && (
        <label className="block text-[10px] text-text-muted">
          {modelOptions.audio_scale_name}
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={strength}
            disabled={!source}
            onChange={event => setParam('audio_scale', parseFloat(event.target.value))}
            className="mt-1 w-full h-1 accent-accent-blue disabled:opacity-40"
          />
          <span className="text-text-secondary">
            {strength.toFixed(2)} — 1 follows the recording through every step; lower hands the
            last steps to the new style
          </span>
        </label>
      )}

      <input
        ref={input}
        hidden
        type="file"
        accept="audio/*"
        onChange={async event => {
          const file = event.target.files?.[0]
          if (file) await uploadInto(file, 'audio_guide')
          event.target.value = ''
        }}
      />
      <input
        ref={timbreInput}
        hidden
        type="file"
        accept="audio/*"
        onChange={async event => {
          const file = event.target.files?.[0]
          if (file) await uploadInto(file, 'audio_guide2')
          event.target.value = ''
        }}
      />

      {reading && <Loader2 size={13} className="animate-spin text-accent-blue" />}
      {sourceName && !reading && (
        <p className="text-[10px] text-text-muted truncate" title={source}>{sourceName}</p>
      )}
      {summary && <p className="text-[10px] text-indicator-success leading-snug">{summary}</p>}
      {error && <p role="alert" className="text-[10px] text-red-400 leading-snug">{error}</p>}

      <p className="text-[10px] text-text-muted leading-snug">
        The voice is separated from the mix and its words are written into
        <span className="text-text-secondary"> Lyrics</span> below, with the song's sections —
        replace them there if the transcription mishears a line.
        {yue2
          ? ' Melody and chords is selected for you: a source song needs a planning mode, and YuE2 re-sings a score it transcribes from the recording.'
          : takesSource
            ? ' The recording is the source audio this engine covers from, so the original lyrics are required.'
            : ''}
      </p>
    </div>
  )
}
