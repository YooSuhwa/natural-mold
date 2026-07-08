'use client'

import { makeAssistantToolUI } from '@assistant-ui/react'
import { useTranslations } from 'next-intl'
import { CheckCircle2Icon, CircleDotIcon, CircleIcon } from 'lucide-react'
import { CollapsiblePill, pillStatusFromAssistantUi } from './collapsible-pill'
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
    color: 'text-status-success',
    bg: 'bg-status-success/10',
    labelKey: 'completed',
  },
  in_progress: {
    Icon: CircleDotIcon,
    color: 'text-primary-strong',
    bg: 'bg-primary/10',
    labelKey: 'inProgress',
  },
  pending: {
    Icon: CircleIcon,
    color: 'text-muted-foreground',
    bg: 'bg-muted',
    labelKey: 'pending',
  },
} as const

// ──────────────────────────────────────────────
// PlanToolUI — write_todos 도구
// ──────────────────────────────────────────────

export const PlanToolUI = makeAssistantToolUI<WriteTodosArgs, string>({
  toolName: 'write_todos',
  render: ({ args, status }) => <PlanToolView args={args} statusType={status.type} />,
})

// 스트리밍 중 tool-call args는 부분 JSON으로 도착한다 — `todos`가 배열이 되기
// 전(문자열/객체 조각)에도 렌더가 호출되므로 Array.isArray + item shape 가드가
// 없으면 실 LLM 경로에서 렌더 크래시가 난다 (M8-4에서 발견, scripted 모델은
// 완성 args만 방출해 재현 불가).
function normalizeTodoItems(args: WriteTodosArgs | undefined): TodoItem[] {
  const raw = args?.todos ?? args?.items
  if (!Array.isArray(raw)) return []
  return raw.filter(
    (item): item is TodoItem =>
      typeof item === 'object' &&
      item !== null &&
      typeof (item as { content?: unknown }).content === 'string',
  )
}

function PlanToolView({ args, statusType }: { args: WriteTodosArgs; statusType: string }) {
  const t = useTranslations('chat.toolCall.plan')
  const items = normalizeTodoItems(args)
  const isRunning = statusType === 'running'
  const completed = items.filter((it) => it.status === 'completed').length
  const meta = isRunning
    ? t('loading')
    : items.length > 0
      ? `${completed}/${items.length}`
      : undefined

  const body =
    items.length > 0 ? (
      <div>
        {items.map((item, i) => {
          // 부분 스트리밍 args의 status는 'in_prog' 같은 조각일 수 있다.
          const s = STATUS_MAP[item.status ?? 'pending'] ?? STATUS_MAP.pending
          const isLast = i === items.length - 1
          return (
            <div key={i} className="flex items-start gap-2">
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
                    'shrink-0 rounded-full px-1.5 py-0.5 moldy-ui-micro',
                    s.bg,
                    s.color,
                  )}
                >
                  {t(s.labelKey)}
                </span>
              </div>
            </div>
          )
        })}
      </div>
    ) : undefined

  return (
    <CollapsiblePill
      kind="tool"
      status={pillStatusFromAssistantUi(statusType)}
      title={t('title')}
      meta={meta}
      defaultExpanded={items.length > 0}
    >
      {body}
    </CollapsiblePill>
  )
}
