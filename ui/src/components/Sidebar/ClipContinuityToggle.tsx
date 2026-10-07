/** Let the plan decide which shots continue the one before them.
 *
 * Measured on a real 30-shot project before this existed: 0 of 30 clips carried anything from
 * the clip before it and none declared a continuity decision, so every shot rendered as its own
 * scene -- the reported "cortes duros sin continuidad de la escena de un clip al siguiente".
 * The mechanism is H3's rolling reference, and it is the only one a reference model can use: the
 * literal final-frame handoff needs a start frame, which that model does not accept.
 *
 * Off by default, and it is a permission rather than a decision: the project says continuity may
 * be used between clips, and the plan says where. A film that continues everywhere has no cuts
 * and loses its rhythm, so this belongs with the other workflow switches rather than on.
 *
 * Only offered for a reference (omni) video model, because that is the only path with the
 * mechanism to carry it out.
 */
import { useStore } from '../../stores/useStore'

export function ClipContinuityToggle({ locked }: { locked: boolean }) {
  const enabled = useStore(s => s.directorSequenceContinuity)
  const setEnabled = useStore(s => s.setDirectorSequenceContinuity)
  const referenceModel = useStore(s => {
    const selected = s.selectedModelPerMode.video || ''
    const model = s.models.find(item => item.model_type === selected)
    return !model?.director || model.director.video_strategy === 'omni_reference'
  })

  return (
    <label
      className={`flex items-center gap-1.5 select-none ${
        locked || !referenceModel ? 'cursor-not-allowed opacity-50' : 'cursor-pointer'
      }`}
      title={referenceModel
        ? 'Let the plan carry a late frame of one clip into the next where the story asks for it, and cut where it does not'
        : 'The selected video model has no reference path to carry a frame between clips'}
    >
      <input
        type="checkbox"
        checked={enabled}
        disabled={locked || !referenceModel}
        onChange={e => setEnabled(e.target.checked)}
        className="accent-accent-blue w-3 h-3"
      />
      <span className="text-[10px] text-text-secondary">Clip continuity</span>
    </label>
  )
}
