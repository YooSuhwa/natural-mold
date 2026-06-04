import { useTranslations } from 'next-intl'
import { ShieldCheckIcon } from 'lucide-react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { SettingsShell } from '../_components/settings-shell'

export default function SecuritySettingsPage() {
  const t = useTranslations('appSettings.security')

  return (
    <SettingsShell>
      <div className="space-y-4">
        <section className="space-y-1">
          <h2 className="text-lg font-semibold text-foreground">{t('title')}</h2>
          <p className="text-sm leading-6 text-muted-foreground">{t('description')}</p>
        </section>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ShieldCheckIcon className="size-4" aria-hidden />
              {t('placeholder.title')}
            </CardTitle>
            <CardDescription>{t('placeholder.description')}</CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">{t('placeholder.detail')}</p>
          </CardContent>
        </Card>
      </div>
    </SettingsShell>
  )
}
