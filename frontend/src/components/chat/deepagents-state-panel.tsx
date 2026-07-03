'use client'

import { FileTextIcon, ListChecksIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import {
  DeepAgentsStateFileList,
  type DeepAgentsStateFileActions,
} from '@/components/chat/deepagents-state-file-list'
import { completedTodoCount, TodosBody } from '@/components/chat/deepagents-todos'
import { CollapsiblePill } from '@/components/chat/tool-ui/collapsible-pill'
import { cn } from '@/lib/utils'
import type { DeepAgentsStateSnapshot } from '@/lib/chat/langgraph-runtime/deepagents-state'

interface DeepAgentsStatePanelProps extends DeepAgentsStateFileActions {
  readonly state: DeepAgentsStateSnapshot
  readonly className?: string
  readonly isLoading?: boolean
  readonly isInterrupted?: boolean
  /**
   * Render the todos ("작업 목록") section. Defaults to true. The live streaming
   * loading indicator passes false: the assistant message already renders the
   * same todos as its persistent "Plan" card (the `write_todos` tool-ui), so
   * showing them here too duplicated the planning state on screen (and tripped
   * Playwright strict mode on the doubled todo text). Files / subagent progress
   * stay live-only, so the indicator keeps rendering those.
   */
  readonly showTodos?: boolean
}

export function DeepAgentsStatePanel({
  state,
  className,
  isLoading = false,
  isInterrupted = false,
  showTodos = true,
  onCopyFile,
  onDownloadFile,
  onOpenPreview,
  onEditFile,
  onSaveFile,
}: DeepAgentsStatePanelProps) {
  const t = useTranslations('chat.deepAgentsState')
  // The two flags below already cover the "nothing to render" case (empty todos
  // and empty files), so a separate hasDeepAgentsState guard would be redundant.
  const showTodosSection = showTodos && state.todos.length > 0
  const showFilesSection = state.files.length > 0
  if (!showTodosSection && !showFilesSection) return null

  return (
    <div className={cn('flex w-full flex-col gap-1.5', className)}>
      {showTodosSection ? (
        <CollapsiblePill
          status="loading"
          kind="thinking"
          title={t('tasks.title')}
          meta={t('tasks.progress', {
            done: completedTodoCount(state.todos),
            total: state.todos.length,
          })}
          leadingIcon={ListChecksIcon}
          defaultExpanded
        >
          <TodosBody todos={state.todos} />
        </CollapsiblePill>
      ) : null}
      {showFilesSection ? (
        <CollapsiblePill
          status="loading"
          kind="tool"
          title={t('files.title')}
          meta={t('files.count', { count: state.files.length })}
          leadingIcon={FileTextIcon}
          defaultExpanded={!showTodosSection}
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
            <p className="text-xs text-muted-foreground">
              {isLoading || isInterrupted
                ? t('files.editDisabledRunning')
                : t('files.editDisabledUnsupported')}
            </p>
          </div>
        </CollapsiblePill>
      ) : null}
    </div>
  )
}
