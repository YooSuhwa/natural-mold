'use client'

import { useEffect, useMemo, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useStore } from 'jotai'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { parseExcludedCapabilities } from '@/lib/chat/excluded-capabilities'
import { memoryKeys } from '@/lib/hooks/use-memory'
import { currentOrgScope, isOrgScopeCurrent } from '@/lib/org-scope/org-scope-signal'
import { conversationQueryKeys } from '@/lib/query-keys/conversations'
import {
  chatArtifactsAtom,
  selectChatArtifactAtom,
  setConversationArtifactsAtom,
  upsertArtifactList,
} from '@/lib/stores/chat-artifacts'
import {
  chatExcludedCapabilitiesAtom,
  type ExcludedCapabilityGroup,
} from '@/lib/stores/chat-excluded-capabilities'
import {
  chatMemoryRecallAtom,
  setConversationMemoryRecallAtom,
  type RecalledMemoryBrief,
} from '@/lib/stores/chat-memory-recall'
import { chatRightRailAtom, type RightRailState } from '@/lib/stores/chat-right-rail'
import { chatSkillDraftBriefAtom, chatSkillValidationAtom } from '@/lib/stores/chat-skill-builder'
import { latestTurnUsageAtom, sessionTokenUsageAtom } from '@/lib/stores/chat-store'
import { chatSubagentNamesAtom } from '@/lib/stores/chat-subagent-names'
import type { Message, TokenUsageBreakdown } from '@/lib/types'
import { useMoldyAgUiFileActivities } from './activity-file-hydration'
import {
  fileActivityPointer,
  fileActivitySummary,
  isAgentMemoProposal,
  memoryChangeActivity,
  recallActivity,
  skillDraftActivity,
  skillValidationActivity,
  subagentNamesActivity,
  usageActivityBreakdown,
} from './activity-values'
import type { AgUiActivitySnapshot } from './chat-history-types'

interface ActivityEffectOptions {
  readonly runId?: string | null
  readonly liveActivityIds?: ReadonlySet<string>
  readonly serverMessages?: readonly Message[]
}

const NO_MESSAGES: readonly Message[] = []
const MEMORY_TOASTS = {
  memory_proposed: ['info', 'proposedToast'],
  memory_saved: ['success', 'savedToast'],
  memory_rejected: ['warning', 'rejectedToast'],
  memory_deleted: ['success', 'deletedToast'],
} as const

function readField(content: Record<string, unknown>, key: string): string | undefined {
  const value = content[key]
  return typeof value === 'string' && value.length > 0 ? value : undefined
}

function belongsToConversation(activity: AgUiActivitySnapshot, conversationId: string): boolean {
  const owner = readField(activity.content, 'conversation_id')
  return owner === undefined || owner === conversationId
}

// The REST envelope uses public row IDs. Model usage events usually identify that
// same row by the provider's message ID. An ambiguous provider ID cannot join rows.
function scopedUsage(
  conversationId: string,
  activities: readonly AgUiActivitySnapshot[],
  messages: readonly Message[],
  currentRunId: string | undefined,
) {
  const publicRows = new Map<string, number>()
  const runtimeRows = new Map<string, string | null>()
  const usageByCall = new Map<string, TokenUsageBreakdown>()
  let latestOrder = -1
  let latestKey: string | undefined
  for (const [order, row] of messages.entries()) {
    if (row.role !== 'assistant' || row.conversation_id !== conversationId) continue
    publicRows.set(row.id, order)
    if (row.runtime_message_id) {
      const prior = runtimeRows.get(row.runtime_message_id)
      runtimeRows.set(
        row.runtime_message_id,
        prior === undefined || prior === row.id ? row.id : null,
      )
    }
    const usage = usageActivityBreakdown(row.usage)
    if (!usage) continue
    const key = JSON.stringify(['row', row.id])
    usageByCall.set(key, usage)
    latestOrder = order
    latestKey = key
  }
  for (const [index, activity] of activities.entries()) {
    if (
      !activity.physicalRunId ||
      activity.activityType !== 'moldy.usage' ||
      !belongsToConversation(activity, conversationId)
    )
      continue
    const usage = usageActivityBreakdown(activity.content)
    if (!usage) continue
    const runtimeId = readField(activity.content, 'assistant_msg_id')
    const claimedId = readField(activity.content, 'public_message_id')
    const row = !activity.subagentRunId
      ? claimedId && publicRows.has(claimedId)
        ? claimedId
        : runtimeRows.get(runtimeId ?? '')
      : undefined
    const key = row
      ? JSON.stringify(['row', row])
      : JSON.stringify([
          'call',
          activity.physicalRunId,
          activity.subagentRunId,
          runtimeId ?? activity.messageId,
        ])
    usageByCall.set(key, { ...usageByCall.get(key), ...usage })
    if (activity.subagentRunId) continue
    const order = row ? publicRows.get(row) : undefined
    if (order === undefined && currentRunId && activity.physicalRunId !== currentRunId) continue
    const position = order ?? messages.length + index
    if (position >= latestOrder) {
      latestOrder = position
      latestKey = key
    }
  }
  const totals = { inputTokens: 0, outputTokens: 0, cost: 0 }
  for (const usage of usageByCall.values()) {
    totals.inputTokens += usage.prompt_tokens
    totals.outputTokens += usage.completion_tokens
    totals.cost += usage.estimated_cost ?? 0
  }
  return { totals, latest: latestKey ? (usageByCall.get(latestKey) ?? null) : null }
}

