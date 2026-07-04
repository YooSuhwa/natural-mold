'use client'

import { CheckCircle2Icon, CircleDotIcon, CircleIcon, type LucideIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import type {
  DeepAgentTodo,
  DeepAgentTodoStatus,
} from '@/lib/chat/langgraph-runtime/deepagents-state'

// deepagents 계획(todos) 렌더 공유 모듈 — 스트리밍 로딩 패널(DeepAgentsStatePanel)과
// 상시 노출 미션 컨트롤 바(MissionControlBar)가 같은 행/그룹 렌더를 쓴다.

export const TODO_STATUS_META: Record<
  DeepAgentTodoStatus,
  { readonly Icon: LucideIcon; readonly className: string }
> = {
  completed: { Icon: CheckCircle2Icon, className: 'text-status-success' },
  in_progress: { Icon: CircleDotIcon, className: 'text-primary-strong' },
  pending: { Icon: CircleIcon, className: 'text-muted-foreground' },
}

const TODO_GROUPS: readonly DeepAgentTodoStatus[] = ['in_progress', 'pending', 'completed']

export function completedTodoCount(todos: readonly DeepAgentTodo[]): number {
  return todos.filter((todo) => todo.status === 'completed').length
}

export function TodoRow({ todo }: { readonly todo: DeepAgentTodo }) {
  const t = useTranslations('chat.deepAgentsState.status')
  const meta = TODO_STATUS_META[todo.status]
  const Icon = meta.Icon
  return (
    <li className="flex min-w-0 items-start gap-2 py-1">
      <Icon className={cn('mt-0.5 size-3.5 shrink-0', meta.className)} />
      <span
        className={cn(
          'min-w-0 flex-1 text-xs leading-5',
          todo.status === 'completed' && 'text-muted-foreground line-through',
        )}
      >
        {todo.content}
      </span>
      <span className={cn('shrink-0 moldy-ui-micro', meta.className)}>{t(todo.status)}</span>
    </li>
  )
}

export function TodosBody({ todos }: { readonly todos: readonly DeepAgentTodo[] }) {
  return (
    <div className="space-y-2">
      {TODO_GROUPS.map((status) => {
        const items = todos.filter((todo) => todo.status === status)
        if (items.length === 0) return null
        return (
          <ol key={status} className="space-y-0.5">
            {items.map((todo) => (
              <TodoRow key={todo.id} todo={todo} />
            ))}
          </ol>
        )
      })}
    </div>
  )
}
