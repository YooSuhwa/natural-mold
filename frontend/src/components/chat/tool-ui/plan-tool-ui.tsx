'use client'

import { makeAssistantToolUI } from '@assistant-ui/react'
import { CheckCircle2Icon, CircleDotIcon, CircleIcon, ListTodoIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

// ──────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────

interface TodoItem {
  content: string
  status?: 'completed' | 'in_progress' | 'pending'
}

interface WriteTodosArgs {
  todos?: TodoItem[]
  items?: TodoItem[] // fallback
}

// ──────────────────────────────────────────────
// Status 설정
// ──────────────────────────────────────────────

const STATUS_MAP = {
  completed: {
    Icon: CheckCircle2Icon,
    color: 'text-emerald-500',
    bg: 'bg-emerald-500/10',
    label: '완료',
  },
  in_progress: {
    Icon: CircleDotIcon,
    color: 'text-primary',
    bg: 'bg-primary/10',
    label: '진행중',
  },
  pending: {
    Icon: CircleIcon,
    color: 'text-muted-foreground',
    bg: 'bg-muted',
    label: '대기',
  },
} as const

// ──────────────────────────────────────────────
// PlanToolUI — write_todos 도구
// ──────────────────────────────────────────────

export const PlanToolUI = makeAssistantToolUI<WriteTodosArgs, string>({
  toolName: 'write_todos',
  render: ({ args, status }) => {
    const items = args?.todos ?? args?.items ?? []
    const isRunning = status.type === 'running'
    const completed = items.filter((it) => it.status === 'completed').length

    return (
      <div className="w-full rounded-xl border bg-muted/20 text-xs">
        {/* Header */}
        <div className="flex items-center gap-2 px-3 py-2">
          <ListTodoIcon className="size-3.5 text-emerald-500" />
          <span className="font-medium">Plan</span>
          {items.length > 0 && (
            <span className="text-muted-foreground">
              {completed}/{items.length}
            </span>
          )}
          {isRunning && (
            <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[10px] text-primary">
              계획 수립 중…
            </span>
          )}
        </div>

        {/* Items */}
        {items.length > 0 && (
          <div className="border-t px-3 py-2">
            {items.map((item, i) => {
              const s = STATUS_MAP[item.status ?? 'pending']
              const isLast = i === items.length - 1
              return (
                <div key={i} className="flex items-start gap-2">
                  {/* 수직 커넥터 + 아이콘 */}
                  <div className="flex flex-col items-center">
                    <div
                      className={cn(
                        'flex size-5 shrink-0 items-center justify-center rounded-full',
                        s.bg,
                      )}
                    >
                      <s.Icon className={cn('size-3', s.color)} />
                    </div>
                    {!isLast && <div className="h-4 w-px bg-border" />}
                  </div>
                  {/* 내용 + 뱃지 */}
                  <div className="flex flex-1 items-start justify-between gap-2 pb-2">
                    <span
                      className={cn(
                        'leading-5',
                        item.status === 'completed' && 'text-muted-foreground line-through',
                      )}
                    >
                      {item.content}
                    </span>
                    <span
                      className={cn(
                        'shrink-0 rounded-full px-1.5 py-0.5 text-[10px]',
                        s.bg,
                        s.color,
                      )}
                    >
                      {s.label}
                    </span>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    )
  },
})
