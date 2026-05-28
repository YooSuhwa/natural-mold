'use client'

import { useRef, useState } from 'react'
import { makeAssistantToolUI } from '@assistant-ui/react'
import {
  BlocksIcon,
  BookOpenIcon,
  CheckIcon,
  FolderIcon,
  PencilIcon,
  PlugIcon,
  WrenchIcon,
  XIcon,
} from 'lucide-react'
import { BUILDER_TOKENS as T } from './builder-tokens'
import { PhaseCard, PhaseCardFooter, PhaseCardHeader } from './phase-card'
import { useApprovalForm, type ApprovalFormState } from './use-approval-form'

type ItemKind = 'tool' | 'middleware'
type RowKind = 'tool' | 'mcp' | 'skill' | 'middleware'

interface ToolItem {
  tool_name?: string
  middleware_name?: string
  description?: string
  reason?: string
  path?: string
  kind?: 'tool' | 'mcp' | 'skill'
}

interface RecommendationArgs {
  phase: number
  title: string
  items: ToolItem[]
  summary?: string
  item_kind: ItemKind
}

const ROW_PATH_PREFIX: Record<RowKind, string> = {
  tool: 'tools',
  mcp: 'mcp',
  skill: 'skills',
  middleware: 'middlewares',
}

const ROW_ICON: Record<RowKind, typeof WrenchIcon> = {
  tool: WrenchIcon,
  mcp: PlugIcon,
  skill: BookOpenIcon,
  middleware: BlocksIcon,
}

function getItemName(item: ToolItem, kind: ItemKind): string {
  if (kind === 'tool') return item.tool_name ?? ''
  return item.middleware_name ?? ''
}

function resolveRowKind(item: ToolItem, cardKind: ItemKind): RowKind {
  if (cardKind === 'middleware') return 'middleware'
  return item.kind ?? 'tool'
}

function getItemPath(item: ToolItem, rowKind: RowKind): string {
  if (item.path) return item.path
  const name = rowKind === 'middleware' ? (item.middleware_name ?? '') : (item.tool_name ?? '')
  return `${ROW_PATH_PREFIX[rowKind]}/${name}.yaml`
}

function ToolRecommendationHeader({
  cardKind,
  title,
  count,
  phase,
}: {
  cardKind: ItemKind
  title: string
  count: number
  phase: number
}) {
  const HeaderIcon = cardKind === 'middleware' ? BlocksIcon : WrenchIcon
  return (
    <PhaseCardHeader>
      <span
        className="inline-flex shrink-0 items-center justify-center"
        style={{
          width: 22,
          height: 22,
          borderRadius: 7,
          background: T.primaryBg,
          color: T.primary,
        }}
      >
        <HeaderIcon className="size-3" />
      </span>
      <span
        className="text-[13.5px] font-semibold"
        style={{ color: T.ink, letterSpacing: '-0.01em' }}
      >
        {title}
      </span>
      <span className="text-[12px]" style={{ color: T.muted }}>
        · {count}개 · 검토 필요
      </span>
      <div className="flex-1" />
      <span
        className="text-[10.5px] font-semibold uppercase"
        style={{ color: T.primaryInk, letterSpacing: '0.04em' }}
      >
        PHASE {phase}
      </span>
    </PhaseCardHeader>
  )
}

function ToolRow({ item, cardKind }: { item: ToolItem; cardKind: ItemKind }) {
  const name = getItemName(item, cardKind)
  const rowKind = resolveRowKind(item, cardKind)
  const Icon = ROW_ICON[rowKind]
  const path = getItemPath(item, rowKind)

  return (
    <div
      className="flex gap-3 rounded-[10px] transition-colors"
      style={{ padding: '12px 10px' }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = T.surfaceAlt
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = 'transparent'
      }}
    >
      <span
        className="inline-flex shrink-0 items-center justify-center"
        style={{
          width: 34,
          height: 34,
          borderRadius: 9,
          background: T.primaryBg,
          color: T.primary,
        }}
      >
        <Icon className="size-4" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex items-center gap-2">
          <code className="font-mono text-[13.5px] font-semibold" style={{ color: T.ink }}>
            {name}
          </code>
          <span
            className="text-[10.5px] font-semibold"
            style={{
              padding: '1px 6px',
              borderRadius: 999,
              background: T.primaryBg,
              color: T.primaryInk,
              letterSpacing: '-0.005em',
            }}
          >
            추천
          </span>
        </div>
        <div className="mb-1.5 inline-flex items-center gap-1" style={{ color: T.muted }}>
          <FolderIcon className="size-2.5" />
          <code
            className="font-mono text-[11.5px]"
            style={{
              padding: '2px 7px',
              borderRadius: 5,
              background: T.borderSoft,
              border: `1px solid ${T.border}`,
              color: T.muted,
            }}
          >
            {path}
          </code>
        </div>
        {item.reason && (
          <p
            className="text-[13px]"
            style={{ color: T.ink2, lineHeight: 1.6, letterSpacing: '-0.005em' }}
          >
            <span className="mr-1.5 font-semibold" style={{ color: T.muted }}>
              사유
            </span>
            {item.reason}
          </p>
        )}
      </div>
    </div>
  )
}

