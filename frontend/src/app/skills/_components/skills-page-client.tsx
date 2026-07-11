'use client'

import { useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { BookOpen, Plus } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

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
import { useStartSkillBuilder } from '@/lib/hooks/use-skill-builder'
import { useSkills } from '@/lib/hooks/use-skills'
import {
  ALL_SKILL_FILTER,
  filterSkillList,
  SKILL_STATE_FILTERS,
  type SkillKindFilter,
  type SkillStateFilter,
} from '@/lib/skill-state-filters'
import { formatDisplayDate } from '@/lib/utils/display-format'
import type { Skill, SkillKind } from '@/lib/types/skill'
import { SkillCard } from './skill-card'
import { SkillPageDialogs } from './skill-page-dialogs'
import { SkillStateFilterChips } from './skill-state-filter-chips'

type CreateTab = 'chat' | 'text' | 'package'
type BuilderMode = 'create' | 'improve'
type SkillTab = SkillKindFilter

const SKILL_TABS: readonly SkillTab[] = [ALL_SKILL_FILTER, 'text', 'package']

function isSkillTab(value: string): value is SkillTab {
  return value === ALL_SKILL_FILTER || value === 'text' || value === 'package'
}

function formatDate(value: string | null): string {
  if (!value) return ''
  return formatDisplayDate(value, { fallback: '' })
}

export function SkillsPageClient() {
  const t = useTranslations('skill')
  const router = useRouter()
  const startBuilder = useStartSkillBuilder()
  const [createOpen, setCreateOpen] = useState(false)
  const [createTab, setCreateTab] = useState<CreateTab>('chat')
  const [activeTab, setActiveTab] = useState<SkillTab>(ALL_SKILL_FILTER)
  const [stateFilter, setStateFilter] = useState<SkillStateFilter>(ALL_SKILL_FILTER)
  const [search, setSearch] = useState('')
  const normalizedSearch = search.trim().toLowerCase()
  const skillQueryParams = useMemo(() => {
    const params: { kind?: SkillKind; q?: string } = {}
    if (activeTab !== ALL_SKILL_FILTER) params.kind = activeTab
    if (normalizedSearch) params.q = normalizedSearch
    return Object.keys(params).length > 0 ? params : undefined
  }, [activeTab, normalizedSearch])
  const { data: skills, isLoading } = useSkills(skillQueryParams)
  const [publishSkill, setPublishSkill] = useState<Skill | null>(null)

  function openCreate(tab: CreateTab) {
    setCreateTab(tab)
    setCreateOpen(true)
  }

  // 상세는 다이얼로그가 아니라 스튜디오 라우트 — 레거시 `?detailId=` 진입은
  // page.tsx 서버 redirect가 흡수한다 (Phase 2).
  function openDetail(id: string) {
    router.push(`/skills/${id}/source`)
  }

  // 빌더 챗 (스킬 스튜디오 phase 1) — start v2로 세션+대화+워크스페이스를
  // 만들고 전용 라우트로 이동한다. 구 SkillBuilderDialog는 제거됨.
  async function startBuilderSession(payload: {
    mode: BuilderMode
    user_request: string
    source_skill_id?: string
  }) {
    try {
      const session = await startBuilder.mutateAsync(payload)
      setCreateOpen(false)
      router.push(`/skills/builder/${session.id}`)
    } catch {
      toast.error(t('builderChat.startFailed'))
    }
  }

  function openBuilderCreate(request: string) {
    void startBuilderSession({ mode: 'create', user_request: request })
  }

  const data = useMemo(() => skills ?? [], [skills])

  const filteredSkills = useMemo(() => {
    return filterSkillList(data, {
      kind: activeTab,
      state: stateFilter,
      query: normalizedSearch,
    })
  }, [activeTab, data, normalizedSearch, stateFilter])

  function countSkills(tab: SkillTab): number {
    return filterSkillList(data, {
      kind: tab,
      state: stateFilter,
      query: normalizedSearch,
    }).length
  }

  function countStateSkills(state: SkillStateFilter): number {
    return filterSkillList(data, {
      kind: activeTab,
      state,
      query: normalizedSearch,
    }).length
  }

  const tabs = SKILL_TABS.map((value) => ({
    value,
    label: value === ALL_SKILL_FILTER ? t('typeFilter.all') : t(`typeFilter.${value}`),
    countLabel: t('count', { count: countSkills(value) }),
  }))

  const stateFilters = SKILL_STATE_FILTERS.map((value) => ({
    value,
    label: t(`stateFilter.${value}`),
    countLabel: t('count', { count: countStateSkills(value) }),
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
                <SkillStateFilterChips
                  ariaLabel={t('stateFilter.label')}
                  value={stateFilter}
                  filters={stateFilters}
                  onValueChange={setStateFilter}
                  className="flex-1"
                />
                <SearchInput
                  containerClassName="flex-1 sm:max-w-sm"
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
                    <Skeleton key={index} className="moldy-skeleton-card h-48" />
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
                      onOpen={openDetail}
                      onPublish={setPublishSkill}
                    />
                  ))}
                </ResourceGrid>
              )}
            </ResourcePanel.Body>
          </>
        )}
      </ResourcePanel>

      <SkillPageDialogs
        createOpen={createOpen}
        createTab={createTab}
        publishSkill={publishSkill}
        onCreateOpenChange={setCreateOpen}
        onCreated={(id) => openDetail(id)}
        onStartChat={openBuilderCreate}
        onPublishOpenChange={(open) => !open && setPublishSkill(null)}
      />
    </ResourcePage>
  )
}
