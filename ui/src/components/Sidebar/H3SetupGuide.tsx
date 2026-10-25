import { HelpCircle } from 'lucide-react'

/**
 * Plain-language setup steps for a MiniMax H3 Director run.
 *
 * The optimization panel exposes several engine toggles whose names mean
 * nothing outside this codebase. Users asked for the answer to "what do I
 * turn on, and where is it?" rather than a redesign, so this keeps the list
 * next to the controls and names the panel where each setting lives.
 */
const STEPS: Array<{ setting: string; where: string; value: string }> = [
  {
    setting: 'Video model',
    where: 'Model picker at the top of the sidebar',
    value: 'H3 Omni — Pruned. It is the one Director expects for voices and references.',
  },
  {
    setting: 'Turbo',
    where: 'Here, in H3 Optimizations',
    value: 'On. It draws with fewer steps, so a shot finishes several times sooner and loses only a little detail.',
  },
  {
    setting: 'Total steps',
    where: 'Director Advanced → Generation',
    value: 'Leave the number Turbo picked (usually 4 or 6). Raising it makes shots slower without fixing artefacts.',
  },
  {
    setting: 'Attention',
    where: 'Here, in H3 Optimizations',
    value: 'Nothing to choose. Maestro uses the fastest engine your model can run, and says so above.',
  },
  {
    setting: 'Resolution and clip length',
    where: 'Director Advanced → Generation',
    value: 'Keep the defaults until one full batch completes. They are the biggest lever on render time.',
  },
  {
    setting: 'Voices and images',
    where: 'Inputs → References',
    value: 'Add one picture or voice per speaker, named exactly as in the brief, so each line keeps the same voice.',
  },
  {
    setting: 'The first shot is slow',
    where: 'Nothing to change',
    value: 'The model is read from disk once (a few minutes). Later shots reuse it, so do not cancel during the first load.',
  },
]

export function H3SetupGuide() {
  return (
    <details className="group rounded-lg border border-border bg-bg-tertiary/40">
      <summary className="flex min-h-9 cursor-pointer list-none items-center gap-2 px-3 py-2 text-[10px] font-medium text-text-primary hover:bg-bg-hover [&::-webkit-details-marker]:hidden">
        <HelpCircle size={13} className="shrink-0 text-text-muted" />
        <span className="min-w-0 flex-1">What to turn on, and where it is</span>
        <span className="text-[9px] text-text-muted group-open:hidden">Open guide</span>
      </summary>
      <ol className="space-y-2 border-t border-border/60 px-3 py-2.5">
        {STEPS.map((step, index) => (
          <li key={step.setting} className="flex gap-2">
            <span className="mt-px shrink-0 text-[9px] font-semibold text-text-muted">
              {index + 1}
            </span>
            <span className="min-w-0">
              <span className="block text-[10px] font-medium text-text-primary">
                {step.setting}
              </span>
              <span className="block text-[9px] leading-relaxed text-text-muted">
                {step.where}
              </span>
              <span className="block text-[9px] leading-relaxed text-text-secondary">
                {step.value}
              </span>
            </span>
          </li>
        ))}
      </ol>
    </details>
  )
}
