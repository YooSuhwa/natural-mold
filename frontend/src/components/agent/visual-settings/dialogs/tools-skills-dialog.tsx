'use client'

import { useMemo, useState, type ComponentType, type SVGProps } from 'react'
import { PackageIcon, ServerIcon, SparklesIcon, WrenchIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { DialogShell } from '@/components/shared/dialog-shell'
import { LineTabsList, LineTabsTrigger } from '@/components/ui/line-tabs'
import { Tabs, TabsContent } from '@/components/ui/tabs'
import { useAllMcpTools } from '@/lib/hooks/use-mcp-servers'

import { CatalogPanel } from './tools-skills-catalog-panel'
import { CurrentColumn } from './tools-skills-current-column'
import type {
  DialogTab,
  ToolsSkillsDialogMode,
  ToolsSkillsDialogProps,
} from './tools-skills-dialog-types'
import { McpPanel, SkillsPanel, ToolsPanel } from './tools-skills-resource-panels'

export type { ToolsSkillsDialogMode } from './tools-skills-dialog-types'

const MODE_META: Record<
  ToolsSkillsDialogMode,
  {
    readonly titleKey: string
    readonly descriptionKey: string
    readonly IconComponent: ComponentType<SVGProps<SVGSVGElement>>
  }
> = {
  all: {
    titleKey: 'mode.all.title',
    descriptionKey: 'mode.all.description',
    IconComponent: WrenchIcon,
  },
  tools: {
    titleKey: 'mode.tools.title',
    descriptionKey: 'mode.tools.description',
    IconComponent: WrenchIcon,
  },
  skills: {
    titleKey: 'mode.skills.title',
    descriptionKey: 'mode.skills.description',
    IconComponent: SparklesIcon,
  },
}

function coerceDialogTab(value: string): DialogTab {
  switch (value) {
    case 'catalog':
    case 'tools':
    case 'mcp':
    case 'skills':
      return value
    default:
      return 'catalog'
  }
}

export function ToolsSkillsDialog({
  open,
  onOpenChange,
  allTools,
  selectedToolIds,
  onToggleTool,
  selectedMcpToolIds,
  onToggleMcpTool,
  allSkills,
  selectedSkillIds,
  onToggleSkill,
  defaultTab = 'catalog',
  mode = 'all',
}: ToolsSkillsDialogProps) {
  const initialTab: DialogTab = mode === 'tools' && defaultTab === 'skills' ? 'tools' : defaultTab
  const [tab, setTab] = useState<DialogTab>(initialTab)
  const { data: allMcpTools } = useAllMcpTools()

  const selectedTools = useMemo(
    () => allTools.filter((tool) => selectedToolIds.has(tool.id)),
    [allTools, selectedToolIds],
  )
  const selectedMcpTools = useMemo(
    () => (allMcpTools ?? []).filter((tool) => selectedMcpToolIds.has(tool.id)),
    [allMcpTools, selectedMcpToolIds],
  )
  const selectedSkills = useMemo(
    () => allSkills.filter((skill) => selectedSkillIds.has(skill.id)),
    [allSkills, selectedSkillIds],
  )

  const t = useTranslations('agent.visualSettings.toolsSkillsDialog')
  const { titleKey, descriptionKey, IconComponent } = MODE_META[mode]
  const showSkills = mode !== 'tools'
  const showToolsAndMcp = mode !== 'skills'
  const totalSelected =
    (showToolsAndMcp ? selectedTools.length + selectedMcpTools.length : 0) +
    (showSkills ? selectedSkills.length : 0)

  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="console" height="tall">
      <DialogShell.Header
        icon={<IconComponent className="size-5" />}
        title={t(titleKey)}
        description={t(descriptionKey)}
      />
      <DialogShell.Body className="flex flex-col">
        <div className="grid min-h-0 flex-1 gap-6 md:grid-cols-2">
          <CurrentColumn
            total={totalSelected}
            tools={showToolsAndMcp ? selectedTools : []}
            mcpTools={showToolsAndMcp ? selectedMcpTools : []}
            skills={showSkills ? selectedSkills : []}
            onRemoveTool={onToggleTool}
            onRemoveMcp={onToggleMcpTool}
            onRemoveSkill={onToggleSkill}
          />

          <section className="flex min-h-0 flex-col">
            {mode === 'skills' ? (
              <SkillsPanel
                allSkills={allSkills}
                selectedSkillIds={selectedSkillIds}
                onToggle={onToggleSkill}
              />
            ) : (
              <Tabs
                value={tab}
                onValueChange={(value) => setTab(coerceDialogTab(value))}
                className="flex min-h-0 w-full flex-1 flex-col"
              >
                <LineTabsList className="w-full justify-start">
                  <LineTabsTrigger value="catalog">
                    <PackageIcon className="size-3.5" /> {t('tabs.catalog')}
                  </LineTabsTrigger>
                  <LineTabsTrigger value="tools">
                    <WrenchIcon className="size-3.5" /> {t('tabs.tools')}
                  </LineTabsTrigger>
                  <LineTabsTrigger value="mcp">
                    <ServerIcon className="size-3.5" /> {t('tabs.mcp')}
                  </LineTabsTrigger>
                  {mode === 'all' ? (
                    <LineTabsTrigger value="skills">
                      <SparklesIcon className="size-3.5" /> {t('tabs.skills')}
                    </LineTabsTrigger>
                  ) : null}
                </LineTabsList>

                <TabsContent value="catalog" className="min-w-0 flex-1 overflow-y-auto pt-3">
                  <CatalogPanel />
                </TabsContent>
                <TabsContent value="tools" className="min-w-0 flex-1 overflow-y-auto pt-3">
                  <ToolsPanel
                    allTools={allTools}
                    selectedToolIds={selectedToolIds}
                    onToggle={onToggleTool}
                  />
                </TabsContent>
                <TabsContent value="mcp" className="min-w-0 flex-1 overflow-y-auto pt-3">
                  <McpPanel selectedIds={selectedMcpToolIds} onToggle={onToggleMcpTool} />
                </TabsContent>
                {mode === 'all' ? (
                  <TabsContent value="skills" className="min-w-0 flex-1 overflow-y-auto pt-3">
                    <SkillsPanel
                      allSkills={allSkills}
                      selectedSkillIds={selectedSkillIds}
                      onToggle={onToggleSkill}
                    />
                  </TabsContent>
                ) : null}
              </Tabs>
            )}
          </section>
        </div>
      </DialogShell.Body>
    </DialogShell>
  )
}
