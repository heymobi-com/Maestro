import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import * as api from '../../api/client'
import type { SavedOmniCharacter } from '../../types'
import { characterDisplayName } from '../../lib/characters'
import { SidebarDialog } from '../Sidebar/SidebarPanels'

/**
 * Edit a saved character in place.
 *
 * Shared by the reference picker (where the character is actually being
 * chosen) and the character browser, so replacing a picture or voice never
 * requires deleting the record that Director plans and presets point at.
 */
export function CharacterEditDialog({
  character,
  onClose,
}: {
  character: SavedOmniCharacter | null
  onClose: () => void
}) {
  const [name, setName] = useState('')
  const [visual, setVisual] = useState<{ path: string; type: 'image' | 'video' } | null>(null)
  const [voicePath, setVoicePath] = useState('')
  const [useVideoVoice, setUseVideoVoice] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!character) return
    setName(character.name)
    setVisual(null)
    setVoicePath('')
    setUseVideoVoice(false)
    setError('')
  }, [character])

  if (!character) return null
  const displayName = characterDisplayName(character.name)
  const kind = visual?.type || character.visual.type

  const pickVisual = async (file: File) => {
    setBusy(true)
    setError('')
    try {
      const uploaded = await api.uploadImage(file)
      setVisual({ path: uploaded.path, type: file.type.startsWith('video/') ? 'video' : 'image' })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not upload that file.')
    } finally { setBusy(false) }
  }
  const pickVoice = async (file: File) => {
    setBusy(true)
    setError('')
    try {
      const uploaded = await api.uploadAudio(file)
      setVoicePath(uploaded.path)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not upload that voice.')
    } finally { setBusy(false) }
  }
  const save = async () => {
    setBusy(true)
    setError('')
    try {
      await api.updateCharacter(character.id, {
        name: name.trim() !== character.name ? name : undefined,
        visual_path: visual?.path,
        visual_type: visual?.type,
        voice_path: voicePath || undefined,
        use_video_voice: useVideoVoice,
      })
      window.dispatchEvent(new Event('maestro-characters-changed'))
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the character.')
    } finally { setBusy(false) }
  }

  const fileClass = 'mt-1 w-full text-[11px] text-text-secondary file:mr-2 file:rounded file:border-0 file:bg-bg-tertiary file:px-2 file:py-1 file:text-text-primary'

  return (
    <SidebarDialog open title={`Edit ${displayName}`} onClose={onClose}>
      <div className="space-y-3 p-3">
        <label className="block text-[11px] text-text-secondary">
          Name
          <input value={name} onChange={event => setName(event.target.value)} disabled={busy}
            className="mt-1 w-full rounded-lg border border-border bg-bg-primary px-2 py-2 text-base text-text-primary focus:border-accent-blue focus:outline-none sm:text-xs" />
        </label>
        <label className="block text-[11px] text-text-secondary">
          Replace the picture or video
          <input type="file" accept="image/*,video/*" disabled={busy}
            onChange={event => { const file = event.target.files?.[0]; if (file) void pickVisual(file) }}
            className={fileClass} />
        </label>
        {visual && <p className="text-[10px] text-accent-blue">New {visual.type} ready: {visual.path.split(/[\\/]/).pop()}</p>}
        <label className="block text-[11px] text-text-secondary">
          Replace the voice (2 seconds or more)
          <input type="file" accept="audio/*" disabled={busy}
            onChange={event => { const file = event.target.files?.[0]; if (file) void pickVoice(file) }}
            className={fileClass} />
        </label>
        {voicePath && <p className="text-[10px] text-accent-blue">New voice ready: {voicePath.split(/[\\/]/).pop()}</p>}
        {kind === 'video' && (
          <label className="flex items-center gap-2 text-[11px] text-text-secondary">
            <input type="checkbox" checked={useVideoVoice} disabled={busy}
              onChange={event => setUseVideoVoice(event.target.checked)} className="accent-accent-blue" />
            Use the video&apos;s own voice
          </label>
        )}
        <p className="text-[10px] leading-relaxed text-text-muted">
          The character keeps its identity, so existing references and Director
          plans keep using the new picture and voice.
        </p>
        {error && <p role="alert" className="text-[11px] text-indicator-error">{error}</p>}
        <div className="flex gap-2 border-t border-border pt-3">
          <button type="button" onClick={() => void save()} disabled={busy}
            className="inline-flex min-h-9 flex-1 items-center justify-center gap-1.5 rounded-lg bg-accent-blue px-3 text-xs font-medium text-white disabled:opacity-40">
            {busy && <Loader2 size={12} className="animate-spin" />}Save changes
          </button>
          <button type="button" onClick={onClose} disabled={busy}
            className="min-h-9 rounded-lg border border-border px-3 text-xs text-text-secondary disabled:opacity-40">
            Cancel
          </button>
        </div>
      </div>
    </SidebarDialog>
  )
}
