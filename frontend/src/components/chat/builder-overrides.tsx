'use client'

import { useMemo, type ReactNode } from 'react'
import Image from 'next/image'
import Markdown from 'react-markdown'
import { useTranslations } from 'next-intl'
import {
  AuiIf,
  MessagePrimitive,
  ComposerPrimitive,
  useAui,
  useAuiState,
  useMessagePartText,
  type EnrichedPartState,
} from '@assistant-ui/react'
import { LayoutGridIcon, PaperclipIcon, SendIcon } from 'lucide-react'
import { useAtomValue } from 'jotai'

import { buildMarkdownComponents } from './markdown-components'
import { CHAT_FINAL_REMARK_PLUGINS } from './markdown-final-plugins'
import { ToolFallbackPanel } from './tool-ui/generic-tool-ui'
import { ToolGroupContainer } from './tool-ui/tool-group-container'
import {
  groupAssistantParts,
  isGroupToolNode,
  groupToolName,
  type GroupedRenderInfo,
} from '@/lib/chat/group-assistant-parts'
import { parsePhaseNarration, type PhaseSegment } from './builder-phase-parser'
import { SystemEventChip } from './system-event-chip'
import { ImeSafeComposerInput } from './ime-safe-composer-input'
import {
  BuilderAssistantName,
  BuilderAssistantSubtitle,
  BuilderIconButton,
  BuilderMessageBubble,
  BuilderMessageText,
} from './tool-ui/builder-primitives'
import {
  MessageEditComposerInput,
  MessageEditComposerRoot,
  useMessageEditComposerControls,
} from './message-edit-composer'
import { chatCancelInFlightAtom } from '@/lib/stores/chat-store'
import { reportClientWarning } from '@/lib/logging/client-logger'

/**
 * Builder-variant 메시지/컴포저 오버라이드.
 *
 * AssistantThread가 `variant="builder"`일 때만 사용. 기본 variant 동작은 그대로 둠.
 * - User: bubble-only (no avatar), mint bubble with tail (designer-directed)
 * - Assistant: bare 38×38 mascot (no chip), "Moldy · 에이전트 빌더" name row
 * - Composer: mint focus ring, 파일/템플릿 IconBtn, 모델 메타, Send ↔ Stop 토글
 */

const MASCOT_SRC = '/moldy-mascot.webp'

/** User 메시지 — 아바타 없음 + mint bubble. */
export function BuilderUserMessage({ metaRow }: { metaRow: React.ReactNode }) {
  return (
    <div className="group relative flex justify-end">
      <div className="flex w-full max-w-[72%] flex-col items-end">
        <BuilderMessageBubble>
          <MessagePrimitive.Content />
        </BuilderMessageBubble>
        {metaRow}
      </div>
    </div>
  )
}

export function BuilderUserEditComposer() {
  const t = useTranslations('chat.message')
  const { canCancel, canSend, cancel } = useMessageEditComposerControls()
  return (
    <div className="flex justify-end">
      <div className="flex w-full max-w-[72%] flex-col items-end">
        <MessageEditComposerRoot className="moldy-builder-edit-composer">
          <MessageEditComposerInput className="moldy-builder-edit-input" autoFocus />
          <div className="flex items-center justify-end gap-1">
            <button
              type="button"
              disabled={!canCancel}
              onClick={cancel}
              className="moldy-builder-button moldy-builder-button-ghost h-7 px-2 disabled:pointer-events-none disabled:opacity-50"
            >
              {t('editCancel')}
            </button>
            <button
              type="submit"
              disabled={!canSend}
              className="moldy-builder-button moldy-builder-button-primary h-7 px-2 disabled:pointer-events-none disabled:opacity-50"
            >
              {t('editSave')}
            </button>
          </div>
        </MessageEditComposerRoot>
      </div>
    </div>
  )
}

const MARKDOWN_COMPONENTS_STREAMING = buildMarkdownComponents({ isStreaming: true })
const MARKDOWN_COMPONENTS_FINAL = buildMarkdownComponents({ isStreaming: false })