// All values compared here have been decoded to bounded public presentation fields.
function sameValue(left: unknown, right: unknown): boolean {
  return left === right || JSON.stringify(left) === JSON.stringify(right)
}

export function useMoldyAgUiActivityEffects(
  conversationId: string,
  wireActivities: readonly AgUiActivitySnapshot[],
  { runId, liveActivityIds, serverMessages = NO_MESSAGES }: ActivityEffectOptions = {},
): void {
  const store = useStore()
  const client = useQueryClient()
  const activities = useMoldyAgUiFileActivities(conversationId, wireActivities)
  const tMemory = useTranslations('chat.memory')
  const scope = useMemo(
    () => ({
      conversationId,
      org: currentOrgScope(),
      seen: new Map<string, string>(),
      previewed: new Map<string, string>(),
      dismissedRuns: new Set<string>(),
    }),
    [conversationId],
  )
  const automaticPreview = useRef<{ conversationId: string; state: RightRailState } | null>(null)
  const latestRun =
    runId ??
    activities.findLast(
      (activity) =>
        !activity.subagentRunId &&
        belongsToConversation(activity, conversationId) &&
        activity.physicalRunId,
    )?.physicalRunId
  const usage = useMemo(
    () => scopedUsage(conversationId, activities, serverMessages, latestRun),
    [activities, conversationId, latestRun, serverMessages],
  )
  useEffect(() => {
    if (!conversationId || !isOrgScopeCurrent(scope.org)) return
    if (!sameValue(store.get(sessionTokenUsageAtom), usage.totals))
      store.set(sessionTokenUsageAtom, usage.totals)
    if (!sameValue(store.get(latestTurnUsageAtom), usage.latest))
      store.set(latestTurnUsageAtom, usage.latest)
  }, [conversationId, scope, store, usage])

  useEffect(() => {
    if (!conversationId || !isOrgScopeCurrent(scope.org)) return
    const previousExclusions = store.get(chatExcludedCapabilitiesAtom)[conversationId]
    if (previousExclusions?.notice?.dismissed)
      scope.dismissedRuns.add(previousExclusions.notice.runKey)
    let exclusions: readonly ExcludedCapabilityGroup[] | null = null
    const names: Record<string, string> = Object.create(null)
    let recalls: readonly RecalledMemoryBrief[] = []
    let draft: ReturnType<typeof skillDraftActivity> = null
    let validation: ReturnType<typeof skillValidationActivity> = null
    let memoriesChanged = false
    let steersChanged = false
    for (const activity of activities) {
      if (!belongsToConversation(activity, conversationId)) continue
      const { content, activityType, messageId, physicalRunId, subagentRunId } = activity
      const key = JSON.stringify([physicalRunId, subagentRunId, messageId, activityType])
      const current = !!physicalRunId && physicalRunId === latestRun
      const fresh = current && liveActivityIds?.has(messageId) === true
      const changed = (value: unknown) => {
        const fingerprint = JSON.stringify(value)
        if (scope.seen.get(key) === fingerprint) return false
        scope.seen.set(key, fingerprint)
        return true
      }
      if (activityType === 'moldy.file_event') {
        const pointer = fileActivityPointer(content)
        if (!pointer) continue
        const items = store.get(chatArtifactsAtom)[conversationId]?.items ?? []
        const previous = items.find((item) => item.id === pointer.id)
        const file = fileActivitySummary(content, conversationId)
        if (!file) {
          changed(null)
          if (
            previous &&
            previous.version_id === pointer.version_id &&
            previous.run_id === physicalRunId
          ) {
            store.set(setConversationArtifactsAtom, {
              conversationId,
              items: items.filter((item) => item.id !== previous.id),
            })
          }
          continue
        }
        if (file.run_id !== physicalRunId || !changed(file)) continue
        if (previous && previous.version_number > file.version_number) continue
        store.set(setConversationArtifactsAtom, {
          conversationId,
          items: upsertArtifactList(items, file),
        })
        const rail = store.get(chatRightRailAtom)
        const followed =
          automaticPreview.current?.conversationId === conversationId
            ? automaticPreview.current.state
            : null
        const firstLiveVersion = fresh && scope.previewed.get(key) !== file.version_id
        if (fresh) scope.previewed.set(key, file.version_id)
        if (
          firstLiveVersion &&
          ((rail.mode === 'none' && followed === null) || rail === followed)
        ) {
          const preview: RightRailState = {
            mode: 'artifacts',
            artifacts: { conversationId, selectedArtifactId: file.id, view: 'preview' },
          }
          automaticPreview.current = { conversationId, state: preview }
          store.set(selectChatArtifactAtom, { conversationId, artifactId: file.id })
          store.set(chatRightRailAtom, preview)
        }
      } else if (activityType === 'moldy.run_steer') {
        const steerId = readField(content, 'steer_id')
        const status = readField(content, 'status')
        if (
          steerId &&
          status &&
          content.conversation_id === conversationId &&
          physicalRunId &&
          content.run_id === physicalRunId &&
          ['pending', 'applied', 'queued', 'needs_review'].includes(status) &&
          changed([steerId, status])
        )
          steersChanged = true
      } else if (activityType === 'moldy.subagent_names') {
        Object.assign(names, subagentNamesActivity(content))
      } else if (activityType === 'moldy.memory_recalled') {
        if (current && !subagentRunId) recalls = recallActivity(content) ?? []
      } else if (activityType === 'moldy.skill_draft') {
        if (!subagentRunId) draft = skillDraftActivity(content) ?? draft
      } else if (activityType === 'moldy.skill_validation') {
        if (!subagentRunId) validation = skillValidationActivity(content) ?? validation
      } else if (activityType === 'moldy.excluded_capabilities' && current && !subagentRunId) {
        exclusions = parseExcludedCapabilities(content) ?? exclusions
      } else if (activityType === 'moldy.agent_memo_proposed') {
        if (isAgentMemoProposal(content) && changed(content.id)) memoriesChanged = true
      } else {
        const change = memoryChangeActivity(activityType, content)
        if (!change) continue
        if (change.name === 'memory_deleted' && change.id) {
          recalls = recalls.filter((memory) => memory.id !== change.id)
        }
        if (!changed(change)) continue
        memoriesChanged = true
        if (fresh) {
          const [method, label] = MEMORY_TOASTS[change.name]
          toast[method](tMemory(label))
        }
      }
    }
    if (latestRun) {
      const runs = [
        ...(previousExclusions?.runs ?? []).filter((id) => id !== latestRun),
        latestRun,
      ].slice(-50)
      const notice = exclusions
        ? { runKey: latestRun, groups: exclusions, dismissed: scope.dismissedRuns.has(latestRun) }
        : null
      const next = { runs, notice }
      if (!sameValue(previousExclusions, next))
        store.set(chatExcludedCapabilitiesAtom, (all) => ({ ...all, [conversationId]: next }))
    }
    if (memoriesChanged)
      void client.invalidateQueries({ queryKey: memoryKeys.all }, { cancelRefetch: false })
    if (steersChanged)
      void client.invalidateQueries(
        { queryKey: conversationQueryKeys.steers(conversationId), exact: true },
        { cancelRefetch: false },
      )
    if (!sameValue(store.get(chatMemoryRecallAtom)[conversationId] ?? [], recalls)) {
      store.set(setConversationMemoryRecallAtom, { conversationId, memories: recalls })
    }
    if (!sameValue(store.get(chatSubagentNamesAtom)[conversationId] ?? {}, names)) {
      store.set(chatSubagentNamesAtom, (state) => ({ ...state, [conversationId]: names }))
    }
    if (!sameValue(store.get(chatSkillDraftBriefAtom)[conversationId] ?? null, draft)) {
      store.set(chatSkillDraftBriefAtom, (state) => {
        const next = { ...state }
        if (draft) next[conversationId] = draft
        else delete next[conversationId]
        return next
      })
    }
    if (!sameValue(store.get(chatSkillValidationAtom)[conversationId] ?? null, validation)) {
      store.set(chatSkillValidationAtom, (state) => {
        const next = { ...state }
        if (validation) next[conversationId] = validation
        else delete next[conversationId]
        return next
      })
    }
  }, [activities, client, conversationId, latestRun, liveActivityIds, scope, store, tMemory])
}
