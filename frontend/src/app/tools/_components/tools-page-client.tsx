'use client'

import { useMemo, useState } from 'react'
import { Plus, Wrench } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/shared/empty-state'
import { SearchInput } from '@/components/shared/search-input'
import {
  CountedLineTabs,
  ResourcePage,
  ResourcePanel,
  ResourceToolbar,
} from '@/components/shared/resource-layout'
import { useTools, useToolTypes } from '@/lib/hooks/use-tools'
import { useCredentials } from '@/lib/hooks/use-credentials'
import type { ToolDefinition } from '@/lib/types/tool'
import { InstalledToolCatalog, ToolCatalog } from './tool-catalog'
import { ToolCreateDialog } from './tool-create-dialog'
import { ToolDetailDialog } from './tool-detail-dialog'

const ALL_TAB = 'all'
const INSTALLED_TAB = 'installed'

export function ToolsPageClient() {
  const t = useTranslations('tool.page')
  const tCatalog = useTranslations('tool.catalog')
  const { data: tools, isLoading: isToolsLoading } = useTools()
  const { data: definitions, isLoading: isDefinitionsLoading } = useToolTypes()
  const { data: credentials } = useCredentials()
  const [activeTab, setActiveTab] = useState<string>(ALL_TAB)
  const [search, setSearch] = useState('')
  const [pickedDefinition, setPickedDefinition] = useState<ToolDefinition | null>(null)
  const [detailId, setDetailId] = useState<string | null>(null)

  const categories = useMemo(() => {
    const set = new Set<string>()
    definitions?.forEach((d) => set.add(d.category || 'general'))
    return [ALL_TAB, ...Array.from(set).sort(), INSTALLED_TAB]
  }, [definitions])

  const definitionLabels = useMemo(() => {
    const m = new Map<string, ToolDefinition>()
    definitions?.forEach((d) => m.set(d.key, d))
    return m
  }, [definitions])

  const credentialMap = useMemo(() => {
    const m = new Map<string, string>()
    credentials?.forEach((c) => m.set(c.id, c.status))
    return m
  }, [credentials])

  const normalizedSearch = search.trim().toLowerCase()

  const filteredTools = useMemo(() => {
    if (!tools) return []
    if (!normalizedSearch) return tools
    return tools.filter((tool) => {
      const definition = definitionLabels.get(tool.definition_key)
      return [
        tool.name,
        tool.description,
        tool.definition_key,
        definition?.display_name,
        definition?.description,
      ]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(normalizedSearch))
    })
  }, [definitionLabels, normalizedSearch, tools])

  function formatTabLabel(value: string): string {
    if (value === INSTALLED_TAB) return t('tabs.installed')
    if (
      value === 'all' ||
      value === 'general' ||
      value === 'search' ||
      value === 'productivity' ||
      value === 'communication' ||
      value === 'automation'
    ) {
      return tCatalog(`categories.${value}`)
    }
    return value
  }

  function countDefinitions(value: string): number {
    if (!definitions) return 0
    return definitions.filter((definition) => {
      if (value !== ALL_TAB && (definition.category || 'general') !== value) return false
      if (!normalizedSearch) return true
      return (
        definition.display_name.toLowerCase().includes(normalizedSearch) ||
        definition.description.toLowerCase().includes(normalizedSearch) ||
        definition.key.toLowerCase().includes(normalizedSearch)
      )
    }).length
  }

  const tabs = categories.map((value) => ({
    value,
    label: formatTabLabel(value),
    countLabel: t('count', {
      count: value === INSTALLED_TAB ? filteredTools.length : countDefinitions(value),
    }),
  }))

  const isInstalledTab = activeTab === INSTALLED_TAB
  const hasInstalledTools = (tools ?? []).length > 0

  return (
    <ResourcePage title={t('title')} description={t('description')}>
      <ResourcePanel>
        <ResourcePanel.Toolbar>
          <CountedLineTabs
            ariaLabel={t('viewMode')}
            value={activeTab}
            tabs={tabs}
            onValueChange={setActiveTab}
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
          {isInstalledTab ? (
            !isToolsLoading && filteredTools.length === 0 ? (
              <EmptyState
                icon={<Wrench className="size-6" />}
                title={hasInstalledTools ? t('empty.filtered') : t('empty.title')}
                description={hasInstalledTools ? undefined : t('empty.description')}
                className="bg-card/50"
                action={
                  hasInstalledTools ? undefined : (
                    <Button onClick={() => setActiveTab(ALL_TAB)}>
                      <Plus className="size-4" />
                      {t('empty.action')}
                    </Button>
                  )
                }
              />
            ) : (
              <InstalledToolCatalog
                tools={filteredTools}
                definitions={definitions}
                credentialStatuses={credentialMap}
                isLoading={isToolsLoading}
                onOpen={(tool) => setDetailId(tool.id)}
              />
            )
          ) : (
            <ToolCatalog
              category={activeTab}
              definitions={definitions}
              isLoading={isDefinitionsLoading}
              search={search}
              onPick={setPickedDefinition}
            />
          )}
        </ResourcePanel.Body>
      </ResourcePanel>

      <ToolCreateDialog
        definition={pickedDefinition}
        open={!!pickedDefinition}
        onOpenChange={(open) => !open && setPickedDefinition(null)}
        onCreated={() => setActiveTab(INSTALLED_TAB)}
      />
      <ToolDetailDialog
        toolId={detailId}
        open={!!detailId}
        onOpenChange={(open) => !open && setDetailId(null)}
      />
    </ResourcePage>
  )
}
