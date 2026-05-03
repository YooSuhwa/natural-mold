'use client'

import { makeAssistantToolUI } from '@assistant-ui/react'
import { WrenchIcon, BlocksIcon } from 'lucide-react'
import { useApprovalForm } from './use-approval-form'
import { ApprovalFooter } from './approval-footer'

type ItemKind = 'tool' | 'middleware'

interface ToolItem {
  tool_name?: string
  middleware_name?: string
  description?: string
  reason?: string
  path?: string
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

function getItemPath(item: ToolItem, kind: ItemKind): string {
  if (item.path) return item.path
  const name = getItemName(item, kind)
  return kind === 'tool' ? `tools/${name}.yaml` : `middlewares/${name}.yaml`
}

function ItemCard({ item, kind }: { item: ToolItem; kind: ItemKind }) {
  const name = getItemName(item, kind)
  const Icon = kind === 'middleware' ? BlocksIcon : WrenchIcon
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
                {getItemPath(item, kind)}
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
