'use client'

import { makeAssistantToolUI } from '@assistant-ui/react'
import { ArrowRightIcon, CheckIcon, ClockIcon, ListTodoIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

type PhaseStatus = 'pending' | 'in_progress' | 'completed'

interface PhaseTodo {
  id: number
  name: string
  status: PhaseStatus
}

interface TimelineArgs {
  todos: PhaseTodo[]
}

const STATUS_BADGE: Record<PhaseStatus, { label: string; bg: string; text: string }> = {
  pending: {
    label: '대기중',
    bg: 'bg-zinc-100 dark:bg-zinc-800',
    text: 'text-zinc-500 dark:text-zinc-400',
  },
  in_progress: {
    label: '진행중',
    bg: 'bg-blue-100 dark:bg-blue-950',
    text: 'text-blue-700 dark:text-blue-300',
  },
  completed: {
    label: '완료',
    bg: 'bg-violet-100 dark:bg-violet-950',
    text: 'text-violet-700 dark:text-violet-300',
  },
}

function PhaseIcon({ status }: { status: PhaseStatus }) {
  if (status === 'completed') {
    return (
      <div className="flex size-7 items-center justify-center rounded-full bg-violet-500 text-white">
        <CheckIcon className="size-4" />
      </div>
    )
  }
  if (status === 'in_progress') {
    return (
      <div className="flex size-7 items-center justify-center rounded-full bg-blue-500 text-white">
        <ArrowRightIcon className="size-4" />
      </div>
    )
  }
  return (
    <div className="flex size-7 items-center justify-center rounded-full border border-zinc-200 bg-white text-zinc-400 dark:border-zinc-700 dark:bg-zinc-900">
      <ClockIcon className="size-4" />
    </div>
  )
}

function PhaseTimelineCard({ todos }: { todos: PhaseTodo[] }) {
  const items = todos ?? []
  return (
    <div className="my-3 rounded-xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex items-center gap-2 border-b border-zinc-200 px-4 py-3 text-sm font-semibold dark:border-zinc-800">
        <ListTodoIcon className="size-4 text-zinc-500" />
        진행 상황
      </div>
      <ol className="relative px-4 py-3">
        {items.map((todo, idx) => {
          const isLast = idx === items.length - 1
          const nextDone = !isLast && items[idx + 1]?.status === 'completed'
          const lineCompleted = todo.status === 'completed' && nextDone
          const badge = STATUS_BADGE[todo.status]
          return (
            <li key={todo.id} className="relative flex items-center gap-3 py-2">
              {/* 세로 연결선 */}
              {!isLast && (
                <span
                  className={cn(
                    'absolute left-[14px] top-[34px] h-[calc(100%-12px)] w-px',
                    lineCompleted
                      ? 'bg-violet-300 dark:bg-violet-700'
                      : 'bg-zinc-200 dark:bg-zinc-700',
                  )}
                  aria-hidden
                />
              )}
              <PhaseIcon status={todo.status} />
              <span
                className={cn(
                  'flex-1 text-sm',
                  todo.status === 'in_progress'
                    ? 'font-semibold text-zinc-900 dark:text-zinc-100'
                    : todo.status === 'completed'
                      ? 'text-zinc-700 dark:text-zinc-300'
                      : 'text-zinc-500 dark:text-zinc-400',
                )}
              >
                Phase {todo.id}: {todo.name}
              </span>
              <span
                className={cn(
                  'rounded-full px-2.5 py-0.5 text-xs font-medium',
                  badge.bg,
                  badge.text,
                )}
              >
                {badge.label}
              </span>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

export const PhaseTimelineToolUI = makeAssistantToolUI<TimelineArgs, unknown>({
  toolName: 'phase_timeline',
  render: ({ args }) => <PhaseTimelineCard todos={args.todos ?? []} />,
})
