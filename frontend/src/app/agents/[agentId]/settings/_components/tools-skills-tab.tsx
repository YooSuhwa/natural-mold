import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { useTools } from '@/lib/hooks/use-tools'
import { useSkills } from '@/lib/hooks/use-skills'
import { useMiddlewares } from '@/lib/hooks/use-middlewares'
import { Checkbox } from '@/components/ui/checkbox'
import { Skeleton } from '@/components/ui/skeleton'

interface ToolsSkillsTabProps {
  selectedToolIds: Set<string>
  onToggleTool: (id: string) => void
  selectedSkillIds: Set<string>
  onToggleSkill: (id: string) => void
  selectedMiddlewareTypes: Set<string>
  onToggleMiddleware: (type: string) => void
}

export function ToolsSkillsTab({
  selectedToolIds,
  onToggleTool,
  selectedSkillIds,
  onToggleSkill,
  selectedMiddlewareTypes,
  onToggleMiddleware,
}: ToolsSkillsTabProps) {
  const t = useTranslations('agent.settings')
  const { data: tools } = useTools()
  const { data: skills } = useSkills()
  const { data: middlewares } = useMiddlewares()

  const noToolsParts = String(t.raw('noTools')).split('{link}')
  const noSkillsParts = String(t.raw('noSkills')).split('{link}')

  return (
    <div className="space-y-6">
      {/* Tools */}
      <div className="space-y-2">
        <label className="text-sm font-medium">{t('tools')}</label>
        {tools ? (
          tools.length > 0 ? (
            <div className="space-y-2 rounded-lg border p-3">
              {tools.map((tool) => (
                <label key={tool.id} className="flex items-center gap-3 text-sm cursor-pointer">
                  <Checkbox
                    checked={selectedToolIds.has(tool.id)}
                    onCheckedChange={() => onToggleTool(tool.id)}
                  />
                  <span>{tool.name}</span>
                  {tool.description && (
                    <span className="text-xs text-muted-foreground">- {tool.description}</span>
                  )}
                </label>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              {noToolsParts[0]}
              <Link href="/tools" className="text-primary hover:underline">
                {t('toolsLink')}
              </Link>
              {noToolsParts[1]}
            </p>
          )
        ) : (
          <Skeleton className="h-16 w-full" />
        )}
      </div>

      {/* Skills */}
      <div className="space-y-2">
        <label className="text-sm font-medium">{t('skills')}</label>
        {skills ? (
          skills.length > 0 ? (
            <div className="space-y-2 rounded-lg border p-3">
              {skills.map((skill) => (
                <label key={skill.id} className="flex items-center gap-3 text-sm cursor-pointer">
                  <Checkbox
                    checked={selectedSkillIds.has(skill.id)}
                    onCheckedChange={() => onToggleSkill(skill.id)}
                  />
                  <span>{skill.name}</span>
                  {skill.description && (
                    <span className="text-xs text-muted-foreground">- {skill.description}</span>
                  )}
                </label>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              {noSkillsParts[0]}
              <Link href="/skills" className="text-primary hover:underline">
                {t('skillsLink')}
              </Link>
              {noSkillsParts[1]}
            </p>
          )
        ) : (
          <Skeleton className="h-16 w-full" />
        )}
      </div>

      {/* Middlewares */}
      <div className="space-y-2">
        <label className="text-sm font-medium">{t('middlewares')}</label>
        {middlewares ? (
          middlewares.length > 0 ? (
            <div className="space-y-2 rounded-lg border p-3">
              {middlewares.map((mw) => (
                <label key={mw.type} className="flex items-center gap-3 text-sm cursor-pointer">
                  <Checkbox
                    checked={selectedMiddlewareTypes.has(mw.type)}
                    onCheckedChange={() => onToggleMiddleware(mw.type)}
                  />
                  <span>{mw.display_name}</span>
                  {mw.description && (
                    <span className="text-xs text-muted-foreground">- {mw.description}</span>
                  )}
                </label>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">{t('noMiddlewares')}</p>
          )
        ) : (
          <Skeleton className="h-16 w-full" />
        )}
      </div>
    </div>
  )
}
