'use client'

import { NetworkIcon } from 'lucide-react'
import { useAtomValue, useSetAtom } from 'jotai'
import { useTranslations } from 'next-intl'
import type { SubagentDiscoverySnapshot } from '@langchain/react'
import { useChatConversationId } from '@/components/chat/conversation-context'
import {
  summarizeSubagentProgress,
  useSubagentSnapshots,
} from '@/lib/chat/langgraph-runtime/subagent-runtime'
import { chatRightRailAtom } from '@/lib/stores/chat-right-rail'
import { chatSubagentNamesAtom, resolveSubagentDisplayName } from '@/lib/stores/chat-subagent-names'
import { cn } from '@/lib/utils'

/**
 * SubagentTeamStrip — 이번 대화에서 위임된 서브에이전트 팀을 한 줄로 보여주는
 * 상시 스트립. MissionControlBar(계획)와 같은 sticky 헤더 영역에 살며,
 * 스트리밍 중·종료 후·리로드 후 모두 유지된다(SDK discovery가 thread state로
 * re-seed). 칩 클릭은 우측 레일의 per-subagent 상세 패널을 연다 — 인라인
 * SubagentCard의 openRail 계약을 그대로 재사용한다.
 *
 * 데이터는 `useSubagentSnapshots()`(SubagentRuntimeProvider) 하나만 쓰고,
 * 표시명은 `moldy.subagent_names` 맵으로 렌더 시점에만 치환한다(G10-A 계약 —
 * SDK 스냅샷/checkpoint는 불변).
 */

const STATUS_DOT_CLASS: Record<SubagentDiscoverySnapshot['status'], string> = {
  running: 'bg-status-info animate-pulse',
  complete: 'bg-status-success',
  error: 'bg-status-danger',
}

function TeamChip({
  snapshot,
  displayName,
  onOpen,
}: {
  readonly snapshot: SubagentDiscoverySnapshot
  readonly displayName: string
  readonly onOpen: () => void
}) {
  const t = useTranslations('chat.teamStrip')
  return (
    <button
      type="button"
      onClick={onOpen}
      title={snapshot.taskInput || displayName}
      aria-label={t('chipLabel', { name: displayName })}
      data-moldy-team-chip={snapshot.status}
      className={cn(
        'inline-flex max-w-48 shrink-0 items-center gap-1.5 rounded-full border border-border/60',
        'bg-background px-2.5 py-0.5 moldy-ui-caption text-foreground/85 transition-colors',
        'hover:bg-accent hover:text-foreground',
      )}
    >
      {/* SDK depth는 root=0, 직접 위임=1 — 서브의 서브(≥2)만 중첩 마커. */}
      {snapshot.depth > 1 ? (
        <span aria-hidden className="shrink-0 text-muted-foreground">
          ↳
        </span>
      ) : null}
      <span
        aria-hidden
        className={cn('size-1.5 shrink-0 rounded-full', STATUS_DOT_CLASS[snapshot.status])}
      />
      <span className="truncate">{displayName}</span>
    </button>
  )
}

export function SubagentTeamStrip() {
  const t = useTranslations('chat.teamStrip')
  const snapshots = useSubagentSnapshots()
  const conversationId = useChatConversationId()
  const subagentNames = useAtomValue(chatSubagentNamesAtom)
  const setRail = useSetAtom(chatRightRailAtom)

  if (snapshots.length === 0) return null

  const names = conversationId ? subagentNames[conversationId] : undefined
  const summary = summarizeSubagentProgress(snapshots)

  return (
    <div
      className="border-b border-border/60 bg-background/95 px-4 py-1.5"
      data-moldy-team-strip="true"
    >
      <div className="mx-auto flex w-full max-w-3xl items-center gap-2">
        <span className="flex shrink-0 items-center gap-1.5 moldy-ui-caption font-medium text-muted-foreground">
          <NetworkIcon aria-hidden className="size-3.5" />
          {t('title')}
        </span>
        <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto">
          {snapshots.map((snapshot) => {
            const displayName = resolveSubagentDisplayName(names, snapshot.name)
            return (
              <TeamChip
                key={snapshot.id}
                snapshot={snapshot}
                displayName={displayName}
                onOpen={() =>
                  setRail({
                    mode: 'subagent',
                    subagent: {
                      conversationId,
                      toolCallId: snapshot.id,
                      agentName: displayName,
                      input: snapshot.taskInput ?? '',
                    },
                  })
                }
              />
            )
          })}
        </div>
        <span className="shrink-0 moldy-ui-caption text-muted-foreground">
          {summary.running > 0
            ? t('runningMeta', { count: summary.running })
            : t('doneMeta', { done: summary.completed + summary.failed, total: summary.total })}
        </span>
      </div>
    </div>
  )
}
