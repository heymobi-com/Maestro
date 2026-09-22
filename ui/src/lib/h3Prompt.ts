/**
 * Reading the shot markers inside a compiled H3 prompt.
 *
 * A Director clip is one continuous shot, and the compiled Context-IR body says
 * so with a single `[Shot 1]`. A body that also carries `[Shot 2]` is describing
 * several shots inside one clip: the model performs each of them in the same
 * few seconds, so anyone placed in a later framing is rendered a second time.
 * Shot 26 of one project put Ricardo "in the periphery" and then again "in the
 * background blur", in two shots inside a 7.29 s clip, and the duplicate man on
 * screen was those instructions being obeyed.
 */

/** Shot numbers a Context-IR prompt declares, in order of appearance. */
export function declaredShotNumbers(prompt: string): number[] {
  const found: number[] = []
  const pattern = /\[\s*shot\s+(\d+)\s*\]/gi
  let match: RegExpExecArray | null
  while ((match = pattern.exec(prompt || '')) !== null) found.push(Number(match[1]))
  return found
}

/** True when the prompt asks for more than one shot inside a single clip. */
export function declaresMultipleShots(prompt: string): boolean {
  return declaredShotNumbers(prompt).some(number => number > 1)
}

/** The sentence shown beside a prompt that declares several shots. */
export function multipleShotWarning(prompt: string): string {
  const shots = declaredShotNumbers(prompt)
  if (!shots.some(number => number > 1)) return ''
  const listed = shots.map(number => `[Shot ${number}]`).join(', ')
  return (
    `This prompt declares ${shots.length} shots (${listed}) inside one clip. ` +
    'A clip is one continuous shot, so whoever is placed in a later framing is ' +
    'rendered again — that is how a character ends up duplicated on screen. ' +
    'Fold the extra shots into a single [Shot 1].'
  )
}
