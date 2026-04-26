'use client'

import { useMemo, useCallback } from 'react'
import { useExternalStoreRuntime, useExternalMessageConverter } from '@assistant-ui/react'
import type { ThreadMessageLike, useExternalMessageConverter as UEC } from '@assistant-ui/react'
import type {
  BuilderIntent,
  BuilderToolRecommendation,
  BuilderMiddlewareRecommendation,
  BuilderDraftConfig,
} from '@/lib/types'
import type { PhaseState } from '@/app/agents/new/conversational/_components/phase-timeline'
import { extractText } from './utils'

export interface BuilderState {
  userRequest: string
  buildStatus: 'idle' | 'building' | 'preview' | 'failed'
  phases: PhaseState[]
  intent: BuilderIntent | null
  tools: BuilderToolRecommendation[]
  middlewares: BuilderMiddlewareRecommendation[]
  draftConfig: BuilderDraftConfig | null
  errorMessage: string
}

/** 빌더 상태를 가상 ThreadMessageLike로 변환. */
function buildVirtualMessages(state: BuilderState): VirtualMessage[] {
  const msgs: VirtualMessage[] = []

  if (!state.userRequest || state.buildStatus === 'idle') return msgs

  // 1. 사용자 요청 → UserMessage
  msgs.push({
    _id: 'builder-user',
    role: 'user',
    content: state.userRequest,
  })

  // 2. 빌드 진행/결과 → AssistantMessage
  // 진행 중인 Phase를 텍스트로 요약
  const activePhases = state.phases.filter((p) => p.status !== 'pending')
  const phaseLines = activePhases.map((p) => {
    const status =
      p.status === 'completed'
        ? '✓'
        : p.status === 'active'
          ? '⟳'
          : p.status === 'failed'
            ? '✗'
            : '⚠'
    const summary = p.resultSummary ? ` — ${p.resultSummary}` : ''
    return `${status} Phase ${p.id}${summary}`
  })

  let summaryText = phaseLines.join('\n')

  if (state.intent) {
    summaryText += `\n\n**${state.intent.agent_name}** — ${state.intent.agent_description}`
  }

  if (state.errorMessage && state.buildStatus === 'failed') {
    summaryText += `\n\n⚠ ${state.errorMessage}`
  }

  if (summaryText) {
    msgs.push({
      _id: 'builder-progress',
      role: 'assistant',
      content: summaryText,
    })
  }

  return msgs
}

// Lightweight virtual message type (not the full backend Message)
interface VirtualMessage {
  _id: string
  role: 'user' | 'assistant'
  content: string
}

const convertVirtualMessage: UEC.Callback<VirtualMessage> = (msg): ThreadMessageLike => ({
  role: msg.role,
  id: msg._id,
  content: msg.content,
})

interface UseBuilderRuntimeOptions {
  state: BuilderState
  /** Composer에서 입력 시 호출 — handleBuild(text) */
  onSubmit: (request: string) => void
}

/**
 * 빌더 파이프라인 상태를 assistant-ui ExternalStoreRuntime으로 변환.
 *
 * - 사용자 요청 → UserMessage
 * - Phase 진행 → AssistantMessage (텍스트 요약)
 * - 빌더 페이지의 기존 _components는 Thread 외부에서 직접 렌더링
 */
export function useBuilderRuntime({ state, onSubmit }: UseBuilderRuntimeOptions) {
  const virtualMessages = useMemo(() => buildVirtualMessages(state), [state])

  const threadMessages = useExternalMessageConverter({
    callback: convertVirtualMessage,
    messages: virtualMessages,
    isRunning: state.buildStatus === 'building',
  })

  const onNew = useCallback(
    async (appendMessage: { content: readonly { type: string; text?: string }[] }) => {
      const text = extractText(appendMessage.content)
      if (text) onSubmit(text)
    },
    [onSubmit],
  )

  return useExternalStoreRuntime({
    messages: threadMessages,
    isRunning: state.buildStatus === 'building',
    onNew,
  })
}
