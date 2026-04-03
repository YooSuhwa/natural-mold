'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { Loader2Icon, LayoutTemplateIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useTemplates } from '@/lib/hooks/use-templates'
import { useCreateAgent } from '@/lib/hooks/use-agents'
import { useModels } from '@/lib/hooks/use-models'
import { Button } from '@/components/ui/button'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState } from '@/components/shared/empty-state'
import { PageHeader } from '@/components/shared/page-header'
import type { Template } from '@/lib/types'

export default function TemplateSelectionPage() {
  const router = useRouter()
  const t = useTranslations('agent.template')
  const [selectedCategory, setSelectedCategory] = useState('')

  const categories = [
    { value: '', label: t('category.all') },
    { value: '생산성', label: t('category.productivity') },
    { value: '커뮤니케이션', label: t('category.communication') },
    { value: '데이터', label: t('category.data') },
  ]
  const { data: templates, isLoading } = useTemplates(selectedCategory || undefined)
  const { data: models } = useModels()
  const createAgent = useCreateAgent()
  const [creatingId, setCreatingId] = useState<string | null>(null)

  const defaultModelId = models?.find((m) => m.is_default)?.id ?? models?.[0]?.id ?? ''

  async function handleCreateFromTemplate(template: Template) {
    setCreatingId(template.id)
    try {
      const agent = await createAgent.mutateAsync({
        name: template.name,
        description: template.description ?? undefined,
        system_prompt: template.system_prompt,
        model_id: template.recommended_model_id ?? defaultModelId,
        template_id: template.id,
      })
      router.push(`/agents/${agent.id}`)
    } catch {
      setCreatingId(null)
    }
  }

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      <PageHeader title={t('pageTitle')} />

      <Tabs defaultValue="" onValueChange={(val) => setSelectedCategory(val as string)}>
        <TabsList>
          {categories.map((cat) => (
            <TabsTrigger key={cat.value} value={cat.value}>
              {cat.label}
            </TabsTrigger>
          ))}
        </TabsList>

        {categories.map((cat) => (
          <TabsContent key={cat.value} value={cat.value}>
            {isLoading ? (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Card key={i}>
                    <CardHeader>
                      <Skeleton className="h-5 w-32" />
                      <Skeleton className="h-4 w-48" />
                    </CardHeader>
                    <CardContent>
                      <Skeleton className="h-8 w-full" />
                    </CardContent>
                  </Card>
                ))}
              </div>
            ) : templates && templates.length > 0 ? (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {templates.map((template) => (
                  <Card key={template.id} className="flex flex-col">
                    <CardHeader>
                      <CardTitle>{template.name}</CardTitle>
                      {template.description && (
                        <CardDescription className="line-clamp-2">
                          {template.description}
                        </CardDescription>
                      )}
                    </CardHeader>
                    <CardContent className="mt-auto space-y-3">
                      {template.recommended_tools && template.recommended_tools.length > 0 && (
                        <p className="text-xs text-muted-foreground">
                          {t('tools', { toolNames: template.recommended_tools.join(', ') })}
                        </p>
                      )}
                      <Button
                        className="w-full"
                        variant="outline"
                        onClick={() => handleCreateFromTemplate(template)}
                        disabled={creatingId === template.id}
                      >
                        {creatingId === template.id ? (
                          <Loader2Icon className="mr-1 size-4 animate-spin" />
                        ) : null}
                        {t('createFromTemplate')}
                      </Button>
                    </CardContent>
                  </Card>
                ))}
              </div>
            ) : (
              <EmptyState
                icon={<LayoutTemplateIcon className="size-6" />}
                title={t('emptyCategory')}
              />
            )}
          </TabsContent>
        ))}
      </Tabs>
    </div>
  )
}
