'use client'

import { BrainIcon } from 'lucide-react'
import { useAtomValue } from 'jotai'
import { useTranslations } from 'next-intl'
import { CollapsiblePill } from '@/components/chat/tool-ui/collapsible-pill'
import { useChatConversationId } from '@/components/chat/conversation-context'
import { chatMemoryRecallAtom } from '@/lib/stores/chat-memory-recall'

/**
 * MemoryRecallChip — 이번 런의 system prompt에 주입된 장기 기억(회상)을
 * 보여주는 상시 칩. 데이터는 `moldy.memory_recalled` stream-head 이벤트
 * (memory-recall-events.ts)가 대화 단위 atom에 채운다. 회상이 없는 대화에선
 * 렌더하지 않는다. 펼치면 scope 배지 + 기억 미리보기 목록.
 */
export function MemoryRecallChip() {
  const t = useTranslations('chat.memoryRecall')
  const conversationId = useChatConversationId()
  const recallState = useAtomValue(chatMemoryRecallAtom)
  const memories = conversationId ? (recallState[conversationId] ?? []) : []

  if (memories.length === 0) return null

  return (
    <div
      className="border-b border-border/60 bg-background/95 px-4 py-1.5"
      data-moldy-memory-recall="true"
    >
      <div className="mx-auto w-full max-w-3xl">
        <CollapsiblePill
          kind="thinking"
          status="success"
          leadingIcon={BrainIcon}
          title={t('title')}
          meta={t('count', { count: memories.length })}
          defaultExpanded={false}
        >
          <ul className="space-y-1 border-t border-border/60 px-3 py-2">
            {memories.map((memory, index) => (
              <li
                key={memory.id ?? index}
                className="flex items-start gap-2"
                data-moldy-memory-recall-item={memory.scope}
              >
                <span className="mt-0.5 shrink-0 rounded-full bg-muted px-1.5 py-0.5 moldy-ui-micro font-medium text-muted-foreground">
                  {memory.scope === 'user' ? t('scopeUser') : t('scopeAgent')}
                </span>
                <span className="min-w-0 flex-1 moldy-ui-caption leading-relaxed text-foreground/80">
                  {memory.content}
                </span>
              </li>
            ))}
          </ul>
        </CollapsiblePill>
      </div>
    </div>
  )
}
