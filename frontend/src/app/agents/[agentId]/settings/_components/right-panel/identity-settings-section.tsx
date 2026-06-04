'use client'

import { KeyRoundIcon, UserRoundIcon, type LucideIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import type { AgentIdentityMode } from '@/lib/types'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface IdentitySettingsSectionProps {
  identityMode: AgentIdentityMode
  onIdentityModeChange: (mode: AgentIdentityMode) => void
}

export function IdentitySettingsSection({
  identityMode,
  onIdentityModeChange,
}: IdentitySettingsSectionProps) {
  const t = useTranslations('agent.settings.identity')
  const options: {
    value: AgentIdentityMode
    icon: LucideIcon
    title: string
    description: string
  }[] = [
    {
      value: 'per_user',
      icon: UserRoundIcon,
      title: t('perUser.title'),
      description: t('perUser.description'),
    },
    {
      value: 'fixed',
      icon: KeyRoundIcon,
      title: t('fixed.title'),
      description: t('fixed.description'),
    },
  ]

  return (
    <section className="rounded-lg border px-4 py-3">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-medium">{t('title')}</div>
          <div className="mt-0.5 text-xs text-muted-foreground">{t('description')}</div>
        </div>
        <span className="moldy-status-surface rounded-md px-1.5 py-0.5 moldy-ui-micro font-medium">
          {identityMode}
        </span>
      </div>
      <div className="grid grid-cols-1 gap-2">
        {options.map((option) => {
          const Icon = option.icon
          const active = identityMode === option.value
          return (
            <Button
              key={option.value}
              type="button"
              variant={active ? 'secondary' : 'ghost'}
              aria-pressed={active}
              onClick={() => onIdentityModeChange(option.value)}
              className={cn(
                'h-auto justify-start gap-2 border border-transparent px-3 py-2 text-left',
                active && 'border-primary/30',
              )}
            >
              <Icon className="size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0">
                <span className="block text-xs font-medium">{option.title}</span>
                <span className="block text-xs font-normal text-muted-foreground">
                  {option.description}
                </span>
              </span>
            </Button>
          )
        })}
      </div>
    </section>
  )
}
