/**
 * Templates in the editor (workflow-plan 3.2): the person types text and picks values;
 * the draft stores segments. In a text box a reference reads ``{{node.field.sub}}`` (node
 * id, then the path), ``{{input.name}}`` for a workflow input. Parsing turns every such
 * token back into a ``ref`` segment, so the stored form never holds ``{{...}}`` strings.
 * In a loop, ``{{prev:node.field}}`` is the last round's value of a later step
 * (``previous`` on the stored reference).
 */
import type { TemplateSegment } from '@/lib/types/workflow'

const TOKEN = /\{\{\s*(prev:)?([A-Za-z_][A-Za-z0-9_]*)((?:\.[^{}.\s]+)*)\s*\}\}/g

export function refToken(node: string, path: string, previous = false): string {
  const dotted = path
    .split('/')
    .filter(Boolean)
    .map((part) => `.${part}`)
    .join('')
  return `{{${previous ? 'prev:' : ''}${node}${dotted}}}`
}

export function segmentsToText(segments: readonly TemplateSegment[] | undefined): string {
  return (segments ?? [])
    .map((segment) =>
      'text' in segment
        ? segment.text
        : refToken(segment.ref.node, segment.ref.path, segment.ref.previous === true),
    )
    .join('')
}

export function textToSegments(text: string): TemplateSegment[] {
  const segments: TemplateSegment[] = []
  let last = 0
  for (const match of text.matchAll(TOKEN)) {
    const at = match.index ?? 0
    if (at > last) segments.push({ text: text.slice(last, at) })
    const path = (match[3] ?? '')
      .split('.')
      .filter(Boolean)
      .map((part) => `/${part}`)
      .join('')
    segments.push({
      ref: match[1] ? { node: match[2], path, previous: true } : { node: match[2], path },
    })
    last = at + match[0].length
  }
  if (last < text.length) segments.push({ text: text.slice(last) })
  return segments
}

/** The references a template uses, in order, without repeats ("이 단계에 전달할 입력"). */
export function referencedValues(
  segments: readonly TemplateSegment[] | undefined,
): { node: string; path: string; previous?: boolean }[] {
  const seen = new Set<string>()
  const refs: { node: string; path: string; previous?: boolean }[] = []
  for (const segment of segments ?? []) {
    if ('text' in segment) continue
    const key = refKey(segment.ref)
    if (seen.has(key)) continue
    seen.add(key)
    refs.push(segment.ref)
  }
  return refs
}

/** One value's key in lists and chips (a previous-round reference is its own value). */
export function refKey(ref: { node: string; path: string; previous?: boolean }): string {
  return `${ref.previous ? 'prev:' : ''}${ref.node}${ref.path}`
}
