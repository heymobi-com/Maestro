import { useMemo, useState } from 'react'
import { BookOpen, Check } from 'lucide-react'
import type { SpeechCitation, SpeechSourceMatch } from '../../api/client'
import { SidebarDialog } from './SidebarPanels'

/**
 * Review the documents a grounded script will cite, before any audio is rendered.
 *
 * The default citation is the document, not the passage: what a listener can be
 * told is "según el informe sobre síntesis de voz local", while the passages are
 * the detail a reviewer might want to check. Keeping the passages collapsed makes
 * the default view honest about what will be claimed.
 */
export function SpeechCitationReview({
  open,
  citations,
  passages,
  onClose,
  onConfirm,
}: {
  open: boolean
  citations: SpeechCitation[]
  passages: SpeechSourceMatch[]
  onClose: () => void
  onConfirm: (citations: SpeechCitation[]) => void
}) {
  const [selected, setSelected] = useState<Record<string, boolean>>({})
  const [showPassages, setShowPassages] = useState(false)

  const keep = useMemo(
    () => citations.filter(citation => selected[citation.source_name] !== false),
    [citations, selected],
  )

  return (
    <SidebarDialog
      open={open}
      title="Sources cited by this script"
      onClose={onClose}
      footer={(
        <button
          type="button"
          onClick={() => onConfirm(keep)}
          disabled={keep.length === 0}
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-accent-blue px-3 py-2 text-xs font-medium text-white disabled:opacity-50"
        >
          <Check size={14} />
          Keep {keep.length} source{keep.length === 1 ? '' : 's'}
        </button>
      )}
    >
      <div className="space-y-3">
        <p className="text-[11px] leading-relaxed text-text-muted">
          The script was written from these documents and cites them by name. Uncheck any
          you do not want credited; the audio render records only the ones you keep.
        </p>

        <div className="space-y-1.5">
          {citations.map(citation => (
            <label
              key={citation.source_name}
              className="flex items-start gap-2 rounded-lg border border-border px-2 py-1.5"
            >
              <input
                type="checkbox"
                checked={selected[citation.source_name] !== false}
                onChange={event => setSelected(current => ({
                  ...current, [citation.source_name]: event.target.checked,
                }))}
                className="mt-0.5"
              />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[11px] text-text-primary">
                  {citation.source_name}
                </span>
                <span className="block text-[10px] text-text-muted">
                  {citation.passages || 0} passage{(citation.passages || 0) === 1 ? '' : 's'}
                  {citation.headings && citation.headings.length > 0
                    ? ` · ${citation.headings.slice(0, 2).join(' · ')}`
                    : ''}
                </span>
              </span>
            </label>
          ))}
        </div>

        {passages.length > 0 && (
          <div className="space-y-1.5">
            <button
              type="button"
              onClick={() => setShowPassages(value => !value)}
              className="flex items-center gap-1.5 text-[11px] text-accent-blue hover:underline"
            >
              <BookOpen size={12} />
              {showPassages ? 'Hide' : 'Review'} the {passages.length} passages used
            </button>
            {showPassages && (
              <div className="space-y-2">
                {passages.map((passage, index) => (
                  <div key={index} className="rounded-lg border border-border bg-bg-tertiary/40 p-2">
                    <p className="text-[10px] text-text-muted">
                      [{index + 1}] {passage.source_name}
                      {passage.heading ? ` · ${passage.heading}` : ''}
                    </p>
                    <p className="mt-1 text-[11px] leading-relaxed text-text-secondary">
                      {passage.text}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {citations.length === 0 && (
          <p className="text-[11px] text-text-muted">
            No source matched this brief, so the script was written without grounding.
          </p>
        )}
      </div>
    </SidebarDialog>
  )
}
