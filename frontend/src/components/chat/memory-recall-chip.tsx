'use client'

import { useMemo } from 'react'
import { BrainIcon } from 'lucide-react'
import { useAtomValue } from 'jotai'
import { useTranslations } from 'next-intl'
import { CollapsiblePill } from '@/components/chat/tool-ui/collapsible-pill'
import { useChatConversationId } from '@/components/chat/conversation-context'
import { useMemories } from '@/lib/hooks/use-memory'
import { chatMemoryRecallAtom, type RecalledMemoryBrief } from '@/lib/stores/chat-memory-recall'

/** 영속 이벤트의 기억 내용 마스킹 값 (backend protocol_redaction 계약). */
const REDACTED_CONTENT = '<redacted>'

function MemoryRecallList({ memories }: { readonly memories: readonly RecalledMemoryBrief[] }) {
  const t = useTranslations('chat.memoryRecall')
  return (
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
            {memory.content && memory.content !== REDACTED_CONTENT ? (
              memory.content
            ) : (
              <span className="text-muted-foreground">{t('hiddenContent')}</span>
            )}
          </span>
        </li>
      ))}
    </ul>
  )
}

/**
 * 리로드 경로 — 영속화된 회상 이벤트는 기억 내용이 `<redacted>`로 마스킹돼
 * 있으므로(공유/스냅샷 안전 계약), brief의 id로 메모리 API에서 내용을
 * 재조회해 합친다. memory-tool-ui가 proposal을 서버 재조회로 복원하는 것과
 * 같은 패턴 — 소유자 전용 API라 공유 페이지에서는 복원되지 않는다.
 * (이 컴포넌트는 redacted brief가 있을 때만 마운트되어 불필요한 fetch가 없다.)
 */
function MemoryRecallListWithJoin({
  memories,
}: {
  readonly memories: readonly RecalledMemoryBrief[]
}) {
  const records = useMemories()
  const resolved = useMemo<readonly RecalledMemoryBrief[]>(() => {
    const contentById = new Map(
      (records.data ?? []).map((record) => [record.id, record.content] as const),
    )
    return memories.map((memory) =>
      memory.content === REDACTED_CONTENT && memory.id
        ? { ...memory, content: contentById.get(memory.id) ?? '' }
        : memory,
    )
  }, [memories, records.data])
  return <MemoryRecallList memories={resolved} />
}

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
  const hasRedacted = memories.some((memory) => memory.content === REDACTED_CONTENT)

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
          {hasRedacted ? (
            <MemoryRecallListWithJoin memories={memories} />
          ) : (
            <MemoryRecallList memories={memories} />
          )}
        </CollapsiblePill>
      </div>
    </div>
  )
}
