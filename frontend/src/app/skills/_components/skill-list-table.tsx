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
import { ApiError } from '@/lib/api/errors'
import { skillsApi } from '@/lib/api/skills'
import { useAgents } from '@/lib/hooks/use-agents'
import { useDeleteSkill } from '@/lib/hooks/use-skills'
import { formatDisplayDate } from '@/lib/utils/display-format'
import type { Skill } from '@/lib/types/skill'

/** 구 SkillCard와 동일한 게시 가드 — 이미 게시된 스킬에는 게시 진입점을 숨긴다. */
function canPublishSkill(skill: Skill): boolean {
  return !skill.publication_summary?.state || skill.publication_summary.state === 'not_published'
}

/** 확인 다이얼로그 이름 열거 상한 — 무제한 열거는 max-w-xs 다이얼로그에서
 * 수백 줄로 자라 확인/취소 버튼이 화면 밖으로 잘린다 (R5). */
const NAME_LIST_CAP = 8

function formatNameList(names: readonly string[], more: (count: number) => string): string {
  if (names.length <= NAME_LIST_CAP) return names.join(', ')
  return `${names.slice(0, NAME_LIST_CAP).join(', ')} ${more(names.length - NAME_LIST_CAP)}`
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
  improvePending,
  onPublish,
}: {
  /** 부모의 useMemo 결과를 그대로 받는다 — 새 identity를 만들면 선택 통지 effect가 재순환한다. */
  readonly skills: Skill[]
  readonly isLoading: boolean
  readonly emptyTitle: string
  readonly onImprove: (skillId: string) => void
  /** 빌더 세션 시작 중 — 행 "수정" 이중 클릭이 세션을 중복 생성하지 않게 막는다. */
  readonly improvePending: boolean
  readonly onPublish: (skill: Skill) => void
}) {
  const t = useTranslations('skill')
  const list = useTranslations('skill.studio.list')
  const router = useRouter()
  const removeSkill = useDeleteSkill()
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({})
  const [selected, setSelected] = useState<Skill[]>([])
  // 삭제 대상은 **id만** 저장하고 실행/표시 시점에 현재 목록에서 파생한다 —
  // 선택 스냅샷 객체는 refetch 후 stale해질 수 있다(DataTable 통지는 id 기반).
  const [pendingDeleteIds, setPendingDeleteIds] = useState<string[]>([])
  const [deleting, setDeleting] = useState(false)
  const pendingSkills = skills.filter((skill) => pendingDeleteIds.includes(skill.id))
  // 다이얼로그가 파생-close된 뒤(대상이 refetch로 전부 소실) 잔존 id가 남으면
  // 해당 스킬이 목록에 재등장할 때 파괴적 다이얼로그가 저절로 재오픈된다 —
  // guarded render-time 리셋으로 정리(패키지 에디터 selectedPath 선례).
  if (pendingDeleteIds.length > 0 && pendingSkills.length === 0 && !deleting) {
    setPendingDeleteIds([])
  }

  function resetSelection() {
    setRowSelection({})
    setSelected([])
  }

  async function executeDelete() {
    if (pendingSkills.length === 0) {
      // 다이얼로그 오픈 중 refetch로 대상이 전부 사라진 경우 — 잔존 id를
      // 정리해 닫는다(안 그러면 확인 버튼이 무반응인 dead-end).
      setPendingDeleteIds([])
      return
    }
    setDeleting(true)
    const failures: string[] = []
    // 순차 삭제 — 기존 단건 DELETE 재사용, 부분 실패는 이름으로 보고 (AD-5).
    for (const skill of pendingSkills) {
      try {
        await removeSkill.mutateAsync(skill.id)
      } catch (error) {
        // 404 = 멱등 성공 — 다른 탭/플로우에서 이미 삭제된 대상. 실패로 세면
        // 결과는 요청대로인데 "삭제 실패" 토스트가 오발한다 (규칙 ④, R5).
        if (!(error instanceof ApiError && error.status === 404)) {
          failures.push(skill.name)
        }
      }
    }
    setDeleting(false)
    setPendingDeleteIds([])
    resetSelection()
    const deletedCount = pendingSkills.length - failures.length
    if (deletedCount > 0) {
      toast.success(list('deleteSuccess', { count: deletedCount }))
    }
    if (failures.length > 0) {
      // 실패 이름도 확인 다이얼로그와 같은 상한 — 세션 만료 등으로 전건 실패 시
      // 무상한 토스트가 화면을 덮는다 (R6).
      toast.error(
        list('deletePartialFailure', {
          names: formatNameList(failures, (count) => list('moreNames', { count })),
        }),
      )
    }
  }

  const connectedTotal = pendingSkills.reduce((sum, skill) => sum + skill.used_by_count, 0)
  const pendingNames = formatNameList(
    pendingSkills.map((skill) => skill.name),
    (count) => list('moreNames', { count }),
  )
  // AD-4.1 — 영향받는 에이전트 이름은 신규 API 없이 에이전트 목록에서 역도출.
  // 삭제 확인이 열려 있고 **연결 카운트가 있을 때만** fetch — 무조건 fetch는
  // /skills 방문마다(R5), 연결 0 삭제마다(R6) 무거운 에이전트 전체 직렬화를
  // 부른다(사이드바는 useAgentSummaries라 캐시 공유 없음). 로딩 중엔 자리
  // 지킴 문구로 다이얼로그 텍스트 리플로를 막는다.
  const {
    data: agents,
    isLoading: agentsLoading,
    isError: agentsError,
  } = useAgents({
    enabled: pendingDeleteIds.length > 0 && connectedTotal > 0,
  })
  const pendingIdSet = new Set(pendingDeleteIds)
  const affectedAgentNames =
    connectedTotal > 0
      ? (agents ?? [])
          .filter((agent) => agent.skills?.some((brief) => pendingIdSet.has(brief.id)))
          .map((agent) => agent.name)
      : []

  const columns: ColumnDef<Skill, unknown>[] = [
    {
      accessorKey: 'name',
      header: t('columns.skill'),
      cell: ({ row }) => (
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">{row.original.name}</p>
          <p className="moldy-ui-micro truncate font-mono text-muted-foreground">
            {row.original.slug}
            {row.original.version ? ` · v${row.original.version}` : ''}
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
      // 목업 계약(통과율 정렬) — 요약 pass_rate 기준, 미평가는 최하단.
      accessorFn: (skill) => skill.latest_evaluation_summary?.pass_rate ?? -1,
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
          improvePending={improvePending}
          onPublish={onPublish}
          onDelete={(skill) => setPendingDeleteIds([skill.id])}
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
                onClick={() => setPendingDeleteIds(selected.map((skill) => skill.id))}
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
        open={pendingSkills.length > 0}
        onOpenChange={(open) => {
          if (!open && !deleting) setPendingDeleteIds([])
        }}
        title={list('bulkDeleteTitle', { count: pendingSkills.length })}
        description={
          connectedTotal > 0
            ? [
                list('bulkDeleteDescriptionConnected', {
                  names: pendingNames,
                  connected: connectedTotal,
                }),
                // 조회 실패를 침묵 소실로 두지 않는다 — 연결 카운트만 보이고
                // 이름 공개(AD-4.1)가 사라지면 사용자는 실패를 모른 채 파괴적
                // 확정을 누른다 (R6).
                agentsLoading
                  ? list('affectedAgentsLoading')
                  : agentsError
                    ? list('affectedAgentsError')
                    : affectedAgentNames.length > 0
                      ? list('affectedAgents', {
                          names: formatNameList(affectedAgentNames, (count) =>
                            list('moreNames', { count }),
                          ),
                        })
                      : null,
              ]
                .filter(Boolean)
                .join('\n')
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
  improvePending,
  onPublish,
  onDelete,
}: {
  readonly skill: Skill
  readonly onImprove: (skillId: string) => void
  readonly improvePending: boolean
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
        disabled={improvePending}
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
