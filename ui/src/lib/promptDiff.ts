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
 *
 * Marking is not enough on its own. A compiled prompt repeats its six fields
 * verbatim in every shot, so a measured edit of one word marks 0.1% of 6,184
 * characters: two columns of near-identical text with a needle in them. The
 * comparison therefore also reports the change REGIONS -- each change with the
 * sentence around it and the field it falls in -- which is what a reader can act
 * on when the prompt is long.
 */

export type DiffKind = 'same' | 'added' | 'removed'

export type DiffSegment = { text: string; kind: DiffKind }

export type DiffRegion = {
  /** 1-based position of the change, for "cambio 2 de 5". */
  index: number
  /** The Context-IR field the change falls in, when it can be told. */
  field: string
  left: DiffSegment[]
  right: DiffSegment[]
  /** True when identical text was left out before/after this region. */
  truncatedBefore: boolean
  truncatedAfter: boolean
}

export type PromptDiff = {
  left: DiffSegment[]
  right: DiffSegment[]
  /** Characters that exist only in the proposal. */
  added: number
  /** Characters that exist only in the current prompt. */
  removed: number
  /** Number of changed runs, which is how many places a reader has to visit. */
  changed: number
  /** One entry per place the proposal changes, each with its own context. */
  regions: DiffRegion[]
}

// A word plus the whitespace that follows it, or a run of whitespace on its own.
const WORD_TOKENS = /\S+\s*|\s+/g

// Above this the token table stops being worth its memory: the comparison falls
// back to whole lines, which still says which parts of the prompt moved.
const MAX_CELLS = 3_000_000

// How many unchanged words of context each side of a change keeps. Enough to
// read the sentence the change sits in, short enough that a region stays small.
const CONTEXT_TOKENS = 16

// Two changes closer than this are one reading, not two blocks that repeat the
// same surrounding sentence twice.
const MERGE_GAP = 14

// The fields a compiled Context-IR prompt is made of. A line such as
// "Dialogue: <d>..." inside the body is not a field, so the label is taken from
// this list rather than from "any word before a colon".
const H3_FIELDS = [
  'subject_definitions',
  'summary',
  'retention_analysis',
  'detailed_description',
  'integrated_multimodal_description',
  'overall_soundscape',
  'non_diegetic_music',
]

const FIELD_RE = new RegExp(
  `(?:^|\\n)[ \\t]*(${H3_FIELDS.join('|')})[ \\t]*:`,
  'g',
)

/** Which of the prompt's fields a character offset falls in. */
function fieldAt(text: string, offset: number): string {
  FIELD_RE.lastIndex = 0
  let field = ''
  let match = FIELD_RE.exec(text)
  while (match && match.index < offset) {
    field = match[1]
    match = FIELD_RE.exec(text)
  }
  return field
}

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

  // One aligned sequence, so a change can be located in it, cut out of it, and
  // still land in the same place in both columns.
  const aligned: DiffSegment[] = [
    ...leftTokens.slice(0, head).map(text => ({ text, kind: 'same' as const })),
    ...ops,
    ...leftTokens.slice(leftEnd).map(text => ({ text, kind: 'same' as const })),
  ]
  const left: DiffSegment[] = []
  const right: DiffSegment[] = []
  // Where each aligned token starts in each column, and where it starts in the
  // original text: a region is a slice of the columns, labelled by its offset.
  const leftIndex: number[] = []
  const rightIndex: number[] = []
  const leftOffset: number[] = []
  let offset = 0
  for (const op of aligned) {
    leftIndex.push(left.length)
    rightIndex.push(right.length)
    leftOffset.push(offset)
    if (op.kind !== 'added') {
      left.push(op)
      offset += op.text.length
    }
    if (op.kind !== 'removed') right.push(op)
  }
  leftIndex.push(left.length)
  rightIndex.push(right.length)
  leftOffset.push(offset)

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
    regions: changeRegions(
      aligned, original, { left, right, leftIndex, rightIndex, leftOffset },
    ),
  }
}

/** Each change with the sentence it sits in, so a long prompt stays readable. */
function changeRegions(
  aligned: DiffSegment[],
  original: string,
  columns: {
    left: DiffSegment[]
    right: DiffSegment[]
    leftIndex: number[]
    rightIndex: number[]
    leftOffset: number[]
  },
): DiffRegion[] {
  const touched: number[] = []
  aligned.forEach((op, index) => {
    if (op.kind !== 'same') touched.push(index)
  })
  if (!touched.length) return []

  const groups: number[][] = [[touched[0]]]
  for (const index of touched.slice(1)) {
    const current = groups[groups.length - 1]
    if (index - current[current.length - 1] - 1 <= MERGE_GAP) current.push(index)
    else groups.push([index])
  }

  return groups.map((group, position) => {
    const first = group[0]
    const last = group[group.length - 1]
    let from = Math.max(0, first - CONTEXT_TOKENS)
    let to = Math.min(aligned.length, last + 1 + CONTEXT_TOKENS)
    // Do not open a region with the blank line that separates two fields.
    while (from < first && !aligned[from].text.trim()) from += 1
    while (to > last + 1 && !aligned[to - 1].text.trim()) to -= 1
    return {
      index: position + 1,
      // The field of the FIRST changed token, not of the context window: the
      // context starts up to sixteen words earlier and can sit in the previous
      // field, which labelled a change in detailed_description as one in
      // retention_analysis.
      field: fieldAt(original, columns.leftOffset[first]),
      left: columns.left.slice(columns.leftIndex[from], columns.leftIndex[to]),
      right: columns.right.slice(columns.rightIndex[from], columns.rightIndex[to]),
      truncatedBefore: from > 0,
      truncatedAfter: to < aligned.length,
    }
  })
}
