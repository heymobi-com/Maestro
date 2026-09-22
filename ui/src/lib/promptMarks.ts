/**
 * Turning the search box's hit list into the pieces a highlight layer paints.
 *
 * A textarea cannot style its own text, so the marks are drawn in a layer behind
 * it. That layer has to be an exact split of the text: every character belongs to
 * exactly one piece, in order, so the mirror stays aligned with the textarea and
 * the painted rectangles sit under the words they belong to. Pure and separate
 * from the component, because a mis-split highlight is invisible in a screenshot
 * and easy to get subtly wrong.
 */

export type MarkMatch = { start: number; end: number }

export type MarkPiece = { text: string; mark: 'none' | 'match' | 'active' }

export function markPieces(
  value: string,
  matches: MarkMatch[],
  active: number,
): MarkPiece[] {
  const text = value ?? ''
  const pieces: MarkPiece[] = []
  let cursor = 0
  for (const [index, match] of (matches ?? []).entries()) {
    const start = Math.max(cursor, Math.min(match.start, text.length))
    const end = Math.max(start, Math.min(match.end, text.length))
    // Overlapping or empty hits are skipped rather than allowed to reorder the
    // text: the split must stay monotonic.
    if (end <= start || match.start < cursor) continue
    if (start > cursor) pieces.push({ text: text.slice(cursor, start), mark: 'none' })
    pieces.push({ text: text.slice(start, end), mark: index === active ? 'active' : 'match' })
    cursor = end
  }
  pieces.push({ text: text.slice(cursor), mark: 'none' })
  return pieces
}
