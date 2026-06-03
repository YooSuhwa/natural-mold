'use client'

import { ListIcon } from 'lucide-react'
import { useSetAtom } from 'jotai'
import { cn } from '@/lib/utils'
import { useChatConversationId } from '@/components/chat/conversation-context'
import { chatRightRailAtom } from '@/lib/stores/chat-right-rail'

interface Props {
  messageId: string
  content: string
  className?: string
  variant?: 'icon' | 'compact'
  label?: string
}

/**
 * Reusable trigger that opens the right rail in `outline` mode for a message.
 * Designed so other workers (P0-1 message-meta-row) can drop it into their
 * message action menu without touching this component.
 */
export function OutlineTrigger({
  messageId,
  content,
  className,
  variant = 'icon',
  label = 'Outline',
}: Props) {
  const setRail = useSetAtom(chatRightRailAtom)
  const conversationId = useChatConversationId()

  if (!content || !content.trim()) return null

  const handleClick = () => {
    setRail({
      mode: 'outline',
      outline: { conversationId, messageId, content },
    })
  }

  if (variant === 'compact') {
    return (
      <button
        type="button"
        onClick={handleClick}
        className={cn(
          'inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 moldy-ui-caption text-muted-foreground transition-colors hover:bg-accent hover:text-foreground',
          className,
        )}
        title={label}
      >
        <ListIcon className="size-3" aria-hidden />
        {label}
      </button>
    )
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      aria-label={label}
      title={label}
      className={cn(
        'inline-flex size-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground',
        className,
      )}
    >
      <ListIcon className="size-3.5" aria-hidden />
    </button>
  )
}
