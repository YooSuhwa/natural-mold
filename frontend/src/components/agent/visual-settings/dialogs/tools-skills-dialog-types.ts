import type { CredentialDefinition } from '@/lib/types/credential'
import type { McpToolWithServer } from '@/lib/types/mcp'
import type { Skill } from '@/lib/types/skill'
import type { ToolInstance } from '@/lib/types/tool'

export type SelectedKind = 'tool' | 'mcp' | 'skill'
export type AvailableKind = SelectedKind | 'catalog'
export type DialogTab = 'catalog' | 'tools' | 'mcp' | 'skills'
export type ToolsSkillsDialogMode = 'all' | 'tools' | 'skills'

export type ToolsSkillsDialogProps = {
  readonly open: boolean
  readonly onOpenChange: (value: boolean) => void
  readonly allTools: readonly ToolInstance[]
  readonly selectedToolIds: ReadonlySet<string>
  readonly onToggleTool: (toolId: string) => void
  readonly selectedMcpToolIds: ReadonlySet<string>
  readonly onToggleMcpTool: (toolId: string) => void
  readonly allSkills: readonly Skill[]
  readonly selectedSkillIds: ReadonlySet<string>
  readonly onToggleSkill: (skillId: string) => void
  readonly defaultTab?: DialogTab
  readonly mode?: ToolsSkillsDialogMode
}

export type CatalogDefinition = CredentialDefinition
export type AvailableTool = ToolInstance
export type AvailableMcpTool = McpToolWithServer
