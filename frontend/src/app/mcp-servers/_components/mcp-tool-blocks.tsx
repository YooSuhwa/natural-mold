'use client'

import { useMemo } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { useApiErrorMessage } from '@/components/shared/api-error-state'
import { PrincipalPicker, principalKey } from '@/components/sharing/principal-picker'
import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api/client'
import {
  useBlockMcpTool,
  useMcpToolBlocks,
  useUnblockMcpTool,
} from '@/lib/hooks/use-mcp-tool-blocks'

const PRINCIPALS = ['user', 'group'] as const

/**
 * People and teams kept from one MCP tool (tool-governance-plan 3.6). Server managers
 * only; a blocked person's runs stop being given the tool and their calls are refused.
 */
export function McpToolBlocks({
  serverId,
  toolId,
  toolName,
}: {
  serverId: string
  toolId: string
  toolName: string
}) {
  const t = useTranslations('mcp.toolTable.blocks')
  const blocks = useMcpToolBlocks(serverId, toolId)
  const block = useBlockMcpTool(serverId, toolId)
  const unblock = useUnblockMcpTool(serverId, toolId)
  const errorMessage = useApiErrorMessage()
  const items = blocks.data?.items
  const blockedKeys = useMemo(
    () =>
      new Set((items ?? []).map((item) => principalKey(item.principal_type, item.principal_id))),
    [items],
  )

  return (
    <div className="space-y-2 text-xs" data-tool-blocks={toolName}>
      <p className="text-muted-foreground">{t('help')}</p>
      <PrincipalPicker
        types={PRINCIPALS}
        excludeKeys={blockedKeys}
        label={t('pickerLabel', { tool: toolName })}
        disabled={block.isPending}
        onPick={(subject) => {
          if (subject.type !== 'user' && subject.type !== 'group') return
          block.mutate(
            { type: subject.type, id: subject.id },
            {
              onError: (error) => {
                const owner =
                  error instanceof ApiError &&
                  error.code === 'grant_invalid' &&
                  error.details?.reason === 'owner'
                toast.error(owner ? t('ownerRefused') : errorMessage(error, t('blockFailed')))
              },
            },
          )
        }}
      />
      {blocks.isError ? (
        <p role="alert" className="text-destructive">
          {t('loadFailed')}
        </p>
      ) : items && items.length === 0 ? (
        <p className="text-muted-foreground">{t('empty')}</p>
      ) : (
        <ul className="divide-y divide-border rounded-lg border bg-background">
          {(items ?? []).map((item) => (
            <li key={item.grant_id} className="flex items-center justify-between gap-2 px-2 py-1.5">
              <span className="min-w-0 break-words">
                {item.display_name ?? t('unknownPrincipal')}
                <span className="ml-1.5 text-muted-foreground">
                  {item.principal_type === 'group'
                    ? t('team', { count: item.member_count ?? 0 })
                    : t('person')}
                </span>
              </span>
              <Button
                size="sm"
                variant="ghost"
                className="h-7 px-2 text-xs"
                disabled={unblock.isPending}
                aria-label={t('unblockLabel', {
                  name: item.display_name ?? t('unknownPrincipal'),
                })}
                onClick={() =>
                  unblock.mutate(item.grant_id, {
                    onError: (error) => toast.error(errorMessage(error, t('unblockFailed'))),
                  })
                }
              >
                {t('unblock')}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
