/** AppendMessage content에서 텍스트 추출 */
export function extractText(content: readonly { type: string; text?: string }[]): string {
  return content
    .filter((p): p is { type: 'text'; text: string } => p.type === 'text')
    .map((p) => p.text)
    .join('')
}
