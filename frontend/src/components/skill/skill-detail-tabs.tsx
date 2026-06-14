'use client'

import { FileText, History, KeyRound, ListChecks, Settings2 } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { LineTabsList, LineTabsTrigger } from '@/components/ui/line-tabs'

export type SkillDetailTab = 'content' | 'credentials' | 'evaluation' | 'history' | 'metadata'

const TABS: readonly SkillDetailTab[] = [
  'content',
  'credentials',
  'evaluation',
  'history',
  'metadata',
]

function iconForTab(tab: SkillDetailTab) {
  switch (tab) {
    case 'content':
      return FileText
    case 'credentials':
      return KeyRound
    case 'evaluation':
      return ListChecks
    case 'history':
      return History
    case 'metadata':
      return Settings2
  }
}

export function coerceSkillDetailTab(value: string | null | undefined): SkillDetailTab {
  if (!value) return 'content'
  switch (value) {
    case 'content':
    case 'credentials':
    case 'evaluation':
    case 'history':
    case 'metadata':
      return value
    default:
      return 'content'
  }
}

export function SkillDetailTabs({
  visibleTabs = TABS,
}: {
  readonly visibleTabs?: readonly SkillDetailTab[]
}) {
  const t = useTranslations('skill.detailDialog.tabs')

  return (
    <div className="border-b border-border/60 px-6">
      <LineTabsList className="w-full justify-start overflow-x-auto">
        {visibleTabs.map((tab) => {
          const Icon = iconForTab(tab)
          return (
            <LineTabsTrigger key={tab} value={tab}>
              <Icon className="size-3.5" />
              {t(tab)}
            </LineTabsTrigger>
          )
        })}
      </LineTabsList>
    </div>
  )
}
