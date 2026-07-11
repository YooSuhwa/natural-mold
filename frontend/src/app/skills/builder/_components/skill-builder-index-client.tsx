'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { HammerIcon, Plus, Sparkles } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { EmptyState } from '@/components/shared/empty-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { SkillCreateDialog } from '@/components/skill/skill-create-dialog'
import { useBuilderSessionLauncher, useSkillBuilderSessions } from '@/lib/hooks/use-skill-builder'
import { useSkill } from '@/lib/hooks/use-skills'
import { formatDisplayDateTime } from '@/lib/utils/display-format'
import type { SkillBuilderSessionBrief } from '@/lib/types/skill-builder'

/**
 * 빌더 인덱스 (스펙 AD-1) — 세션 없이 빌더 탭에 진입했을 때의 착지점.
 * `?skillId=`로 스코프되면 해당 스킬의 세션 이력 + 개선 시작 CTA를 보여준다.
 */
export function SkillBuilderIndexClient() {
  const t = useTranslations('skill.studio.builderIndex')
  const router = useRouter()
  const searchParams = useSearchParams()
  const skillId = searchParams.get('skillId')
  const { data: skill } = useSkill(skillId)
  const { data: sessions, isLoading } = useSkillBuilderSessions(
    skillId ? { skill_id: skillId } : undefined,
  )
  const launcher = useBuilderSessionLauncher()
  const [createOpen, setCreateOpen] = useState(false)

  // 세션 시작/라우팅/실패 토스트는 공유 launcher가 소유한다 (리뷰 R).
  async function startCreate(request: string) {
    const started = await launcher.startCreate(request)
    if (started) setCreateOpen(false)
  }

  const items = sessions ?? []

  return (
    <div className="moldy-app-surface flex min-h-0 flex-1 overflow-hidden p-3">
      <div className="moldy-panel flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <header className="flex flex-wrap items-center gap-2 border-b border-border/60 px-4 py-3">
          <HammerIcon className="size-4 shrink-0 text-muted-foreground" />
          <h1 className="text-sm font-semibold">{t('title')}</h1>
          <span className="hidden min-w-0 truncate text-xs text-muted-foreground lg:inline">
            {skill ? t('scopedHint', { name: skill.name }) : t('hint')}
          </span>
          <span className="ml-auto flex shrink-0 items-center gap-2">
            {skill ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={launcher.pending}
                onClick={() => void launcher.startImprove(skill.id)}
              >
                <Sparkles className="size-3.5" />
                {t('improveSkill', { name: skill.name })}
              </Button>
            ) : null}
            <Button type="button" size="sm" onClick={() => setCreateOpen(true)}>
              <Plus className="size-4" />
              {t('startCreate')}
            </Button>
          </span>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
          {isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-16 w-full rounded-lg" />
              <Skeleton className="h-16 w-full rounded-lg" />
            </div>
          ) : items.length === 0 ? (
            <EmptyState
              icon={<HammerIcon className="size-6" />}
              title={t('emptyTitle')}
              description={t('emptyHint')}
              className="bg-card/50"
              action={
                <Button onClick={() => setCreateOpen(true)}>
                  <Plus className="size-4" />
                  {t('startCreate')}
                </Button>
              }
            />
          ) : (
            <ul className="space-y-2" data-testid="builder-session-list">
              {items.map((session) => (
                <SessionRow key={session.id} session={session} />
              ))}
            </ul>
          )}
        </div>
      </div>

      <SkillCreateDialog
        key={`builder-index-create-${createOpen}`}
        open={createOpen}
        onOpenChange={setCreateOpen}
        initialTab="chat"
        onCreated={(id) => {
          setCreateOpen(false)
          router.push(`/skills/${id}/source`)
        }}
        onStartChat={(request) => void startCreate(request)}
      />
    </div>
  )
}

function SessionRow({ session }: { readonly session: SkillBuilderSessionBrief }) {
  const t = useTranslations('skill.studio.builderIndex')
  const builderChat = useTranslations('skill.builderChat')

  return (
    <li>
      <Link
        href={`/skills/builder/${session.id}`}
        className="flex flex-wrap items-center gap-2 rounded-lg border border-border/70 px-3 py-2.5 hover:border-primary-strong/50"
      >
        <Badge variant="outline" className="shrink-0">
          {session.mode === 'improve' ? builderChat('modeImprove') : builderChat('modeCreate')}
        </Badge>
        <Badge variant="secondary" className="shrink-0">
          {builderChat(`status.${session.status}` as Parameters<typeof builderChat>[0])}
        </Badge>
        <span className="min-w-0 flex-1 truncate text-sm">{session.user_request}</span>
        <span className="moldy-ui-micro shrink-0 text-muted-foreground">
          {t('updatedAt', { time: formatDisplayDateTime(session.updated_at) })}
        </span>
      </Link>
    </li>
  )
}
