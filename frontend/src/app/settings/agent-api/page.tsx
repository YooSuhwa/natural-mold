import { useTranslations } from 'next-intl'
import { Code2Icon } from 'lucide-react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { SettingsShell } from '../_components/settings-shell'

export default function AgentApiSettingsPage() {
  const t = useTranslations('appSettings.agentApi')

  return (
    <SettingsShell>
      <div className="space-y-4">
        <section className="space-y-1">
          <h2 className="text-lg font-semibold text-foreground">{t('title')}</h2>
          <p className="text-sm leading-6 text-muted-foreground">{t('description')}</p>
        </section>

        <Card>
          <CardHeader>
            <div className="flex items-center justify-between gap-3">
              <CardTitle className="flex items-center gap-2">
                <Code2Icon className="size-4" aria-hidden />
                {t('placeholder.title')}
              </CardTitle>
              <Badge variant="outline">{t('status')}</Badge>
            </div>
            <CardDescription>{t('placeholder.description')}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="rounded-lg border border-border/60 bg-muted/30 p-3">
              <p className="text-xs font-medium text-muted-foreground">{t('futureEndpoint')}</p>
              <p className="mt-1 font-mono text-xs text-foreground">
                POST /v1/agents/{'{public_id}'}/chat-messages
              </p>
            </div>
            <p className="text-sm text-muted-foreground">{t('placeholder.detail')}</p>
          </CardContent>
        </Card>
      </div>
    </SettingsShell>
  )
}
