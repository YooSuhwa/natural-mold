'use client'

import { useCanPublishMarketplace } from '@/components/marketplace/use-marketplace-publish'
import { createContext, useContext } from 'react'
import { useRouter } from 'next/navigation'
import { Download, MoreHorizontal, Trash2, UploadCloud, UsersIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { getSkillExportUrl } from '@/lib/hooks/use-skills'
import type { Skill } from '@/lib/types/skill'

export type SkillPermissionCheck = (skill: Skill, permission: string) => boolean

/** 구 SkillCard와 동일한 게시 가드 — 이미 게시된 스킬에는 게시 진입점을 숨긴다. */
export function canPublishSkill(skill: Skill): boolean {
  return !skill.publication_summary?.state || skill.publication_summary.state === 'not_published'
}

export interface SkillRowActionHandlers {
  /** Plan 13.9: improve needs can_edit, evaluation/versions can_view, publish can_share. */
  readonly can: SkillPermissionCheck
  readonly onImprove: (skillId: string) => void
  readonly improvePending: boolean
  readonly onPublish: (skill: Skill) => void
  readonly onDelete: (skill: Skill) => void
  /** Plan 13.4: sharing needs can_share (the dialog lives outside the table rows). */
  readonly onShare: (skill: Skill) => void
}

/**
 * Handlers reach the action cell through context so the table's columns stay memoized:
 * a new `columns` identity remounts DataTable's selection checkboxes and drops a click
 * that lands while the page re-renders (e.g. a permissions answer arriving).
 */
export const SkillRowActionsContext = createContext<SkillRowActionHandlers | null>(null)

export function SkillRowActions({ skill }: { readonly skill: Skill }) {
  const handlers = useContext(SkillRowActionsContext)
  if (!handlers) throw new Error('SkillRowActions needs SkillRowActionsContext')
  const { can, onImprove, improvePending, onPublish, onDelete, onShare } = handlers
  const t = useTranslations('skill')
  const tSharing = useTranslations('sharing')
  const list = useTranslations('skill.studio.list')
  const router = useRouter()
  const canPublish = useCanPublishMarketplace()

  // 행 클릭(소스 이동)과 겹치지 않게 각 인터랙티브 요소에서 전파를 끊는다
  // (DataTable 체크박스 컬럼과 동일 선례 — 정적 wrapper 핸들러는 a11y 위반).
  return (
    <div className="flex items-center justify-end gap-1">
      {can(skill, 'can_edit') ? (
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
      ) : null}
      {can(skill, 'can_view') ? (
        <>
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
        </>
      ) : null}
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
          {can(skill, 'can_share') ? (
            <DropdownMenuItem onClick={() => onShare(skill)}>
              <UsersIcon className="size-4" />
              {tSharing('share')}
            </DropdownMenuItem>
          ) : null}
          {canPublish && canPublishSkill(skill) && can(skill, 'can_share') ? (
            <DropdownMenuItem onClick={() => onPublish(skill)}>
              <UploadCloud className="size-4" />
              {t('actions.publish')}
            </DropdownMenuItem>
          ) : null}
          {skill.kind === 'package' && can(skill, 'can_view') ? (
            <DropdownMenuItem
              render={
                <a href={getSkillExportUrl(skill.id)} download aria-label={list('rowExport')} />
              }
            >
              <Download className="size-4" />
              {list('rowExport')}
            </DropdownMenuItem>
          ) : null}
          {can(skill, 'can_delete') ? (
            <DropdownMenuItem variant="destructive" onClick={() => onDelete(skill)}>
              <Trash2 className="size-4" />
              {list('rowDelete')}
            </DropdownMenuItem>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}
