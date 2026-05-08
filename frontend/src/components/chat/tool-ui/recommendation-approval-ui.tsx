'use client'

import { makeAssistantToolUI } from '@assistant-ui/react'
import { WrenchIcon, BlocksIcon, BookOpenIcon, PlugIcon } from 'lucide-react'
import { useApprovalForm } from './use-approval-form'
import { ApprovalFooter } from './approval-footer'

// Card-level kind (Phase 3: tool, Phase 4: middleware). 개별 item 의 카테고리는
// item.kind 로 override — 한 카드 안에 tool / mcp / skill 이 섞여 추천될 때
// 각 행이 자기 아이콘과 path 를 가진다.
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

function getItemName(item: ToolItem, kind: ItemKind): string {
  if (kind === 'tool') return item.tool_name ?? ''
  return item.middleware_name ?? ''
}

const ROW_PATH_PREFIX: Record<RowKind, string> = {
  tool: 'tools',
  mcp: 'mcp',
  skill: 'skills',
  middleware: 'middlewares',
}

function getItemPath(item: ToolItem, rowKind: RowKind): string {
  if (item.path) return item.path
  const name =
    rowKind === 'middleware' ? (item.middleware_name ?? '') : (item.tool_name ?? '')
  return `${ROW_PATH_PREFIX[rowKind]}/${name}.yaml`
}

const ROW_ICON: Record<RowKind, typeof WrenchIcon> = {
  tool: WrenchIcon,
  mcp: PlugIcon,
  skill: BookOpenIcon,
  middleware: BlocksIcon,
}

function resolveRowKind(item: ToolItem, cardKind: ItemKind): RowKind {
  if (cardKind === 'middleware') return 'middleware'
  return item.kind ?? 'tool'
}

function ItemCard({ item, kind }: { item: ToolItem; kind: ItemKind }) {
  const name = getItemName(item, kind)
  const rowKind = resolveRowKind(item, kind)
  const Icon = ROW_ICON[rowKind]
  return (
    <div className="rounded-lg border border-border p-3">
      <div className="flex items-start gap-2">
        <Icon className="mt-0.5 size-4 text-muted-foreground" />
        <div className="flex-1">
          <div className="font-mono text-sm font-semibold">{name}</div>
          {(item.path || name) && (
            <div className="mt-1 text-xs text-muted-foreground">
              경로:{' '}
              <code className="rounded bg-muted px-1 py-0.5">
                {getItemPath(item, rowKind)}
              </code>
            </div>
          )}
          {item.reason && (
            <p className="mt-2 text-sm text-foreground">사유: {item.reason}</p>
          )}
        </div>
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
  const form = useApprovalForm({ isComplete: status === 'complete' })
  const items = args.items ?? []

  return (
    <div className="my-3 rounded-xl border border-border bg-card shadow-sm">
      <div className="border-b border-border px-4 py-3 text-sm font-semibold">
        {args.title || '추천 결과'}
      </div>
      <div className="space-y-2 px-4 py-3">
        {items.map((item, idx) => (
          <ItemCard key={idx} item={item} kind={args.item_kind ?? 'tool'} />
        ))}
        {items.length === 0 && <p className="text-sm text-muted-foreground">추천된 항목이 없습니다.</p>}
      </div>
      {args.summary && (
        <div className="border-t border-border px-4 py-3 text-sm text-foreground">
          {args.summary}
        </div>
      )}
      <ApprovalFooter form={form} showStatusMessage />
    </div>
  )
}

export const RecommendationApprovalToolUI = makeAssistantToolUI<RecommendationArgs, unknown>({
  toolName: 'recommendation_approval',
  render: ({ args, status }) => <RecommendationApproval args={args} status={status.type} />,
})
