import { useState } from 'react'
import { AlertTriangle, Loader2, Trash2 } from 'lucide-react'
import { reloadModels } from '../../api/client'
import type { InstalledCheckpoint } from '../../api/client'
import { removeImportedCheckpoint } from '../../api/importedCheckpoints'

// The two affordances the imported-checkpoints view needed and did not have: a
// badge that says the weights are gone, and a way to remove the install. They
// live here so the view upstream owns pays two lines instead of fifty.

export function ImportedCheckpointMissingBadge({ item }: { item: InstalledCheckpoint }) {
  if (!item.missing) return null
  return (
    <span className="flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded bg-indicator-error/90 text-white" title="The weights are gone; only the registration remains.">
      <AlertTriangle size={8} /> weights missing
    </span>
  )
}

export function ImportedCheckpointRemoveButton({ item, onRemoved }: { item: InstalledCheckpoint; onRemoved: () => void }) {
  const [removing, setRemoving] = useState(false)

  const remove = async () => {
    if (removing) return
    if (!window.confirm(`Remove “${item.name || item.model_type}” and its weights from disk?`)) return
    setRemoving(true)
    try {
      await removeImportedCheckpoint(item.model_type)
      // The registry keeps an entry whose definition file is gone until it is
      // asked again, and this removal just deleted one.
      await reloadModels()
      onRemoved()
    } catch (error) {
      console.error('Removing the imported checkpoint failed:', error)
    } finally {
      setRemoving(false)
    }
  }

  return (
    <button
      type="button"
      onClick={() => void remove()}
      disabled={removing}
      aria-label={`Remove ${item.name || item.model_type}`}
      title="Remove this imported checkpoint, its weights and its registration"
      className="absolute top-1.5 right-1.5 z-10 flex items-center gap-0.5 px-1.5 py-0.5 text-[9px] rounded bg-black/70 text-white/80 hover:text-white hover:bg-indicator-error/80 transition-colors disabled:opacity-50"
    >
      {removing ? <Loader2 size={9} className="animate-spin" /> : <Trash2 size={9} />}
      Remove
    </button>
  )
}
