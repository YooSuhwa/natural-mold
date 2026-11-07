'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'

import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useAgents } from '@/lib/hooks/use-agents'
import { useMemories, useMemoryPolicy, useUserMemorySettings } from '@/lib/hooks/use-memory'
import type { UserMemorySettings } from '@/lib/types'
import { MemoryActivityTab } from './memory-activity-tab'
import { MemoryFirstUseNotice } from './memory-first-use-notice'
import type { MemoryAgentOption } from './memory-form-fields'
import { MemoryOverviewTab } from './memory-overview-tab'
import { MemoryRecordsTab } from './memory-records-tab'
import { MemorySettingsTab } from './memory-settings-tab'

const TABS = ['overview', 'memories', 'activity', 'settings'] as const
type MemoryTab = (typeof TABS)[number]

/**
 * `/settings/memory` (plan 15, 5.9): 개요 / 기억 / 활동 / 설정. Only the person's own
 * memories, and the same screens for company and personal tenants (Q9).
 */
export function MemorySettingsContent() {
  const t = useTranslations('appSettings.memory')
  const { data: settings, isLoading } = useUserMemorySettings()

  return (
    <div className="space-y-4" data-testid="memory-settings-page">
      <section className="space-y-1">
        <h2 className="text-lg font-semibold text-foreground">{t('title')}</h2>
        <p className="text-sm leading-6 text-muted-foreground">{t('description')}</p>
      </section>

      {isLoading || !settings ? (
        <SettingsSectionCard title={t('title')} description={t('description')}>
          <p className="text-sm text-muted-foreground">{t('loading')}</p>
        </SettingsSectionCard>
      ) : (
        <MemoryTabs settings={settings} />
      )}
    </div>
  )
}

function MemoryTabs({ settings }: { settings: UserMemorySettings }) {
  const t = useTranslations('appSettings.memory')
  const [tab, setTab] = useState<MemoryTab>('overview')
  const { data: policy } = useMemoryPolicy()
  const { data: agentsData, isLoading: agentsLoading } = useAgents()
  const agents: MemoryAgentOption[] = (agentsData ?? []).map(({ id, name }) => ({ id, name }))
  const activeMemories = useMemories({ status: 'active' })
  const showFirstUse =
    settings.approval_mode === null &&
    !activeMemories.isLoading &&
    (activeMemories.data?.length ?? 0) === 0

  return (
    <div className="space-y-4">
      {showFirstUse ? <MemoryFirstUseNotice onChooseMode={() => setTab('settings')} /> : null}
      <Tabs value={tab} onValueChange={(value) => setTab(value as MemoryTab)}>
        <TabsList variant="line" className="h-auto" aria-label={t('tabs.label')}>
          {TABS.map((value) => (
            <TabsTrigger key={value} value={value} className="px-4 py-2">
              {t(`tabs.${value}`)}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="overview" className="pt-2">
          <MemoryOverviewTab settings={settings} policy={policy} />
        </TabsContent>
        <TabsContent value="memories" className="pt-2">
          <MemoryRecordsTab agents={agents} agentsLoading={agentsLoading} />
        </TabsContent>
        <TabsContent value="activity" className="pt-2">
          <MemoryActivityTab />
        </TabsContent>
        <TabsContent value="settings" className="pt-2">
          <MemorySettingsTab settings={settings} policy={policy} agents={agents} />
        </TabsContent>
      </Tabs>
    </div>
  )
}
