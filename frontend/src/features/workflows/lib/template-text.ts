/**
 * Templates in the editor (workflow-plan 3.2): the person types text and picks values;
 * the draft stores segments. In a text box a reference reads ``{{node.field.sub}}`` (node
 * id, then the path), ``{{input.name}}`` for a workflow input. Parsing turns every such
 * token back into a ``ref`` segment, so the stored form never holds ``{{...}}`` strings.
 */
import type { TemplateSegment } from '@/lib/types/workflow'

const TOKEN = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)((?:\.[^{}.\s]+)*)\s*\}\}/g

export function refToken(node: string, path: string): string {
  const dotted = path
    .split('/')
    .filter(Boolean)
    .map((part) => `.${part}`)
    .join('')
  return `{{${node}${dotted}}}`
}

export function segmentsToText(segments: readonly TemplateSegment[] | undefined): string {
  return (segments ?? [])
    .map((segment) =>
      'text' in segment ? segment.text : refToken(segment.ref.node, segment.ref.path),
    )
    .join('')
}

export function textToSegments(text: string): TemplateSegment[] {
  const segments: TemplateSegment[] = []
  let last = 0
  for (const match of text.matchAll(TOKEN)) {
    const at = match.index ?? 0
    if (at > last) segments.push({ text: text.slice(last, at) })
    const path = (match[2] ?? '')
      .split('.')
      .filter(Boolean)
      .map((part) => `/${part}`)
      .join('')
    segments.push({ ref: { node: match[1], path } })
    last = at + match[0].length
  }
  if (last < text.length) segments.push({ text: text.slice(last) })
  return segments
}

/** The references a template uses, in order, without repeats ("이 단계에 전달할 입력"). */
export function referencedValues(
  segments: readonly TemplateSegment[] | undefined,
): { node: string; path: string }[] {
  const seen = new Set<string>()
  const refs: { node: string; path: string }[] = []
  for (const segment of segments ?? []) {
    if ('text' in segment) continue
    const key = `${segment.ref.node}${segment.ref.path}`
    if (seen.has(key)) continue
    seen.add(key)
    refs.push(segment.ref)
  }
  return refs
}
