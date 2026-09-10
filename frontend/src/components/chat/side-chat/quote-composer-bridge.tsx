'use client'

import { useEffect, useRef } from 'react'
import { useAui } from '@assistant-ui/react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { useChatConversationId } from '@/components/chat/conversation-context'
import type { useResourceContextComposer } from '@/lib/chat/context/use-resource-context-composer'
import { mergeResourceContextMetadata } from '@/lib/chat/context/resource-context'
import { useSideChat } from './side-chat-context'
import { useSideChatDraft } from './use-side-chat-draft'

export function QuoteComposerBridge({
  context,
  inputRef,
}: {
  readonly context: ReturnType<typeof useResourceContextComposer>
  readonly inputRef: React.RefObject<HTMLTextAreaElement | null>
}) {
  const workspace = useSideChat()
  const conversationId = useChatConversationId()
  const aui = useAui()
  const handled = useRef<number | null>(null)
  const t = useTranslations('chat.resourceContext')
  useSideChatDraft(conversationId, context.references)
  useEffect(() => {
    const request = workspace?.delivery
    if (!workspace || !request || handled.current === request.id) return
    const targetId = request.target === 'main' ? workspace.mainId : workspace.sideId
    if (!targetId || conversationId !== targetId) return
    handled.current = request.id
    const result = context.add(request.reference)
    if (result.kind === 'limit') toast.error(t('limit'))
    else {
      if (request.prompt && !aui.composer.getState().text.trim()) {
        aui.composer.setRunConfig({
          custom: mergeResourceContextMetadata(
            aui.composer.getState().runConfig.custom ?? {},
            result.refs,
          ),
        })
        aui.composer.setText(request.prompt)
        aui.composer.send()
      }
      inputRef.current?.focus()
    }
    workspace.acknowledge(request.id)
  }, [workspace, conversationId, context, aui, inputRef, t])
  return null
}
