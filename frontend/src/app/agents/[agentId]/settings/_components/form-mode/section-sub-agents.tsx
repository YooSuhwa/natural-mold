'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { SettingsIcon, UsersIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useAgents } from '@/lib/hooks/use-agents'
import { SubAgentsDialog } from '../dialogs/sub-agents-dialog'

interface SectionSubAgentsProps {
  selectedSubAgentIds: Set<string>
  onToggleSubAgent: (id: string) => void
  /** 자기 자신은 sub-agent가 될 수 없으므로 필터에 사용. 매뉴얼(생성) 페이지에선 빈 문자열 전달. */
  currentAgentId: string
}

const MAX_VISIBLE_NAMES = 2

export function SectionSubAgents({
  selectedSubAgentIds,
  onToggleSubAgent,
  currentAgentId,
}: SectionSubAgentsProps) {
  const t = useTranslations('agent.settings.subAgents')
  const [open, setOpen] = useState(false)
  const { data: agents } = useAgents()

  const candidates = useMemo(
    () => agents?.filter((a) => a.id !== currentAgentId) ?? [],
    [agents, currentAgentId],
  )
  const selected = useMemo(
    () => candidates.filter((a) => selectedSubAgentIds.has(a.id)),
    [candidates, selectedSubAgentIds],
  )

  const summary = useMemo(() => {
    if (!agents) return ''
    if (selected.length === 0) return t('placeholder')
    const visibleNames = selected.slice(0, MAX_VISIBLE_NAMES).map((a) => a.name)
    const more = selected.length - visibleNames.length
    return more > 0 ? `${visibleNames.join(', ')} +${more}` : visibleNames.join(', ')
  }, [agents, selected, t])

  if (!agents) {
    return <Skeleton className="h-11 w-full rounded-lg" />
  }

  return (
    <>
      <div className="flex items-center justify-between rounded-lg border px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <UsersIcon className="size-4 text-muted-foreground" />
          <span className="text-sm font-medium">{t('title')}</span>
          <span className="truncate text-sm text-muted-foreground">{summary}</span>
        </div>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => setOpen(true)}
          aria-label={t('manageTitle')}
        >
          <SettingsIcon className="size-4" />
        </Button>
      </div>
      <SubAgentsDialog
        open={open}
        onOpenChange={setOpen}
        selectedSubAgentIds={selectedSubAgentIds}
        onToggleSubAgent={onToggleSubAgent}
        currentAgentId={currentAgentId}
      />
    </>
  )
}
