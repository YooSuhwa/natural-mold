'use client'

import { ListChecksIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { completedTodoCount, TodosBody } from '@/components/chat/deepagents-todos'
import { CollapsiblePill } from '@/components/chat/tool-ui/collapsible-pill'
import type { DeepAgentTodo } from '@/lib/chat/langgraph-runtime/deepagents-state'

/**
 * MissionControlBar — 에이전트 계획(write_todos)의 상시 노출 체크리스트.
 *
 * 스트리밍 로딩 인디케이터(런 종료 시 unmount)와 달리 스레드 최상단에 붙어
 * 스트리밍 중·종료 후·리로드 후 모두 같은 자리에서 진행 상황을 보여준다.
 * 데이터는 `stream.values.todos`(deepagents 계획 채널) — 리로드 시 thread state
 * hydrate로 복원된다. 인라인 write_todos Plan 카드는 "그 시점의 스냅샷"으로
 * 히스토리에 남고, 이 바는 "현재 계획"의 canonical 표면이다.
 */
export function MissionControlBar({ todos }: { readonly todos: readonly DeepAgentTodo[] }) {
  const t = useTranslations('chat.deepAgentsState')
  if (todos.length === 0) return null
  const done = completedTodoCount(todos)
  const allDone = done === todos.length
  return (
    <div
      className="border-b border-border/60 bg-background/95 px-4 py-1.5"
      data-moldy-mission-control="true"
    >
      <div className="mx-auto w-full max-w-3xl">
        <CollapsiblePill
          status={allDone ? 'success' : 'loading'}
          kind="thinking"
          title={t('tasks.title')}
          meta={t('tasks.progress', { done, total: todos.length })}
          leadingIcon={ListChecksIcon}
          defaultExpanded={false}
        >
          <TodosBody todos={todos} />
        </CollapsiblePill>
      </div>
    </div>
  )
}
