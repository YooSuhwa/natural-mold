'use client'

import { BookOpenIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import type { SkillBrief } from '@/lib/types'

interface Props {
  skills: SkillBrief[] | undefined
}

/**
 * Compact row that surfaces an agent's attached Skills above the chat thread.
 * Renders nothing when the agent has no skills attached so it stays invisible
 * for legacy / un-skilled agents.
 */
export function AgentSkillsRow({ skills }: Props) {
  const t = useTranslations('chat.skillsRow')
  if (!skills || skills.length === 0) return null

  return (
    <div className="flex items-center gap-2 border-b border-border/60 bg-primary/35 px-4 py-2 text-xs">
      <BookOpenIcon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
      <span className="shrink-0 font-medium text-muted-foreground">{t('label')}</span>
      <div className="flex flex-wrap gap-1.5">
        {skills.map((s) => (
          <span
            key={s.id}
            title={s.description ?? s.name}
            className="inline-flex items-center rounded-full bg-primary-strong/10 px-2 py-0.5 text-xs font-medium text-primary-strong"
          >
            {s.name}
          </span>
        ))}
      </div>
    </div>
  )
}
