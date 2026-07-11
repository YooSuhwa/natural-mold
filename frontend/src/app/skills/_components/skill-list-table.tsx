'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import type { ColumnDef, RowSelectionState } from '@tanstack/react-table'
import { Download, MoreHorizontal, Trash2, UploadCloud } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { OriginBadge } from '@/components/marketplace/badges/origin-badge'
import { PublicationBadge } from '@/components/marketplace/badges/publication-badge'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { DataTable } from '@/components/ui/data-table'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { SkillEvaluationSummaryBadge } from '@/components/skill/skill-evaluation-summary-badge'
import { SkillHealthBadge } from '@/components/skill/skill-health-badge'
import { skillsApi } from '@/lib/api/skills'
import { useDeleteSkill } from '@/lib/hooks/use-skills'
import { formatDisplayDate } from '@/lib/utils/display-format'
import type { Skill } from '@/lib/types/skill'

/** 구 SkillCard와 동일한 게시 가드 — 이미 게시된 스킬에는 게시 진입점을 숨긴다. */
function canPublishSkill(skill: Skill): boolean {
  return !skill.publication_summary?.state || skill.publication_summary.state === 'not_published'
}

/**
 * 스킬 목록 표 (Phase 2 목업 skill-table) — DataTable rowSelection의 첫 도입.
 *
 * 선택 상태는 controlled(rowSelectionState)로 소유해 벌크 삭제/선택 해제 시
 * remount 없이 리셋한다(정렬·페이지 유지). 검색으로 숨겨진 선택 행이 남을 수
 * 있어 확인 다이얼로그에 대상 이름을 열거한다 (스펙 AD-5).
 */
export function SkillListTable({
  skills,
  isLoading,
  emptyTitle,
  onImprove,
  onPublish,
}: {
  /** 부모의 useMemo 결과를 그대로 받는다 — 새 identity를 만들면 선택 통지 effect가 재순환한다. */
  readonly skills: Skill[]
  readonly isLoading: boolean
  readonly emptyTitle: string
  readonly onImprove: (skillId: string) => void
  readonly onPublish: (skill: Skill) => void
}) {
  const t = useTranslations('skill')
  const list = useTranslations('skill.studio.list')
  const router = useRouter()
  const removeSkill = useDeleteSkill()
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({})
  const [selected, setSelected] = useState<Skill[]>([])
  const [pendingDelete, setPendingDelete] = useState<Skill[]>([])
  const [deleting, setDeleting] = useState(false)

  function resetSelection() {
    setRowSelection({})
    setSelected([])
  }

  async function executeDelete() {
    if (pendingDelete.length === 0) return
    setDeleting(true)
    const failures: string[] = []
    // 순차 삭제 — 기존 단건 DELETE 재사용, 부분 실패는 이름으로 보고 (AD-5).
    for (const skill of pendingDelete) {
      try {
        await removeSkill.mutateAsync(skill.id)
      } catch {
        failures.push(skill.name)
      }
    }
    setDeleting(false)
    setPendingDelete([])
    resetSelection()
    const deletedCount = pendingDelete.length - failures.length
    if (deletedCount > 0) {
      toast.success(list('deleteSuccess', { count: deletedCount }))
    }
    if (failures.length > 0) {
      toast.error(list('deletePartialFailure', { names: failures.join(', ') }))
    }
  }

  const connectedTotal = pendingDelete.reduce((sum, skill) => sum + skill.used_by_count, 0)
  const pendingNames = pendingDelete.map((skill) => skill.name).join(', ')

  const columns: ColumnDef<Skill, unknown>[] = [
    {
      accessorKey: 'name',
      header: t('columns.skill'),
      cell: ({ row }) => (
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">{row.original.name}</p>
          <p className="moldy-ui-micro truncate font-mono text-muted-foreground">
            {row.original.slug}
          </p>
        </div>
      ),
    },
    {
      accessorKey: 'kind',
      header: t('columns.kind'),
      cell: ({ row }) => (
        <Badge variant="secondary" className="moldy-ui-micro">
          {t(`typeFilter.${row.original.kind}`)}
        </Badge>
      ),
    },
    {
      id: 'status',
      header: t('columns.status'),
      enableSorting: false,
      cell: ({ row }) => (
        // 게시/출처 배지는 구 카드에서 이관 — 목록에서 게시 상태를 잃지 않는다.
        <div className="flex flex-wrap items-center gap-1">
          <SkillHealthBadge health={row.original.health} />
          <OriginBadge summary={row.original.origin_summary} />
          <PublicationBadge summary={row.original.publication_summary} />
        </div>
      ),
    },
    {
      id: 'evaluation',
      header: t('columns.evaluation'),
      enableSorting: false,
      cell: ({ row }) => (
        <SkillEvaluationSummaryBadge summary={row.original.latest_evaluation_summary} />
      ),
    },
    {
      accessorKey: 'used_by_count',
      header: t('columns.agents'),
      cell: ({ row }) => (
        <span className="text-sm tabular-nums">
          {t('agentsCount', { count: row.original.used_by_count })}
        </span>
      ),
    },
    {
      accessorKey: 'updated_at',
      header: t('columns.updatedAt'),
      cell: ({ row }) => (
        <span className="moldy-ui-micro text-muted-foreground">
          {formatDisplayDate(row.original.updated_at, { fallback: '' })}
        </span>
      ),
    },
    {
      id: 'actions',
      header: '',
      enableSorting: false,
      cell: ({ row }) => (
        <SkillRowActions
          skill={row.original}
          onImprove={onImprove}
          onPublish={onPublish}
          onDelete={(skill) => setPendingDelete([skill])}
        />
      ),
    },
  ]

  return (
    <>
      <DataTable
        columns={columns}
        data={skills}
        loading={isLoading}
        pageSize={20}
        enableRowSelection
        rowSelectionState={rowSelection}
        onRowSelectionStateChange={setRowSelection}
        onRowSelectionChange={setSelected}
        onRowClick={(skill) => router.push(`/skills/${skill.id}/source`)}
        emptyTitle={emptyTitle}
        toolbar={
          selected.length > 0 ? (
            <div className="flex items-center gap-2" data-testid="skill-bulk-bar">
              <span className="moldy-ui-micro text-muted-foreground">
                {list('selectedCount', { count: selected.length })}
              </span>
              <Button
                type="button"
                variant="destructive"
                size="sm"
                onClick={() => setPendingDelete(selected)}
              >
                <Trash2 className="size-3.5" />
                {list('deleteSelected')}
              </Button>
              <Button type="button" variant="ghost" size="sm" onClick={resetSelection}>
                {list('clearSelection')}
              </Button>
            </div>
          ) : null
        }
      />

      <DeleteConfirmDialog
        open={pendingDelete.length > 0}
        onOpenChange={(open) => {
          if (!open && !deleting) setPendingDelete([])
        }}
        title={list('bulkDeleteTitle', { count: pendingDelete.length })}
        description={
          connectedTotal > 0
            ? list('bulkDeleteDescriptionConnected', {
                names: pendingNames,
                connected: connectedTotal,
              })
            : list('bulkDeleteDescription', { names: pendingNames })
        }
        confirmLabel={list('deleteSelected')}
        isPending={deleting}
        onConfirm={() => void executeDelete()}
      />
    </>
  )
}