/** Builder 전용 text part — phase narration을 SystemEventChip으로 변환. */
function BuilderAssistantTextPart() {
  const tPhase = useTranslations('chat.phaseTimeline')
  const part = useMessagePartText()
  const isRunning = useAuiState(
    (s) => (s.message?.status as { type?: string } | undefined)?.type === 'running',
  )
  const text = part?.text ?? ''
  const segments = useMemo<PhaseSegment[]>(() => parsePhaseNarration(text), [text])
  const components = isRunning ? MARKDOWN_COMPONENTS_STREAMING : MARKDOWN_COMPONENTS_FINAL

  if (segments.length === 0) return null

  return (
    <div className="flex flex-col gap-3">
      {segments.map((seg, idx) => {
        if (seg.kind === 'event') {
          const name =
            tPhase(`names.${seg.phaseId}`) || tPhase('phaseNameFallback', { phaseId: seg.phaseId })
          const status =
            seg.transition === 'completed' ? tPhase('completedLabel') : tPhase('startedLabel')
          const label = tPhase('phaseLabel', { phaseId: seg.phaseId, status })
          return (
            <SystemEventChip
              key={`evt-${idx}`}
              kind={seg.transition}
              label={label}
              sublabel={name}
            />
          )
        }
        return (
          <BuilderMessageText key={`txt-${idx}`}>
            <Markdown components={components} remarkPlugins={CHAT_FINAL_REMARK_PLUGINS}>
              {seg.text}
            </Markdown>
          </BuilderMessageText>
        )
      })}
    </div>
  )
}

/** Builder 전용 ToolFallback wrapper — 시각 표시는 기본 ToolFallback과 동일하게 유지.
 *
 * BUILDER_TOOL_UI에 등록된 tool들(phase_timeline / ask_user / recommendation_approval
 * 등)은 자체 ToolUI가 인터셉트하므로 이 fallback에 닿지 않는다. 안전망으로 기본
 * ToolFallbackPanel을 그대로 사용해 모르는 도구도 화면에 표시되게 한다. */
function BuilderToolFallback(props: {
  toolName: string
  args: Record<string, unknown>
  result?: unknown
  status: { type: string }
}) {
  const resolved =
    props.status.type === 'running'
      ? ('running' as const)
      : props.status.type === 'complete'
        ? ('complete' as const)
        : ('error' as const)
  return (
    <ToolFallbackPanel
      toolName={props.toolName}
      args={props.args}
      result={props.result}
      status={resolved}
    />
  )
}

// ── 빌더 표면 tool-call 그룹핑 (메인 v3와 공유하는 GroupedParts) ───────────
//
// groupBy/노드 판별은 `group-assistant-parts.ts`에서 메인 v3 채팅과 공유한다.
// leaf 비주얼만 빌더 전용으로 분기: 텍스트는 phase-narration을 SystemEventChip으로
// 바꾸는 `BuilderAssistantTextPart`, 도구 박스는 등록된 per-tool UI(leaf.toolUI) →
// 없으면 `BuilderToolFallback`. 메인 v3와 달리 order 재배치 없이 자연 순서를 쓰며,
// 묶는 비주얼만 `ToolGroupContainer`(검색류면 출처 집계까지)로 통일한다.

/** GroupedParts의 노드/leaf를 빌더 톤으로 그린다. group-tool 노드는 N≥2면 컨테이너,
 * N=1이면 패스스루. text leaf는 phase-narration 보존, tool-call leaf는 등록 UI 우선. */
export function renderBuilderGroupedPart({ part, children }: GroupedRenderInfo): ReactNode {
  if (isGroupToolNode(part)) {
    const running = part.status?.type === 'running'
    // N=1은 컨테이너 없이 그룹 내부(단일 tool-call leaf)를 그대로 통과.
    if (part.indices.length < 2) {
      return children
    }
    // running→펼침/done→접힘은 key remount로 달성(CollapsiblePill은 uncontrolled).
    return (
      <ToolGroupContainer
        key={running ? 'running' : 'done'}
        toolName={groupToolName(part)}
        count={part.indices.length}
        running={running}
        indices={part.indices}
      >
        {children}
      </ToolGroupContainer>
    )
  }

  switch (part.type) {
    case 'text':
      return <BuilderAssistantTextPart />
    case 'tool-call': {
      // 등록된 per-tool UI(BUILDER_TOOL_UI)는 leaf.toolUI로 흐른다. 미등록 도구는
      // BuilderToolFallback이 안전망으로 표시 — 기존 tools.Fallback 동작과 동일.
      const leaf = part as Extract<EnrichedPartState, { type: 'tool-call' }>
      return (
        leaf.toolUI ?? (
          <BuilderToolFallback
            toolName={leaf.toolName}
            args={leaf.args as Record<string, unknown>}
            result={leaf.result}
            status={leaf.status}
          />
        )
      )
    }
    case 'data':
      // 빌더는 dataUI를 등록하지 않아 보통 undefined(=렌더 없음). 메인 v3와 동일하게
      // 등록된 data renderer가 있으면 그대로 위임.
      return (part as Extract<EnrichedPartState, { type: 'data' }>).dataRendererUI
    case 'indicator':
      // indicator="never"라 발화하지 않지만 방어적으로 null.
      return null
    default:
      // image/file/source/reasoning 등: 빌더는 Text/tool만 렌더했으므로 기본 null.
      return null
  }
}

/** Builder Assistant 메시지 본문 — parts 사이에 12px gap stack. 연속 같은 도구는
 * 메인 v3와 동일하게 1개 그룹 컨테이너로 묶는다(GroupedParts). */
