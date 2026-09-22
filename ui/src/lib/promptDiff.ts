/**
 * A word-level comparison of two prompts, so a correction can be read at a glance.
 *
 * The assistant rewrites a compiled prompt that runs to several thousand
 * characters, so "what exactly did it change" is the only question worth putting
 * on screen. That is also what makes a note like "the lip-sync is still wrong"
 * verifiable: the two columns show whether the correction touched the acting
 * instruction or only the prose around it.
 *
 * The tokens keep their trailing whitespace, so each side renders exactly the text
 * it was given, and the identical head and tail are trimmed before comparing --
 * which is what keeps a 4,000-character prompt cheap, because the interesting
 * middle stays small even when the prompt does not.
 */

export type DiffKind = 'same' | 'added' | 'removed'

export type DiffSegment = { text: string; kind: DiffKind }

export type PromptDiff = {
  left: DiffSegment[]
  right: DiffSegment[]
  /** Characters that exist only in the proposal. */
  added: number
  /** Characters that exist only in the current prompt. */
  removed: number
  /** Number of changed runs, which is how many places a reader has to visit. */
  changed: number
}

// A word plus the whitespace that follows it, or a run of whitespace on its own.
const WORD_TOKENS = /\S+\s*|\s+/g

// Above this the token table stops being worth its memory: the comparison falls
// back to whole lines, which still says which parts of the prompt moved.
const MAX_CELLS = 3_000_000

function wordTokens(text: string): string[] {
  return text.match(WORD_TOKENS) ?? []
}

function lineTokens(text: string): string[] {
  return text.match(/[^\n]*\n|[^\n]+/g) ?? []
}

/** Merge neighbouring segments of the same kind: fewer spans, same rendering. */
function merged(segments: DiffSegment[]): DiffSegment[] {
  const out: DiffSegment[] = []
  for (const segment of segments) {
    const last = out[out.length - 1]
    if (last && last.kind === segment.kind) last.text += segment.text
    else out.push({ ...segment })
  }
  return out
}

function compareTokens(a: string[], b: string[]): DiffSegment[] {
  const rows = a.length + 1
  const cols = b.length + 1
  const table = new Int32Array(rows * cols)
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i * cols + j] = a[i] === b[j]
        ? table[(i + 1) * cols + (j + 1)] + 1
        : Math.max(table[(i + 1) * cols + j], table[i * cols + (j + 1)])
    }
  }
  const ops: DiffSegment[] = []
  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      ops.push({ kind: 'same', text: a[i] })
      i += 1
      j += 1
    } else if (table[(i + 1) * cols + j] >= table[i * cols + (j + 1)]) {
      ops.push({ kind: 'removed', text: a[i] })
      i += 1
    } else {
      ops.push({ kind: 'added', text: b[j] })
      j += 1
    }
  }
  while (i < a.length) {
    ops.push({ kind: 'removed', text: a[i] })
    i += 1
  }
  while (j < b.length) {
    ops.push({ kind: 'added', text: b[j] })
    j += 1
  }
  return ops
}

export function diffPrompts(before: string, after: string): PromptDiff {
  const original = before ?? ''
  const proposed = after ?? ''
  const leftTokens = wordTokens(original)
  const rightTokens = wordTokens(proposed)

  let head = 0
  while (
    head < leftTokens.length
    && head < rightTokens.length
    && leftTokens[head] === rightTokens[head]
  ) {
    head += 1
  }
  let leftEnd = leftTokens.length
  let rightEnd = rightTokens.length
  while (
    leftEnd > head
    && rightEnd > head
    && leftTokens[leftEnd - 1] === rightTokens[rightEnd - 1]
  ) {
    leftEnd -= 1
    rightEnd -= 1
  }

  const middleLeft = leftTokens.slice(head, leftEnd)
  const middleRight = rightTokens.slice(head, rightEnd)
  const ops = middleLeft.length * middleRight.length > MAX_CELLS
    ? compareTokens(
        lineTokens(middleLeft.join('')),
        lineTokens(middleRight.join('')),
      )
    : compareTokens(middleLeft, middleRight)

  const left: DiffSegment[] = leftTokens
    .slice(0, head)
    .map(text => ({ text, kind: 'same' as const }))
  const right: DiffSegment[] = rightTokens
    .slice(0, head)
    .map(text => ({ text, kind: 'same' as const }))
  for (const op of ops) {
    if (op.kind === 'same') {
      left.push(op)
      right.push(op)
    } else if (op.kind === 'removed') {
      left.push(op)
    } else {
      right.push(op)
    }
  }
  for (const text of leftTokens.slice(leftEnd)) {
    left.push({ text, kind: 'same' })
    right.push({ text, kind: 'same' })
  }

  const mergedLeft = merged(left)
  const mergedRight = merged(right)
  return {
    left: mergedLeft,
    right: mergedRight,
    added: mergedRight.reduce(
      (total, segment) => total + (segment.kind === 'added' ? segment.text.length : 0),
      0,
    ),
    removed: mergedLeft.reduce(
      (total, segment) => total + (segment.kind === 'removed' ? segment.text.length : 0),
      0,
    ),
    changed: mergedLeft.filter(segment => segment.kind === 'removed').length
      + mergedRight.filter(segment => segment.kind === 'added').length,
  }
}
