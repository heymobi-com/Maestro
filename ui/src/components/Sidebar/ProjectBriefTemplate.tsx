/**
 * The project brief's skeleton, one click from the composer.
 *
 * There was no template anywhere: `docs/` had none, and the box's only guidance was a
 * placeholder, so a director's section names were invented while the pipeline does have a
 * vocabulary. `SUBJECT LOCK` is parsed by the compiler and decides which participant is
 * which `<Subject N>`; everything else is read as a labelled brief by the planning model,
 * and only a part of it reaches each shot.
 *
 * The skeleton matches `docs/Maestro-project-brief-template.md`, including the order and its
 * reason: identity first, then the audio contract, then the world, and the film's story last
 * because it is the planner's text and is deliberately not copied into every shot.
 */

import { FileText } from 'lucide-react'

export const PROJECT_BRIEF_TEMPLATE = `SUBJECT LOCK (critical, non-negotiable):
- <Subject 1> is ALWAYS <name>. <Subject 2> is ALWAYS <name>.

CHARACTERS AND BINDINGS:
<Subject 1> (S1): <age, build, hair, wardrobe head to toe>; identity comes from <Picture 1>.
<Subject 2> (S2)(SINGER): <...>; identity comes from <Picture 2>.

AUDIO-DRIVEN GENERATION:
- The image is driven by the supplied audio. Sync lip movements exactly.
- Only <who> lip-syncs. Everyone else keeps their mouth closed.
- Do not generate new dialogue, invent speech, or add voices or characters.

VOICES:
- S1: <pitch, gender, delivery>. S2: <pitch, gender, delivery>.

CAMERA:
- <the film's language: lens, movement, framing habits>.

AMBIENCE:
- <the visual world: setting, lighting, palette, texture, style>.

DESCRIPTION:
<the film's story: what happens, in what order, and which effects carry the meaning.
One process with stages -- a plant sprouting, blooming, drying -- is one beat spread over
several shots, not one shot. This section is written last on purpose: it is the text the
planner reads, and it is not copied into every shot.>
`

type Props = {
  onInsert: (text: string) => void
  disabled?: boolean
}

export function ProjectBriefTemplate({ onInsert, disabled }: Props) {
  return (
    <button
      type="button"
      onClick={() => onInsert(PROJECT_BRIEF_TEMPLATE)}
      disabled={disabled}
      data-testid="director-brief-template"
      title="Insert the project brief template: subject lock, characters, audio contract, voices, camera, ambience, then the story"
      aria-label="Insert project brief template"
      className="p-2 rounded-lg border border-border text-text-muted hover:text-text-primary hover:border-accent-blue transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
    >
      <FileText size={16} />
    </button>
  )
}
