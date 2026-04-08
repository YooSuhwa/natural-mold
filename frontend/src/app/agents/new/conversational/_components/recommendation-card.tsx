'use client'

import type { LucideIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

interface RecommendationItem {
  name: string
  description: string
  reason: string
}

interface RecommendationCardProps {
  icon: LucideIcon
  titleKey: string
  items: RecommendationItem[]
}

export function RecommendationCard({ icon: Icon, titleKey, items }: RecommendationCardProps) {
  const t = useTranslations('agent.creation')
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Icon className="size-4 text-primary" />
          {t(titleKey)} ({items.length})
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {items.map((item) => (
          <div key={item.name} className="flex gap-3 rounded-xl border bg-background p-4">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
              <Icon className="size-4" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold">{item.name}</p>
              <p className="mt-0.5 text-sm text-muted-foreground leading-relaxed">
                {item.description}
              </p>
              <p className="mt-1 text-xs text-primary/80">
                {t('toolReason')}: {item.reason}
              </p>
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  )
}
