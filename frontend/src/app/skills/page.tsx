'use client'

import { useMemo, useState } from 'react'
import { BookOpen, Plus } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/shared/empty-state'
import { SearchInput } from '@/components/shared/search-input'
import {
  CountedLineTabs,
  ResourceGrid,
  ResourcePage,
  ResourcePanel,
  ResourceToolbar,
} from '@/components/shared/resource-layout'
import { Skeleton } from '@/components/ui/skeleton'
import { SkillCard } from '@/components/skill/skill-card'
import { SkillCreateDialog } from '@/components/skill/skill-create-dialog'
import { SkillDetailDialog } from '@/components/skill/skill-detail-dialog'
import { SkillBuilderDialog } from '@/components/skill/skill-builder-dialog'
import { PublishWizard } from '@/components/marketplace/publish-wizard'
import { useSkills } from '@/lib/hooks/use-skills'
import type { Skill, SkillKind } from '@/lib/types/skill'

type CreateTab = 'chat' | 'text' | 'package'
type SkillTab = 'all' | Skill['kind']

const ALL_TAB = 'all'
const SKILL_TABS: readonly SkillTab[] = [ALL_TAB, 'text', 'package']

function isSkillTab(value: string): value is SkillTab {
  return value === ALL_TAB || value === 'text' || value === 'package'
}

function formatDate(value: string | null): string {
  if (!value) return ''
  return new Date(value).toLocaleDateString()
}

