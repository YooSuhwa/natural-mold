'use client'

import { useRef, useState } from 'react'
import { makeAssistantToolUI } from '@assistant-ui/react'
import { useTranslations } from 'next-intl'
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
import {
  BuilderActionRow,
  BuilderBody,
  BuilderButton,
  BuilderFeedbackWrap,
  BuilderHeaderIcon,
  BuilderMuted,
  BuilderPath,
  BuilderPhaseLabel,
  BuilderRow,
  BuilderRowIcon,
  BuilderSummary,
  BuilderTag,
  BuilderTextarea,
  BuilderTitle,
} from './builder-primitives'
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
      <BuilderHeaderIcon>
        <HeaderIcon className="size-3" />
      </BuilderHeaderIcon>
      <BuilderTitle>{title}</BuilderTitle>
      <BuilderMuted>· {count}개 · 검토 필요</BuilderMuted>
      <div className="flex-1" />
      <BuilderPhaseLabel>PHASE {phase}</BuilderPhaseLabel>
    </PhaseCardHeader>
  )
}

function ToolRow({ item, cardKind }: { item: ToolItem; cardKind: ItemKind }) {
  const t = useTranslations('chat.recommendation')
  const name = getItemName(item, cardKind)
  const rowKind = resolveRowKind(item, cardKind)
  const Icon = ROW_ICON[rowKind]
  const path = getItemPath(item, rowKind)

  return (
    <BuilderRow>
      <BuilderRowIcon>
        <Icon className="size-4" />
      </BuilderRowIcon>
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex items-center gap-2">
          <code className="moldy-builder-code font-mono">{name}</code>
          <BuilderTag>{t('recommended')}</BuilderTag>
        </div>
        <div className="mb-1.5 inline-flex items-center gap-1 moldy-builder-color-muted">
          <FolderIcon className="size-2.5" />
          <BuilderPath>{path}</BuilderPath>
        </div>
        {item.reason && (
          <p className="moldy-builder-copy">
            <span className="mr-1.5 font-semibold moldy-builder-color-muted">{t('reason')}</span>
            {item.reason}
          </p>
        )}
      </div>
    </BuilderRow>
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
  const t = useTranslations('chat.recommendation')
  const composingRef = useRef(false)

  return (
    <BuilderFeedbackWrap>
      <BuilderTextarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
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
        placeholder={t('feedbackPlaceholder')}
        rows={2}
        disabled={disabled}
      />
    </BuilderFeedbackWrap>
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
  const t = useTranslations('chat.recommendation')
  return (
    <BuilderButton tone="primary" onClick={onClick} disabled={disabled} className="px-4">
      <CheckIcon className="size-3" strokeWidth={3} />
      {submitted ? t('approved') : t('approveAndContinue')}
    </BuilderButton>
  )
}

function RejectButton({ onClick, disabled }: { onClick: () => void; disabled: boolean }) {
  const t = useTranslations('chat.recommendation')
  return (
    <BuilderButton tone="secondary" onClick={onClick} disabled={disabled}>
      <XIcon className="size-3" strokeWidth={2.5} />
      {t('requestRevision')}
    </BuilderButton>
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
  const t = useTranslations('chat.recommendation')
  const { submitted, isLocked, handleApprove, handleRevision } = form

  return (
    <BuilderActionRow>
      <BuilderButton
        tone="ghost"
        onClick={() => setFeedbackOpen(!feedbackOpen)}
        disabled={isLocked}
      >
        <PencilIcon className="size-3" />
        {feedbackOpen ? t('closeFeedback') : t('writeFeedback')}
      </BuilderButton>
      <div className="flex items-center gap-2">
        <RejectButton onClick={handleRevision} disabled={isLocked} />
        <ApproveButton
          onClick={handleApprove}
          disabled={isLocked}
          submitted={submitted === 'approved'}
        />
      </div>
    </BuilderActionRow>
  )
}

function RecommendationApproval({
  args,
  status,
}: {
  args: RecommendationArgs
  status: 'running' | 'complete' | 'incomplete' | 'requires-action'
}) {
  const t = useTranslations('chat.recommendation')
  const form = useApprovalForm({
    isComplete: status === 'complete',
    approveDisplay: t('approve'),
    revisionFallback: t('requestRevision'),
  })
  const [feedbackOpen, setFeedbackOpen] = useState(false)

  const items = args.items ?? []
  const cardKind = args.item_kind ?? 'tool'
  const title = args.title || (cardKind === 'middleware' ? t('middlewareTitle') : t('toolTitle'))

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
        <BuilderBody>
          {items.length === 0 && (
            <p className="px-2.5 py-3 moldy-ui-body-sm moldy-builder-color-muted">{t('empty')}</p>
          )}
          {items.map((item, idx) => (
            <ToolRow key={idx} item={item} cardKind={cardKind} />
          ))}
        </BuilderBody>
        {args.summary && <BuilderSummary>{args.summary}</BuilderSummary>}
      </PhaseCard>
    </div>
  )
}

export const RecommendationApprovalToolUI = makeAssistantToolUI<RecommendationArgs, unknown>({
  toolName: 'recommendation_approval',
  render: ({ args, status }) => <RecommendationApproval args={args} status={status.type} />,
})
