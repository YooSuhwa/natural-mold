'use client'

import {
  CheckCircle2Icon,
  CircleDotIcon,
  CircleIcon,
  FileTextIcon,
  ListChecksIcon,
  type LucideIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import {
  DeepAgentsStateFileList,
  type DeepAgentsStateFileActions,
} from '@/components/chat/deepagents-state-file-list'
import { CollapsiblePill } from '@/components/chat/tool-ui/collapsible-pill'
import { cn } from '@/lib/utils'
import type {
  DeepAgentsStateSnapshot,
  DeepAgentTodo,
  DeepAgentTodoStatus,
} from '@/lib/chat/langgraph-runtime/deepagents-state'
import { hasDeepAgentsState } from '@/lib/chat/langgraph-runtime/deepagents-state'

interface DeepAgentsStatePanelProps extends DeepAgentsStateFileActions {
  readonly state: DeepAgentsStateSnapshot
  readonly className?: string
  readonly isLoading?: boolean
  readonly isInterrupted?: boolean
}

const STATUS_META: Record<
  DeepAgentTodoStatus,
  { readonly Icon: LucideIcon; readonly className: string }
> = {
  completed: { Icon: CheckCircle2Icon, className: 'text-status-success' },
  in_progress: { Icon: CircleDotIcon, className: 'text-primary-strong' },
  pending: { Icon: CircleIcon, className: 'text-muted-foreground' },
}

const TODO_GROUPS: readonly DeepAgentTodoStatus[] = ['in_progress', 'pending', 'completed']

function completedCount(todos: readonly DeepAgentTodo[]): number {
  return todos.filter((todo) => todo.status === 'completed').length
}

function TodoRow({ todo }: { readonly todo: DeepAgentTodo }) {
  const t = useTranslations('chat.deepAgentsState.status')
  const meta = STATUS_META[todo.status]
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

function TodosBody({ todos }: { readonly todos: readonly DeepAgentTodo[] }) {
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

export function DeepAgentsStatePanel({
  state,
  className,
  isLoading = false,
  isInterrupted = false,
  onCopyFile,
  onDownloadFile,
  onOpenPreview,
  onEditFile,
  onSaveFile,
}: DeepAgentsStatePanelProps) {
  const t = useTranslations('chat.deepAgentsState')
  if (!hasDeepAgentsState(state)) return null
  const done = completedCount(state.todos)
  const editDisabledReason =
    isLoading || isInterrupted ? t('files.editDisabledRunning') : t('files.editDisabledUnsupported')

  return (
    <div className={cn('flex w-full flex-col gap-1.5', className)}>
      {state.todos.length > 0 ? (
        <CollapsiblePill
          status="loading"
          kind="thinking"
          title={t('tasks.title')}
          meta={t('tasks.progress', { done, total: state.todos.length })}
          leadingIcon={ListChecksIcon}
          defaultExpanded
        >
          <TodosBody todos={state.todos} />
        </CollapsiblePill>
      ) : null}
      {state.files.length > 0 ? (
        <CollapsiblePill
          status="loading"
          kind="tool"
          title={t('files.title')}
          meta={t('files.count', { count: state.files.length })}
          leadingIcon={FileTextIcon}
          defaultExpanded={state.todos.length === 0}
        >
          <div className="space-y-2">
            <DeepAgentsStateFileList
              files={state.files}
              onCopyFile={onCopyFile}
              onDownloadFile={onDownloadFile}
              onOpenPreview={onOpenPreview}
              onEditFile={onEditFile}
              onSaveFile={onSaveFile}
            />
            <p className="text-xs text-muted-foreground">{editDisabledReason}</p>
          </div>
        </CollapsiblePill>
      ) : null}
    </div>
  )
}