export default function SkillsPage() {
  const t = useTranslations('skill')
  const [createOpen, setCreateOpen] = useState(false)
  const [createTab, setCreateTab] = useState<CreateTab>('chat')
  const [builderOpen, setBuilderOpen] = useState(false)
  const [builderInitialRequest, setBuilderInitialRequest] = useState('')
  const [activeTab, setActiveTab] = useState<SkillTab>(ALL_TAB)
  const [search, setSearch] = useState('')
  const normalizedSearch = search.trim().toLowerCase()
  const skillQueryParams = useMemo(() => {
    const params: { kind?: SkillKind; q?: string } = {}
    if (activeTab !== ALL_TAB) params.kind = activeTab
    if (normalizedSearch) params.q = normalizedSearch
    return Object.keys(params).length > 0 ? params : undefined
  }, [activeTab, normalizedSearch])
  const { data: skills, isLoading } = useSkills(skillQueryParams)
  // Deep-link from /marketplace Open button: `/skills?detailId=...`.
  // useState lazy initializer runs once at mount (post-hydration on client,
  // safely returns null during SSR/prerender). Avoids effect+setState pattern
  // that the react-hooks/set-state-in-effect rule rejects.
  const [detailId, setDetailId] = useState<string | null>(() => {
    if (typeof window === 'undefined') return null
    return new URLSearchParams(window.location.search).get('detailId')
  })
  const [publishSkill, setPublishSkill] = useState<Skill | null>(null)

  function openCreate(tab: CreateTab) {
    setCreateTab(tab)
    setCreateOpen(true)
  }

  const data = useMemo(() => skills ?? [], [skills])

  const filteredSkills = useMemo(() => {
    return data.filter((skill) => {
      if (activeTab !== ALL_TAB && skill.kind !== activeTab) return false
      if (!normalizedSearch) return true
      return [skill.name, skill.slug, skill.description, skill.version, skill.kind]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(normalizedSearch))
    })
  }, [activeTab, data, normalizedSearch])

  function countSkills(tab: SkillTab): number {
    return data.filter((skill) => {
      if (tab !== ALL_TAB && skill.kind !== tab) return false
      if (!normalizedSearch) return true
      return [skill.name, skill.slug, skill.description, skill.version, skill.kind]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(normalizedSearch))
    }).length
  }

  const tabs = SKILL_TABS.map((value) => ({
    value,
    label: value === ALL_TAB ? t('typeFilter.all') : t(`typeFilter.${value}`),
    countLabel: t('count', { count: countSkills(value) }),
  }))

  const isInitialEmpty = !isLoading && data.length === 0
  const isFilteredEmpty = !isLoading && data.length > 0 && filteredSkills.length === 0

  return (
    <ResourcePage
      title={t('title')}
      description={t('description')}
      action={
        <Button onClick={() => openCreate('chat')}>
          <Plus className="size-4" />
          {t('new')}
        </Button>
      }
    >
      <ResourcePanel>
        {isInitialEmpty ? (
          <ResourcePanel.Body>
            <EmptyState
              icon={<BookOpen className="size-6" />}
              title={t('empty.title')}
              description={t('empty.description')}
              className="bg-card/50"
              action={
                <Button onClick={() => openCreate('chat')}>
                  <Plus className="size-4" />
                  {t('firstSkill')}
                </Button>
              }
            />
          </ResourcePanel.Body>
        ) : (
          <>
            <ResourcePanel.Toolbar>
              <CountedLineTabs
                ariaLabel={t('viewMode.label')}
                value={activeTab}
                tabs={tabs}
                onValueChange={(value) => {
                  if (isSkillTab(value)) setActiveTab(value)
                }}
              />
              <ResourceToolbar>
                <SearchInput
                  containerClassName="flex-1 sm:max-w-[360px]"
                  placeholder={t('searchPlaceholder')}
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </ResourceToolbar>
            </ResourcePanel.Toolbar>

            <ResourcePanel.Body className="bg-background/30">
              {isLoading ? (
                <ResourceGrid minColumnWidth={300}>
                  {Array.from({ length: 6 }).map((_, index) => (
                    <Skeleton key={index} className="moldy-skeleton-card h-[196px]" />
                  ))}
                </ResourceGrid>
              ) : isFilteredEmpty ? (
                <EmptyState title={t('empty.filtered')} className="bg-card/50" />
              ) : (
                <ResourceGrid minColumnWidth={300}>
                  {filteredSkills.map((skill) => (
                    <SkillCard
                      key={skill.id}
                      skill={skill}
                      kindLabel={t(`typeFilter.${skill.kind}`)}
                      agentsLabel={t('agentsCount', { count: skill.used_by_count })}
                      updatedLabel={formatDate(skill.updated_at)}
                      actionLabel={t('actions.manage')}
                      publishLabel={t('actions.publish')}
                      onOpen={setDetailId}
                      onPublish={setPublishSkill}
                    />
                  ))}
                </ResourceGrid>
              )}
            </ResourcePanel.Body>
          </>
        )}
      </ResourcePanel>

      <SkillCreateDialog
        key={`create-${createTab}`}
        open={createOpen}
        onOpenChange={setCreateOpen}
        initialTab={createTab}
        onCreated={(id) => setDetailId(id)}
        onStartChat={(request) => {
          setBuilderInitialRequest(request)
          setBuilderOpen(true)
        }}
      />
      <SkillBuilderDialog
        open={builderOpen}
        mode="create"
        initialRequest={builderInitialRequest}
        onOpenChange={setBuilderOpen}
        onCreated={(id) => setDetailId(id)}
      />
      <SkillDetailDialog
        skillId={detailId}
        open={!!detailId}
        onOpenChange={(open) => {
          if (open) return
          setDetailId(null)
          // /marketplace에서 ``?detailId=...`` deep-link로 진입한 경우, dialog
          // 닫을 때 URL의 query string도 함께 정리한다. history.replaceState로
          // route 자체는 다시 그리지 않아 list scroll/state 보존.
          if (typeof window !== 'undefined' && window.location.search) {
            window.history.replaceState(null, '', '/skills')
          }
        }}
      />

      <PublishWizard
        skill={publishSkill}
        open={!!publishSkill}
        onOpenChange={(open) => !open && setPublishSkill(null)}
      />
    </ResourcePage>
  )
}