function FeedbackTextarea({
  value,
  onChange,
  disabled,
}: {
  value: string
  onChange: (v: string) => void
  disabled: boolean
}) {
  const [focused, setFocused] = useState(false)
  const composingRef = useRef(false)

  return (
    <div style={{ padding: '12px 14px 4px' }}>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        onCompositionStart={() => {
          composingRef.current = true
        }}
        onCompositionEnd={() => {
          composingRef.current = false
        }}
        // Korean IME composition guard — Enter는 줄바꿈만, submit은 항상 버튼 클릭으로.
        // 그래도 Enter→submit 단축키를 미래에 붙일 때를 대비해 composingRef를 노출.
        onKeyDown={(e) => {
          if (e.key === 'Enter' && composingRef.current) {
            e.stopPropagation()
          }
        }}
        placeholder="추가하거나 빼고 싶은 도구, 변경 사유를 적어주세요"
        rows={2}
        disabled={disabled}
        className="w-full resize-none font-sans text-[13.5px] outline-none transition-[border-color,box-shadow] duration-150"
        style={{
          padding: '10px 12px',
          background: T.surface,
          border: `1px solid ${focused ? T.primaryDim : T.border}`,
          borderRadius: 9,
          color: T.ink,
          lineHeight: 1.55,
          letterSpacing: '-0.005em',
          boxShadow: focused ? T.focusShadow : 'none',
        }}
      />
    </div>
  )
}

function ApproveButton({
  onClick,
  disabled,
  submitted,
}: {
  onClick: () => void
  disabled: boolean
  submitted: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex items-center gap-1.5 text-[12.5px] font-semibold text-white transition-colors active:translate-y-px disabled:cursor-not-allowed disabled:opacity-60"
      style={{
        height: 32,
        padding: '0 16px',
        borderRadius: 9,
        background: T.primary,
        boxShadow: T.primaryShadow,
        letterSpacing: '-0.005em',
      }}
      onMouseEnter={(e) => {
        if (disabled) return
        e.currentTarget.style.background = T.primaryHover
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = T.primary
      }}
    >
      <CheckIcon className="size-3" strokeWidth={3} />
      {submitted ? '승인 완료' : '승인하고 진행'}
    </button>
  )
}

function RejectButton({ onClick, disabled }: { onClick: () => void; disabled: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex items-center gap-1.5 text-[12.5px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50"
      style={{
        height: 32,
        padding: '0 13px',
        borderRadius: 9,
        background: T.surface,
        border: `1px solid ${T.border}`,
        color: T.ink2,
        letterSpacing: '-0.005em',
      }}
      onMouseEnter={(e) => {
        if (disabled) return
        e.currentTarget.style.background = T.surfaceAlt
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = T.surface
      }}
    >
      <XIcon className="size-3" strokeWidth={2.5} />
      수정 요청
    </button>
  )
}

function FooterRow({
  form,
  feedbackOpen,
  setFeedbackOpen,
}: {
  form: ApprovalFormState
  feedbackOpen: boolean
  setFeedbackOpen: (v: boolean) => void
}) {
  const { submitted, isLocked, handleApprove, handleRevision } = form

  return (
    <div className="flex items-center justify-between" style={{ padding: '10px 14px' }}>
      <button
        type="button"
        onClick={() => setFeedbackOpen(!feedbackOpen)}
        disabled={isLocked}
        className="inline-flex items-center gap-1.5 text-[12.5px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50"
        style={{ color: T.muted, background: 'transparent', borderRadius: 6 }}
      >
        <PencilIcon className="size-3" />
        {feedbackOpen ? '의견 닫기' : '수정 의견 작성'}
      </button>
      <div className="flex items-center gap-2">
        <RejectButton onClick={handleRevision} disabled={isLocked} />
        <ApproveButton
          onClick={handleApprove}
          disabled={isLocked}
          submitted={submitted === 'approved'}
        />
      </div>
    </div>
  )
}

function RecommendationApproval({
  args,
  status,
}: {
  args: RecommendationArgs
  status: 'running' | 'complete' | 'incomplete' | 'requires-action'
}) {
  const form = useApprovalForm({
    isComplete: status === 'complete',
    approveDisplay: '승인',
    revisionFallback: '수정 요청',
  })
  const [feedbackOpen, setFeedbackOpen] = useState(false)

  const items = args.items ?? []
  const cardKind = args.item_kind ?? 'tool'
  const title = args.title || (cardKind === 'middleware' ? '미들웨어 추천' : '도구 추천')

  return (
    <div className="my-3">
      <PhaseCard
        header={
          <ToolRecommendationHeader
            cardKind={cardKind}
            title={title}
            count={items.length}
            phase={args.phase}
          />
        }
        footer={
          // 결정 끝나면 footer(수정 요청 / 승인하고 진행 버튼 + textarea) 전체를 unmount.
          // 카드는 header + body만 남아 결정된 시점 record로 frozen.
          form.isLocked ? null : (
            <PhaseCardFooter>
              {feedbackOpen && (
                <FeedbackTextarea
                  value={form.revision}
                  onChange={form.setRevision}
                  disabled={form.isLocked}
                />
              )}
              <FooterRow
                form={form}
                feedbackOpen={feedbackOpen}
                setFeedbackOpen={setFeedbackOpen}
              />
            </PhaseCardFooter>
          )
        }
      >
        <div style={{ padding: '6px 8px' }}>
          {items.length === 0 && (
            <p className="text-[13px]" style={{ padding: '12px 10px', color: T.muted }}>
              추천된 항목이 없습니다.
            </p>
          )}
          {items.map((item, idx) => (
            <ToolRow key={idx} item={item} cardKind={cardKind} />
          ))}
        </div>
        {args.summary && (
          <div
            className="text-[13px]"
            style={{
              padding: '0 18px 14px',
              color: T.ink2,
              lineHeight: 1.6,
              letterSpacing: '-0.005em',
            }}
          >
            {args.summary}
          </div>
        )}
      </PhaseCard>
    </div>
  )
}

export const RecommendationApprovalToolUI = makeAssistantToolUI<RecommendationArgs, unknown>({
  toolName: 'recommendation_approval',
  render: ({ args, status }) => <RecommendationApproval args={args} status={status.type} />,
})