export function BuilderAssistantMessageParts() {
  return (
    <div className="flex flex-col gap-3">
      <MessagePrimitive.GroupedParts groupBy={groupAssistantParts} indicator="never">
        {renderBuilderGroupedPart}
      </MessagePrimitive.GroupedParts>
    </div>
  )
}

/** Assistant 메시지 — 38×38 bare mascot + 이름줄. */
export function BuilderAssistantMessage({
  children,
  metaRow,
  agentSubtitle,
}: {
  /** 메시지 본문 (MessageMetaRow 포함 X — metaRow는 별도). */
  children: React.ReactNode
  metaRow: React.ReactNode
  agentSubtitle?: string
}) {
  const t = useTranslations('agent.conversational')
  const resolvedAgentSubtitle = agentSubtitle ?? t('builderAgentSubtitle')
  return (
    <div className="group relative flex items-start gap-3">
      <Image
        src={MASCOT_SRC}
        alt={t('builderAgentName')}
        width={38}
        height={38}
        className="shrink-0"
      />
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex items-baseline gap-1.5">
          <BuilderAssistantName>{t('builderAgentName')}</BuilderAssistantName>
          <BuilderAssistantSubtitle>{resolvedAgentSubtitle}</BuilderAssistantSubtitle>
        </div>
        {children}
        {metaRow}
      </div>
    </div>
  )
}

/** 좌측 툴바 IconBtn — 파일첨부 / 템플릿 (시각 stub, 클릭 시 title 표시만). */
function IconBtn({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <BuilderIconButton aria-label={label} title={label}>
      {children}
    </BuilderIconButton>
  )
}

/** Stop 버튼 — 진행 중 응답 취소 (AbortController 경로). */
function BuilderStopButton() {
  const tMsg = useTranslations('chat.message')
  const aui = useAui()
  const isCanceling = useAtomValue(chatCancelInFlightAtom)
  const handleStop = () => {
    if (isCanceling) return
    try {
      aui.thread().cancelRun()
    } catch (err) {
      reportClientWarning('BuilderStopButton', 'cancelRun error:', err)
    }
  }
  return (
    <button
      type="button"
      onClick={handleStop}
      disabled={isCanceling}
      aria-label={tMsg('stop')}
      data-moldy-stop-button="true"
      className="moldy-builder-stop"
    >
      <span aria-hidden className="moldy-builder-stop-mark" />
      {tMsg('stop')}
    </button>
  )
}

/** Send 버튼 — 32×32 민트 square. */
function BuilderSendButton() {
  const t = useTranslations('chat.input')
  return (
    <ComposerPrimitive.Send
      className="moldy-builder-send disabled:cursor-not-allowed"
      aria-label={t('sendButton')}
    >
      <SendIcon className="size-3.5" />
    </ComposerPrimitive.Send>
  )
}

/** Builder 전용 Composer.
 *
 * Spec:
 *  - Outer padding 12/28/18, gradient bg (transparent → #fafafa)
 *  - Card: white, 16 radius, mint focus-within 4px box-shadow ring
 *  - ImeSafeComposerInput submitMode="enter" — Chrome/IME 조합 입력을 composer 상태에 즉시 동기화
 *  - Toolbar: 파일/템플릿 IconBtn(시각만) + 1×16 divider + 모델 메타 + Send/Stop 토글
 */
export function BuilderComposer({ modelLabel }: { modelLabel?: string }) {
  const t = useTranslations('chat.input')
  return (
    <div className="moldy-builder-composer-shell">
      <div className="moldy-builder-composer-inner mx-auto">
        <ComposerPrimitive.Root className="moldy-builder-composer-root group">
          <ImeSafeComposerInput
            autoFocus
            placeholder={t('placeholder')}
            submitMode="enter"
            className="moldy-builder-composer-input"
            rows={2}
          />
          <div className="moldy-builder-composer-toolbar flex items-center justify-between">
            <div className="flex items-center gap-1">
              <IconBtn label={t('attachComingSoon')}>
                <PaperclipIcon className="size-4" />
              </IconBtn>
              <IconBtn label={t('templateComingSoon')}>
                <LayoutGridIcon className="size-4" />
              </IconBtn>
              <span aria-hidden className="moldy-builder-composer-divider" />
              {modelLabel && <span className="moldy-builder-model-label">{modelLabel}</span>}
            </div>
            <AuiIf condition={(s) => !s.thread.isRunning}>
              <BuilderSendButton />
            </AuiIf>
            <AuiIf condition={(s) => s.thread.isRunning}>
              <BuilderStopButton />
            </AuiIf>
          </div>
        </ComposerPrimitive.Root>
      </div>
    </div>
  )
}
