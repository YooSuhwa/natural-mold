type CopyableMessagePart = {
  readonly type: string
  readonly text?: string
}

export type CopyableMessageContent = string | readonly CopyableMessagePart[] | null | undefined

export function getMessageCopyText(content: CopyableMessageContent): string {
  if (typeof content === 'string') return content
  if (!content) return ''

  return content
    .filter((part) => part.type === 'text' || part.type === 'reasoning')
    .map((part) => part.text ?? '')
    .filter(Boolean)
    .join('\n\n')
}

export async function copyTextToClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text)
    return
  }

  if (typeof document === 'undefined' || !document.body) {
    throw new Error('Clipboard API is not available')
  }

  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.left = '-9999px'
  textarea.style.top = '-9999px'

  document.body.appendChild(textarea)
  textarea.select()

  try {
    const copied = document.execCommand('copy')
    if (!copied) throw new Error('Clipboard copy command failed')
  } finally {
    document.body.removeChild(textarea)
  }
}
