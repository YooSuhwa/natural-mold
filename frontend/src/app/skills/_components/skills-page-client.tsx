'use client'

import { useMemo, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
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
import { coerceSkillDetailTab, type SkillDetailTab } from '@/components/skill/skill-detail-tabs'
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

function replaceDetailUrl(skillId: string | null, tab: SkillDetailTab) {
  if (typeof window === 'undefined') return
  if (!skillId) {
    window.history.replaceState(null, '', '/skills')
    return
  }
  const params = new URLSearchParams()
  params.set('detailId', skillId)
  if (tab !== 'content') params.set('tab', tab)
  window.history.replaceState(null, '', `/skills?${params.toString()}`)
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
  // Deep-link (`/skills?detailId=...`) — /marketplace Open 버튼, 빌더 챗 레일의
  // "스킬 열기" 링크. 초기값은 반드시 useSearchParams(라우터 상태)에서 읽어야
  // 한다: 클라이언트 네비게이션 중에는 컴포넌트가 window.location 갱신 **전에**
  // 마운트될 수 있어 location 기반 초기화는 딥링크를 놓친다(빌더 레일 링크에서
  // 실제 재현). useState lazy initializer 유지 — 이후에는 로컬 상태가 소스
  // (URL 동기화는 replaceDetailUrl의 history.replaceState). effect+setState
  // 패턴(react-hooks/set-state-in-effect 거부)은 계속 회피한다.
  const searchParams = useSearchParams()
  const [detailId, setDetailId] = useState<string | null>(() => searchParams.get('detailId'))
  const [detailTab, setDetailTab] = useState<SkillDetailTab>(() =>
    coerceSkillDetailTab(searchParams.get('tab')),
  )
  const [publishSkill, setPublishSkill] = useState<Skill | null>(null)

  function openCreate(tab: CreateTab) {
    setCreateTab(tab)
    setCreateOpen(true)
  }

  function openDetail(id: string, tab: SkillDetailTab = 'content') {
    setDetailId(id)
    setDetailTab(tab)
    replaceDetailUrl(id, tab)
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
      setDetailId(null)
      router.push(`/skills/builder/${session.id}`)
    } catch {
      toast.error(t('builderChat.startFailed'))
    }
  }

  function openBuilderCreate(request: string) {
    void startBuilderSession({ mode: 'create', user_request: request })
  }

  function openBuilderImprove(skillId: string) {
    void startBuilderSession({
      mode: 'improve',
      user_request: t('builderChat.improveDefaultRequest'),
      source_skill_id: skillId,
    })
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
        detailId={detailId}
        detailTab={detailTab}
        publishSkill={publishSkill}
        onCreateOpenChange={setCreateOpen}
        onCreated={(id, tab = 'content') => openDetail(id, tab)}
        onStartChat={openBuilderCreate}
        onDetailTabChange={(tab) => {
          setDetailTab(tab)
          replaceDetailUrl(detailId, tab)
        }}
        onImprove={openBuilderImprove}
        onDetailOpenChange={(open) => {
          if (open) return
          setDetailId(null)
          setDetailTab('content')
          replaceDetailUrl(null, 'content')
        }}
        onPublishOpenChange={(open) => !open && setPublishSkill(null)}
      />
    </ResourcePage>
  )
}
