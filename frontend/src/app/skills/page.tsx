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
import { coerceSkillDetailTab, type SkillDetailTab } from '@/components/skill/skill-detail-tabs'
import { SkillPageDialogs } from '@/components/skill/skill-page-dialogs'
import { useSkills } from '@/lib/hooks/use-skills'
import type { Skill, SkillKind } from '@/lib/types/skill'

type CreateTab = 'chat' | 'text' | 'package'
type BuilderMode = 'create' | 'improve'
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

export default function SkillsPage() {
  const t = useTranslations('skill')
  const [createOpen, setCreateOpen] = useState(false)
  const [createTab, setCreateTab] = useState<CreateTab>('chat')
  const [builderOpen, setBuilderOpen] = useState(false)
  const [builderMode, setBuilderMode] = useState<BuilderMode>('create')
  const [builderSourceSkillId, setBuilderSourceSkillId] = useState<string | null>(null)
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
  const [detailTab, setDetailTab] = useState<SkillDetailTab>(() => {
    if (typeof window === 'undefined') return 'content'
    return coerceSkillDetailTab(new URLSearchParams(window.location.search).get('tab'))
  })
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

  function openBuilderCreate(request: string) {
    setBuilderMode('create')
    setBuilderSourceSkillId(null)
    setBuilderInitialRequest(request)
    setBuilderOpen(true)
  }

  function openBuilderImprove(skillId: string) {
    setBuilderMode('improve')
    setBuilderSourceSkillId(skillId)
    setBuilderInitialRequest('')
    setBuilderOpen(true)
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
        builderOpen={builderOpen}
        builderMode={builderMode}
        builderSourceSkillId={builderSourceSkillId}
        builderInitialRequest={builderInitialRequest}
        detailId={detailId}
        detailTab={detailTab}
        publishSkill={publishSkill}
        onCreateOpenChange={setCreateOpen}
        onBuilderOpenChange={setBuilderOpen}
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