function SkillRowActions({
  skill,
  onImprove,
  onPublish,
  onDelete,
}: {
  readonly skill: Skill
  readonly onImprove: (skillId: string) => void
  readonly onPublish: (skill: Skill) => void
  readonly onDelete: (skill: Skill) => void
}) {
  const t = useTranslations('skill')
  const list = useTranslations('skill.studio.list')
  const router = useRouter()

  // 행 클릭(소스 이동)과 겹치지 않게 각 인터랙티브 요소에서 전파를 끊는다
  // (DataTable 체크박스 컬럼과 동일 선례 — 정적 wrapper 핸들러는 a11y 위반).
  return (
    <div className="flex items-center justify-end gap-1">
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={(event) => {
          event.stopPropagation()
          onImprove(skill.id)
        }}
        aria-label={list('rowImproveAria', { name: skill.name })}
      >
        {list('rowImprove')}
      </Button>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={(event) => {
          event.stopPropagation()
          router.push(`/skills/${skill.id}/evaluation`)
        }}
      >
        {list('rowEvaluation')}
      </Button>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={(event) => {
          event.stopPropagation()
          router.push(`/skills/${skill.id}/versions`)
        }}
      >
        {list('rowVersions')}
      </Button>
      <DropdownMenu>
        <DropdownMenuTrigger
          aria-label={list('rowMenuAria', { name: skill.name })}
          className="inline-flex size-8 items-center justify-center rounded-md hover:bg-muted"
          onClick={(event) => event.stopPropagation()}
        >
          <MoreHorizontal className="size-4" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onClick={() => router.push(`/skills/${skill.id}/source`)}>
            {list('rowViewSource')}
          </DropdownMenuItem>
          {canPublishSkill(skill) ? (
            <DropdownMenuItem onClick={() => onPublish(skill)}>
              <UploadCloud className="size-4" />
              {t('actions.publish')}
            </DropdownMenuItem>
          ) : null}
          {skill.kind === 'package' ? (
            <DropdownMenuItem
              render={
                <a href={skillsApi.exportUrl(skill.id)} download aria-label={list('rowExport')} />
              }
            >
              <Download className="size-4" />
              {list('rowExport')}
            </DropdownMenuItem>
          ) : null}
          <DropdownMenuItem variant="destructive" onClick={() => onDelete(skill)}>
            <Trash2 className="size-4" />
            {list('rowDelete')}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}
