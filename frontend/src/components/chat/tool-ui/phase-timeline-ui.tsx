'use client'

import { makeAssistantToolUI, useAssistantState } from '@assistant-ui/react'
import { CheckIcon, SparklesIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

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

/** Builder ProgressRail 전용 토큰. 빌더 화면 한정 색이라 globals.css로 승격하지 않고 모듈 스코프로 보유. */
const T = {
  border: 'oklch(0.93 0.005 163)',
  surface: '#ffffff',
  ink: 'oklch(0.18 0.005 163)',
  muted: 'oklch(0.55 0.01 163)',
  mutedSoft: 'oklch(0.72 0.01 163)',
  primary: 'oklch(0.596 0.145 163.225)',
  primaryBg: 'oklch(0.96 0.04 163)',
  primaryBgStrong: 'oklch(0.92 0.06 163)',
  trackBg: 'oklch(0.95 0.005 163)',
  connectorRest: 'oklch(0.91 0.005 163)',
  pendingDot: 'oklch(0.85 0.005 163)',
  activeBadgeFg: 'oklch(0.32 0.1 163)',
} as const

function PhaseDot({ status }: { status: PhaseStatus }) {
  const base = {
    width: 22,
    height: 22,
    flexShrink: 0,
    boxShadow: `0 0 0 4px ${T.surface}`,
  } as const

  if (status === 'completed') {
    return (
      <span
        className="inline-flex items-center justify-center rounded-full text-white"
        style={{ ...base, background: T.primary }}
      >
        <CheckIcon className="size-3" strokeWidth={3} />
      </span>
    )
  }
  if (status === 'in_progress') {
    return (
      <span
        className="inline-flex items-center justify-center rounded-full"
        style={{ ...base, background: T.surface, border: `2px solid ${T.primary}` }}
      >
        <span
          className="rounded-full"
          style={{
            width: 8,
            height: 8,
            background: T.primary,
            animation: 'cb-pulse 1.6s ease-in-out infinite',
          }}
        />
      </span>
    )
  }
  return (
    <span
      className="inline-flex items-center justify-center rounded-full"
      style={{
        ...base,
        background: T.surface,
        border: `1.5px dashed ${T.pendingDot}`,
      }}
    >
      <span className="rounded-full" style={{ width: 4, height: 4, background: T.pendingDot }} />
    </span>
  )
}

function StatusBadge({ status }: { status: PhaseStatus }) {
  const t = useTranslations('chat.phaseTimeline')
  const bgFg: { bg: string; fg: string; border: string } =
    status === 'completed'
      ? { bg: T.primaryBg, fg: T.primary, border: 'transparent' }
      : status === 'in_progress'
        ? { bg: T.primaryBgStrong, fg: T.activeBadgeFg, border: 'transparent' }
        : { bg: 'transparent', fg: T.mutedSoft, border: T.border }

  return (
    <span
      className="inline-flex shrink-0 items-center gap-1.5 rounded-full text-[11px] font-semibold"
      style={{
        padding: '2px 8px',
        background: bgFg.bg,
        color: bgFg.fg,
        border: `1px solid ${bgFg.border}`,
        letterSpacing: '-0.005em',
      }}
    >
      {status === 'in_progress' && (
        <span
          className="rounded-full"
          style={{
            width: 5,
            height: 5,
            background: 'currentColor',
            animation: 'cb-pulse 1.6s ease-in-out infinite',
          }}
        />
      )}
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

  return (
    <div
      className="rounded-[14px]"
      style={{
        background: T.surface,
        border: `1px solid ${T.border}`,
        padding: '16px 18px 14px',
        boxShadow: '0 1px 2px oklch(0.4 0.05 163 / 0.04)',
      }}
    >
      <div className="mb-3.5 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span
            className="inline-flex items-center justify-center rounded-[7px]"
            style={{ width: 22, height: 22, background: T.primaryBg, color: T.primary }}
          >
            <SparklesIcon className="size-3" />
          </span>
          <span
            className="text-[13.5px] font-semibold"
            style={{ color: T.ink, letterSpacing: '-0.01em' }}
          >
            {t('title')}
          </span>
          <span className="text-[12px] tabular-nums" style={{ color: T.muted }}>
            {t('progress', { done, total: items.length })}
          </span>
        </div>
        <div
          className="overflow-hidden rounded-full"
          style={{ width: 90, height: 5, background: T.trackBg }}
        >
          <div
            className="h-full rounded-full transition-[width] duration-300"
            style={{ width: `${ratio}%`, background: T.primary }}
          />
        </div>
      </div>

      <ol className="relative m-0 list-none p-0">
        <span
          aria-hidden
          className="absolute"
          style={{
            left: 11,
            top: 12,
            bottom: 12,
            width: 1.5,
            background: `linear-gradient(180deg, ${T.primary} 0%, ${T.primary} ${ratio}%, ${T.connectorRest} ${ratio}%, ${T.connectorRest} 100%)`,
          }}
        />
        {items.map((todo) => (
          <li
            key={todo.id}
            className="relative flex items-center gap-3"
            style={{ padding: '6px 0' }}
          >
            <PhaseDot status={todo.status} />
            <span
              className="flex-1 text-[13.5px]"
              style={{
                color: todo.status === 'pending' ? T.mutedSoft : T.ink,
                fontWeight: todo.status === 'in_progress' ? 600 : 500,
                letterSpacing: '-0.005em',
              }}
            >
              <span
                className="mr-2 text-[11px] font-medium tabular-nums"
                style={{ color: T.muted }}
              >
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
  const latestId = useAssistantState((s) => {
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
