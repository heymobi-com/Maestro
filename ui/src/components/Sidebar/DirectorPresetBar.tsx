/**
 * Reusable Director setups.
 *
 * Reported from use: "cuando algo falla no puedo regresar a corregir sino que tengo que
 * volver a meter todo: personajes, referencias, volver a correr ... en este proceso de
 * ensayo y error esto se debe hacer muchas veces".
 *
 * A Studio recipe saves a look and leaves out the cast images, the audio and the
 * transcript, so it cannot rebuild a Director project. These presets do: the setup is
 * frozen as it is, and loading one fills the composer with the brief, the references,
 * the soundtrack and its analysis already in place.
 */

import { useEffect, useState } from 'react'
import { FolderPlus, Loader2, Save, Trash2 } from 'lucide-react'
import { useStore } from '../../stores/useStore'

export function DirectorPresetBar() {
  const presets = useStore(s => s.directorPresets)
  const loading = useStore(s => s.directorPresetsLoading)
  const loadPresets = useStore(s => s.loadDirectorPresets)
  const loadPreset = useStore(s => s.loadDirectorFromPreset)
  const savePreset = useStore(s => s.saveDirectorPreset)
  const deletePreset = useStore(s => s.deleteDirectorPreset)
  // Saving freezes whatever the composer holds now, so it no longer needs a run first.
  const hasSetup = useStore(s => Boolean(
    s.directorSceneDescription?.trim()
    || s.directorAudioFile
    || s.directorAudioPath
    || s.directorH3References?.length
    || s.directorReferenceImage
    || s.directorCharacterRefs?.length
    || s.directorLocationRefs?.length
  ))
  const busy = useStore(s => s.directorLoading)

  const [selected, setSelected] = useState('')
  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)
  const [opening, setOpening] = useState(false)
  const [note, setNote] = useState<string | null>(null)

  useEffect(() => { void loadPresets() }, [loadPresets])

  useEffect(() => {
    if (!selected && presets.length) setSelected(presets[0].id)
  }, [presets, selected])

  const handleSave = async () => {
    const label = name.trim() || `Setup ${new Date().toLocaleDateString()}`
    setSaving(true)
    setNote(null)
    try {
      await savePreset(label)
      setName('')
      setNote(`Saved “${label}”.`)
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'Could not save the setup')
    } finally {
      setSaving(false)
    }
  }

  const handleLoad = async () => {
    if (!selected) return
    setOpening(true)
    setNote(null)
    try {
      await loadPreset(selected)
    } finally {
      setOpening(false)
    }
  }

  const handleDelete = async () => {
    if (!selected) return
    setNote(null)
    try {
      await deletePreset(selected)
      setSelected('')
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'Could not delete the setup')
    }
  }

  // Always visible in Director: the feature is useless if it appears only after a run,
  // which is exactly when the user is looking for it and cannot find it.
  return (
    <div
      data-testid="director-preset-bar"
      className="rounded-lg border border-border/60 bg-bg-secondary/40 px-2 py-1.5 flex flex-wrap items-center gap-1.5"
    >
      <span className="text-[10px] uppercase tracking-wide text-text-muted">Setups</span>

      {presets.length > 0 ? (
        <>
          <select
            value={selected}
            onChange={e => setSelected(e.target.value)}
            disabled={busy || opening}
            className="text-[10px] bg-bg-primary border border-border rounded px-1 py-0.5 max-w-[42%]"
            title="A saved Director setup: brief, references, audio and analysis"
          >
            {presets.map(preset => (
              <option key={preset.id} value={preset.id}>
                {preset.name}
                {preset.reference_count ? ` · ${preset.reference_count} refs` : ''}
                {preset.audio_name ? ' · audio' : ''}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={handleLoad}
            disabled={busy || opening || !selected}
            className="flex items-center gap-1 px-2 py-0.5 text-[10px] bg-accent-blue/10 border border-accent-blue/30 rounded text-accent-blue hover:bg-accent-blue/20 disabled:opacity-40 transition-colors"
            title="Load this setup into the composer: brief, references, audio and its transcript. The plan is left empty."
          >
            {opening ? <Loader2 size={10} className="animate-spin" /> : <FolderPlus size={10} />}
            Load
          </button>
          <button
            type="button"
            onClick={handleDelete}
            disabled={busy || !selected}
            className="p-1 text-[10px] text-text-muted hover:text-red-400 disabled:opacity-40 transition-colors"
            title="Delete this setup"
            aria-label="Delete setup"
          >
            <Trash2 size={10} />
          </button>
        </>
      ) : (
        <span className="text-[10px] text-text-muted">
          None saved yet — build a project, then <span className="text-text-secondary">Save setup</span>.
        </span>
      )}

      <input
        value={name}
        onChange={e => setName(e.target.value)}
        placeholder="Name this setup"
        disabled={busy || saving}
        className="text-[10px] bg-bg-primary border border-border rounded px-1 py-0.5 w-32"
      />
      <button
        type="button"
        onClick={handleSave}
        disabled={busy || saving || !hasSetup}
        className="flex items-center gap-1 px-2 py-0.5 text-[10px] bg-bg-primary border border-border rounded hover:border-accent-blue/40 disabled:opacity-40 transition-colors"
        title={hasSetup
          ? 'Save this setup: brief, characters, references, audio and settings. The plan is never saved.'
          : 'Add the audio, the references or the brief first'}
      >
        {saving ? <Loader2 size={10} className="animate-spin" /> : <Save size={10} />}
        Save setup
      </button>

      {note && <span className="text-[10px] text-text-muted">{note}</span>}
      {loading && <Loader2 size={10} className="animate-spin text-text-muted" />}
    </div>
  )
}
