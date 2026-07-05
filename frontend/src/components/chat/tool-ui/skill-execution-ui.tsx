'use client'

import { makeAssistantToolUI } from '@assistant-ui/react'
import { useTranslations } from 'next-intl'
import { BookOpenIcon, FileIcon } from 'lucide-react'
import { CollapsiblePill, pillStatusFromAssistantUi } from './collapsible-pill'
import { useIsToolGroupChild } from './tool-group-child-context'
import { useChatConversationId } from '@/components/chat/conversation-context'
import { API_BASE } from '@/lib/api/client'

// ──────────────────────────────────────────────
// SkillExecutionToolUI — execute_in_skill 전용 리치 pill (W2-4/6).
//
// 역할 분담: stdout은 moldy.ui_data terminal 카드가, 생성 파일 미리보기는
// artifact 카드가 담당한다. 이 pill은 "어떤 스킬이 무슨 커맨드를 실행했고
// 어떤 파일을 냈는가"의 요약 + 파일 링크만 책임진다.
//
// 라이브 런에서 HITL 승인 카드가 뜨는 동안 raw pill은
// stripInterruptedRawToolCalls로 숨겨진다 — 이 카드가 주로 보이는 지점은
// 리로드된 대화와 HITL이 꺼진(허용된) 실행이다.
// ──────────────────────────────────────────────

interface SkillExecutionArgs {
  skill_directory?: string
  command?: string
  [key: string]: unknown
}

/** skill_directory 가상 경로에서 스킬 이름(마지막 세그먼트)을 뽑는다. */
export function skillNameFromDirectory(directory: unknown): string | null {
  if (typeof directory !== 'string') return null
  const segments = directory.split('/').filter(Boolean)
  const last = segments[segments.length - 1]
  return last && last !== 'skills' ? last : null
}

/** 결과 문자열 끝의 `OUTPUT_FILES: a.md, b.png` 계약 라인에서 파일명 목록 추출. */
export function outputFilesFromResult(result: unknown): string[] {
  if (typeof result !== 'string') return []
  const marker = 'OUTPUT_FILES:'
  const index = result.lastIndexOf(marker)
  if (index === -1) return []
  return result
    .slice(index + marker.length)
    .split(',')
    .map((name) => name.trim())
    .filter(Boolean)
}

function SkillExecutionRender({
  args,
  result,
  status,
}: {
  args: SkillExecutionArgs
  result?: unknown
  status: { readonly type: string }
}) {
  const t = useTranslations('chat.toolCall.skillExecution')
  const isGroupChild = useIsToolGroupChild()
  const conversationId = useChatConversationId()
  const isRunning = status.type === 'running'
  const skillName = skillNameFromDirectory(args?.skill_directory)
  const command = typeof args?.command === 'string' ? args.command : ''
  const files = isRunning ? [] : outputFilesFromResult(result)

  const title = skillName ?? t('fallbackTitle')
  const meta = isRunning
    ? t('running')
    : files.length > 0
      ? t('files', { count: files.length })
      : t('completed')

  const hasBody = Boolean(command) || files.length > 0
  const body = hasBody ? (
    <div className="space-y-2 border-t border-border/60 px-3 py-2">
      {command ? (
        <div>
          <div className="mb-1 moldy-ui-micro font-semibold uppercase tracking-wider text-muted-foreground">
            {t('command')}
          </div>
          <pre className="whitespace-pre-wrap break-all rounded-md bg-muted/45 px-2 py-1.5 font-mono moldy-ui-caption text-foreground/85">
            {command}
          </pre>
        </div>
      ) : null}
      {files.length > 0 ? (
        <div>
          <div className="mb-1 moldy-ui-micro font-semibold uppercase tracking-wider text-muted-foreground">
            {t('outputFiles')}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {files.map((name) => (
              <a
                key={name}
                href={
                  conversationId
                    ? `${API_BASE}/api/conversations/${conversationId}/files/${encodeURIComponent(name)}`
                    : undefined
                }
                target="_blank"
                rel="noopener noreferrer"
                data-moldy-skill-file={name}
                className="inline-flex max-w-56 items-center gap-1 rounded-md border border-border/60 bg-background px-2 py-1 moldy-ui-caption text-foreground/85 transition-colors hover:bg-accent hover:text-foreground"
              >
                <FileIcon className="size-3 shrink-0 text-muted-foreground" aria-hidden />
                <span className="truncate">{name}</span>
              </a>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  ) : undefined

  return (
    <div data-moldy-skill-execution={skillName ?? 'unknown'}>
      <CollapsiblePill
        kind="tool"
        leadingIcon={BookOpenIcon}
        status={pillStatusFromAssistantUi(status.type)}
        title={title}
        meta={meta}
        defaultExpanded={!isGroupChild && files.length > 0}
        renderBody={body ? () => body : undefined}
      />
    </div>
  )
}

export const SkillExecutionToolUI = makeAssistantToolUI<SkillExecutionArgs, unknown>({
  toolName: 'execute_in_skill',
  render: SkillExecutionRender,
})
