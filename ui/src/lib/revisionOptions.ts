/**
 * The choices the assistant offered, as one chip each.
 *
 * The backend splits them too, but the answer arrives over HTTP and a running
 * server keeps the parser it started with, so the window has to be able to read a
 * merged answer as well. Measured: "OPTIONS: 1. cortar el plano antes del giro.
 * 2. anadir un inserto de la mano" came back as ONE chip whose text was both
 * proposals, and a chip carrying two instructions executes neither.
 */
const INLINE_NUMBERING = /(?<=\s)(?=\d{1,2}[.)][ \t]+\S)/;
const LEADING_MARKER = /^\s*(?:[-*\u2022]|\d+[.)])\s*/;

function splitOne(option: string): string[] {
  const trimmed = option.trim();
  if (!trimmed) return [];
  const numbered = trimmed
    .split(INLINE_NUMBERING)
    .map(piece => piece.replace(LEADING_MARKER, '').trim())
    .filter(Boolean);
  if (numbered.length > 1) return numbered;
  const piped = trimmed
    .split(' | ')
    .map(piece => piece.replace(LEADING_MARKER, '').trim())
    .filter(Boolean);
  if (piped.length > 1) return piped;
  return [trimmed.replace(LEADING_MARKER, '').trim()];
}

export function splitRevisionOptions(options: string[] | undefined): string[] {
  const out: string[] = [];
  for (const option of options || []) {
    for (const piece of splitOne(String(option))) {
      if (piece) out.push(piece);
    }
  }
  return out;
}
