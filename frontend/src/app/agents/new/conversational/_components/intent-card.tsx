'use client'

import { CpuIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import type { BuilderIntent } from '@/lib/types'

export function IntentCard({ intent }: { intent: BuilderIntent }) {
  const t = useTranslations('agent.creation')
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <CpuIcon className="size-4 text-primary" />
          {t('intentTitle')}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="space-y-2 rounded-lg bg-muted/50 p-4 text-sm">
          <div className="flex justify-between">
            <span className="text-muted-foreground">{t('draftName')}</span>
            <span className="font-medium">{intent.agent_name_ko}</span>
          </div>
          <div>
            <span className="text-muted-foreground">{t('intentDescription')}</span>
            <p className="mt-1">{intent.agent_description}</p>
          </div>
          {intent.use_cases.length > 0 && (
            <div>
              <span className="text-muted-foreground">{t('intentUseCases')}</span>
              <ul className="mt-1 list-inside list-disc space-y-0.5">
                {intent.use_cases.map((uc) => (
                  <li key={uc}>{uc}</li>
                ))}
              </ul>
            </div>
          )}
          {intent.required_capabilities.length > 0 && (
            <div>
              <span className="text-muted-foreground">{t('intentCapabilities')}</span>
              <ul className="mt-1 list-inside list-disc space-y-0.5">
                {intent.required_capabilities.map((cap) => (
                  <li key={cap}>{cap}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
