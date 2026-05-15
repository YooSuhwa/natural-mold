import type { ReactNode } from 'react'
import { getTranslations } from 'next-intl/server'

import { Card } from '@/components/ui/card'

export default async function AuthLayout({ children }: { children: ReactNode }) {
  const t = await getTranslations('auth.login.benefits')
  return (
    <div className="grid min-h-screen w-full lg:grid-cols-2">
      {/* Left intro column — hidden on small screens for focus. */}
      <aside className="hidden lg:flex flex-col justify-center bg-muted/30 px-12 py-16">
        <div className="max-w-md space-y-6">
          <div>
            <p className="text-sm font-medium text-muted-foreground">Moldy</p>
            <h2 className="mt-2 text-3xl font-semibold tracking-tight">{t('title')}</h2>
          </div>
          <ul className="space-y-2 text-muted-foreground">
            <li className="flex items-baseline gap-2">
              <span aria-hidden className="text-primary-strong">·</span>
              <span>{t('item1')}</span>
            </li>
            <li className="flex items-baseline gap-2">
              <span aria-hidden className="text-primary-strong">·</span>
              <span>{t('item2')}</span>
            </li>
            <li className="flex items-baseline gap-2">
              <span aria-hidden className="text-primary-strong">·</span>
              <span>{t('item3')}</span>
            </li>
          </ul>
        </div>
      </aside>

      {/* Right form column. */}
      <main className="flex flex-col items-center justify-center px-6 py-12 sm:px-10">
        <Card className="w-full max-w-[420px] rounded-2xl border bg-card shadow-sm p-8 sm:p-8">
          {children}
        </Card>
      </main>
    </div>
  )
}
