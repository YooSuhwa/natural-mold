'use client'

import type { CSSProperties } from 'react'
import { makeAssistantToolUI, useAuiState } from '@assistant-ui/react'
import { CheckIcon, SparklesIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { BuilderHeaderIcon, BuilderMuted, BuilderTitle } from './builder-primitives'

type PhaseStatus = 'pending' | 'in_progress' | 'completed'

interface PhaseTodo {
  id: number
  name: string
  status: PhaseStatus
}

interface TimelineArgs {
  todos: PhaseTodo[]
}

const PHASE_TIMELINE_TOOL_NAME = 'phase_timeline'

/** assistant-ui thread state에서 phase_timeline tool_call들을 모두 찾아 마지막 id를 반환.
 *
 * 백엔드가 phase 전환마다 새 tool_call_id로 phase_timeline을 재emit한다.
 * 이전 봇 메시지에 박힌 phase_timeline은 더 이상 마지막이 아니므로 hide 대상이 됨. */
function selectLatestPhaseTimelineId(
  messages: readonly {
    role?: string
    content?: readonly { type?: string; toolName?: string; toolCallId?: string }[]
  }[],
): string | undefined {
  let latest: string | undefined
  for (const m of messages) {
    if (m.role !== 'assistant') continue
    for (const part of m.content ?? []) {
      if (
        part.type === 'tool-call' &&
        part.toolName === PHASE_TIMELINE_TOOL_NAME &&
        part.toolCallId
      ) {
        latest = part.toolCallId
      }
    }
  }
  return latest
}

/** 모든 phase가 completed/pending만 갖고 있을 때 첫 번째 pending을 in_progress로 도출.
 *
 * 백엔드가 `mark_completed_through(N)`만 호출하면 1..N=completed / N+1..=pending 으로
 * emit되어 어떤 단계도 in_progress가 안 됨. 시각 컴포넌트는 in_progress를 이미 지원하므로
 * 프론트엔드에서 derivation. 백엔드가 향후 explicit emit해도 호환 (이미 in_progress 있으면
 * 미적용). */
function deriveInProgress(todos: PhaseTodo[]): PhaseTodo[] {
  if (todos.some((t) => t.status === 'in_progress')) return todos
  const idx = todos.findIndex((t) => t.status === 'pending')
  if (idx < 0) return todos
  const next = [...todos]
  next[idx] = { ...next[idx], status: 'in_progress' }
  return next
}

function PhaseDot({ status }: { status: PhaseStatus }) {
  if (status === 'completed') {
    return (
      <span className="moldy-phase-dot" data-status={status}>
        <CheckIcon className="size-3" strokeWidth={3} />
      </span>
    )
  }
  if (status === 'in_progress') {
    return (
      <span className="moldy-phase-dot" data-status={status}>
        <span className="moldy-phase-dot-core" data-status={status} />
      </span>
    )
  }
  return (
    <span className="moldy-phase-dot" data-status={status}>
      <span className="moldy-phase-dot-core" data-status={status} />
    </span>
  )
}

function StatusBadge({ status }: { status: PhaseStatus }) {
  const t = useTranslations('chat.phaseTimeline')

  return (
    <span className="moldy-phase-status" data-status={status}>
      {status === 'in_progress' && <span className="moldy-phase-status-pulse" />}
      {t(`status.${status}`)}
    </span>
  )
}

function ProgressRail({ todos }: { todos: PhaseTodo[] }) {
  const t = useTranslations('chat.phaseTimeline')
  const items = deriveInProgress(todos ?? [])
  const total = items.length || 1
  const done = items.filter((t) => t.status === 'completed').length
  const ratio = Math.min(100, (done / total) * 100)
  const phaseStyle = { '--phase-ratio': `${ratio}%` } as CSSProperties

  return (
    <div className="moldy-phase-rail" style={phaseStyle}>
      <div className="mb-3.5 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BuilderHeaderIcon>
            <SparklesIcon className="size-3" />
          </BuilderHeaderIcon>
          <BuilderTitle>{t('title')}</BuilderTitle>
          <BuilderMuted className="tabular-nums">
            {t('progress', { done, total: items.length })}
          </BuilderMuted>
        </div>
        <div className="moldy-phase-progress-track">
          <div className="moldy-phase-progress-fill" />
        </div>
      </div>

      <ol className="relative m-0 list-none p-0">
        <span aria-hidden className="moldy-phase-connector" />
        {items.map((todo) => (
          <li key={todo.id} className="moldy-phase-row">
            <PhaseDot status={todo.status} />
            <span className="moldy-phase-label" data-status={todo.status}>
              <span className="mr-2 moldy-ui-caption font-medium tabular-nums moldy-builder-color-muted">
                {String(todo.id).padStart(2, '0')}
              </span>
              {todo.name}
            </span>
            <StatusBadge status={todo.status} />
          </li>
        ))}
      </ol>
    </div>
  )
}

type ThreadMessageLite = {
  role?: string
  content?: readonly { type?: string; toolName?: string; toolCallId?: string }[]
}

function PhaseTimelineRender({ toolCallId, args }: { toolCallId: string; args: TimelineArgs }) {
  // thread 안 마지막 phase_timeline tool_call이 아니면 hide.
  // 이전 봇 메시지에 박힌 동일 tool은 더 이상 렌더하지 않아 진행 카드가 최신 메시지에만 보이게.
  // assistant-ui 0.12+ 의 `useAuiState((s) => s.thread.messages)` 패턴 — ThreadContext.d.ts 의
  // 마이그레이션 가이드에 공식 노출된 selector.
  const latestId = useAuiState((s) => {
    const thread = (s as { thread?: { messages?: readonly ThreadMessageLite[] } }).thread
    return selectLatestPhaseTimelineId(thread?.messages ?? [])
  })
  if (latestId && latestId !== toolCallId) return null
  return (
    <div className="my-3">
      <ProgressRail todos={args.todos ?? []} />
    </div>
  )
}

export const PhaseTimelineToolUI = makeAssistantToolUI<TimelineArgs, unknown>({
  toolName: PHASE_TIMELINE_TOOL_NAME,
  render: ({ toolCallId, args }) => <PhaseTimelineRender toolCallId={toolCallId} args={args} />,
})
