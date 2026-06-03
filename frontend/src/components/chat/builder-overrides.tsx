'use client'

import { useMemo } from 'react'
import Image from 'next/image'
import Markdown from 'react-markdown'
import { useTranslations } from 'next-intl'
import {
  MessagePrimitive,
  ComposerPrimitive,
  ThreadPrimitive,
  useAui,
  useAssistantState,
  useMessagePartText,
} from '@assistant-ui/react'
import { LayoutGridIcon, PaperclipIcon, SendIcon } from 'lucide-react'

import { buildMarkdownComponents } from './markdown-content'
import { CHAT_FINAL_REMARK_PLUGINS } from './markdown-plugins'
import { ToolFallbackPanel } from './tool-ui/generic-tool-ui'
import { parsePhaseNarration, type PhaseSegment } from './builder-phase-parser'
import { SystemEventChip } from './system-event-chip'
import { ImeSafeComposerInput } from './ime-safe-composer-input'
import {
  BuilderAssistantName,
  BuilderAssistantSubtitle,
  BuilderEditComposer,
  BuilderIconButton,
  BuilderMessageBubble,
  BuilderMessageText,
} from './tool-ui/builder-primitives'

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
  return (
    <div className="flex justify-end">
      <div className="flex w-full max-w-[72%] flex-col items-end">
        <ComposerPrimitive.Root asChild>
          <BuilderEditComposer>
            <ImeSafeComposerInput className="moldy-builder-edit-input" autoFocus />
            <div className="flex items-center justify-end gap-1">
              <ComposerPrimitive.Cancel className="moldy-builder-button moldy-builder-button-ghost h-7 px-2">
                {t('editCancel')}
              </ComposerPrimitive.Cancel>
              <ComposerPrimitive.Send className="moldy-builder-button moldy-builder-button-primary h-7 px-2">
                {t('editSave')}
              </ComposerPrimitive.Send>
            </div>
          </BuilderEditComposer>
        </ComposerPrimitive.Root>
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
  const isRunning = useAssistantState(
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

const BUILDER_PART_COMPONENTS = {
  Text: BuilderAssistantTextPart,
  tools: { Fallback: BuilderToolFallback },
} as const

/** Builder Assistant 메시지 본문 — parts 사이에 12px gap stack. */
export function BuilderAssistantMessageParts() {
  return (
    <div className="flex flex-col gap-3">
      <MessagePrimitive.Content components={BUILDER_PART_COMPONENTS} />
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
        unoptimized
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
    <BuilderIconButton
      aria-label={label}
      title={label}
    >
      {children}
    </BuilderIconButton>
  )
}

/** Stop 버튼 — 진행 중 응답 취소 (AbortController 경로). */
function BuilderStopButton() {
  const tMsg = useTranslations('chat.message')
  const aui = useAui()
  const handleStop = () => {
    try {
      aui.thread().cancelRun()
    } catch (err) {
      console.warn('[BuilderStopButton] cancelRun error:', err)
    }
  }
  return (
    <button
      type="button"
      onClick={handleStop}
      aria-label={tMsg('stop')}
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
        <ComposerPrimitive.Root
          className="moldy-builder-composer-root group"
        >
          <ImeSafeComposerInput
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
              {modelLabel && (
                <span className="moldy-builder-model-label">{modelLabel}</span>
              )}
            </div>
            <ThreadPrimitive.If running={false}>
              <BuilderSendButton />
            </ThreadPrimitive.If>
            <ThreadPrimitive.If running={true}>
              <BuilderStopButton />
            </ThreadPrimitive.If>
          </div>
        </ComposerPrimitive.Root>
      </div>
    </div>
  )
}
