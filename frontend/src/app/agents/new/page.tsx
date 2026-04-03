'use client'

import Link from 'next/link'
import { MessageSquareIcon, LayoutTemplateIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { PageHeader } from '@/components/shared/page-header'

export default function AgentNewPage() {
  const t = useTranslations('agent.new')

  return (
    <div className="flex flex-1 flex-col gap-8 overflow-auto p-6">
      <PageHeader title={t('pageTitle')} />

      <div className="mx-auto grid w-full max-w-3xl gap-6 sm:grid-cols-2">
        <Card className="cursor-pointer transition-colors hover:border-primary/40">
          <CardHeader className="text-center">
            <div className="mx-auto mb-2 flex size-12 items-center justify-center rounded-full bg-primary/10 text-primary">
              <MessageSquareIcon className="size-6" />
            </div>
            <CardTitle>{t('conversational.title')}</CardTitle>
            <CardDescription>{t('conversational.description')}</CardDescription>
          </CardHeader>
          <CardContent className="flex justify-center">
            <Link href="/agents/new/conversational">
              <Button>{t('conversational.startButton')}</Button>
            </Link>
          </CardContent>
        </Card>

        <Card className="cursor-pointer transition-colors hover:border-primary/40">
          <CardHeader className="text-center">
            <div className="mx-auto mb-2 flex size-12 items-center justify-center rounded-full bg-primary/10 text-primary">
              <LayoutTemplateIcon className="size-6" />
            </div>
            <CardTitle>{t('template.title')}</CardTitle>
            <CardDescription>{t('template.description')}</CardDescription>
          </CardHeader>
          <CardContent className="flex justify-center">
            <Link href="/agents/new/template">
              <Button variant="outline">{t('template.browseButton')}</Button>
            </Link>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
