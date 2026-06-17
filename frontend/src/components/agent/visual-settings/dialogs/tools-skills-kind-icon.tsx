'use client'

import { PackageIcon, ServerIcon, SparklesIcon, WrenchIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import type { AvailableKind } from './tools-skills-dialog-types'

export function KindIcon({ kind }: { readonly kind: AvailableKind }) {
  const t = useTranslations('agent.visualSettings.toolsSkillsDialog.kind')
  const config = {
    tool: {
      Icon: WrenchIcon,
      className: 'moldy-dashboard-action-icon moldy-status-accent',
      label: t('tool'),
    },
    mcp: {
      Icon: ServerIcon,
      className: 'moldy-dashboard-action-icon moldy-status-info',
      label: t('mcp'),
    },
    skill: {
      Icon: SparklesIcon,
      className: 'moldy-dashboard-action-icon moldy-status-success',
      label: t('skill'),
    },
    catalog: {
      Icon: PackageIcon,
      className: 'moldy-dashboard-action-icon moldy-status-warn',
      label: t('catalog'),
    },
  }[kind]
  const { Icon, className, label } = config

  return (
    <span
      className={`flex size-8 shrink-0 items-center justify-center rounded-md ${className}`}
      aria-label={label}
      title={label}
    >
      <Icon className="size-4" />
    </span>
  )
}
