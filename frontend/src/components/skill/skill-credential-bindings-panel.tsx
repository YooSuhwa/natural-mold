'use client'

import { useMemo } from 'react'
import type { ReactNode } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { CredentialPicker } from '@/components/credential/credential-picker'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import {
  useDeleteSkillCredentialBinding,
  useSetSkillCredentialBinding,
  useSkillCredentialBindings,
  useSkillCredentialRequirements,
} from '@/lib/hooks/use-marketplace'

export function SkillCredentialBindingsPanel({
  skillId,
  emptyFallback = null,
}: {
  readonly skillId: string
  readonly emptyFallback?: ReactNode
}) {
  const t = useTranslations('skill.detailDialog')
  const { data: requirements, isLoading: requirementsLoading } =
    useSkillCredentialRequirements(skillId)
  const { data: bindings, isLoading: bindingsLoading } = useSkillCredentialBindings(skillId)
  const setBinding = useSetSkillCredentialBinding(skillId)
  const deleteBinding = useDeleteSkillCredentialBinding(skillId)

  const bindingByKey = useMemo(() => {
    const map = new Map<string, string>()
    bindings?.forEach((binding) => {
      map.set(binding.requirement_key, binding.credential_id)
    })
    return map
  }, [bindings])

  const loading = requirementsLoading || bindingsLoading
  const pending = setBinding.isPending || deleteBinding.isPending

  async function handleCredentialChange(requirementKey: string, credentialId: string | null) {
    try {
      if (credentialId) {
        await setBinding.mutateAsync({ requirementKey, credentialId })
        toast.success(t('credentialUpdated'))
      } else {
        await deleteBinding.mutateAsync(requirementKey)
        toast.success(t('credentialCleared'))
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('credentialUpdateFailed'))
    }
  }

  if (loading) {
    return <Skeleton className="h-20 w-full rounded-lg" />
  }

  if (!requirements?.length) return emptyFallback

  return (
    <section className="rounded-lg border border-border/70 bg-muted/20 p-3">
      <div className="mb-3 space-y-0.5">
        <h3 className="text-sm font-semibold text-foreground">{t('credentialBindingsTitle')}</h3>
        <p className="text-xs text-muted-foreground">{t('credentialBindingsDescription')}</p>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {requirements.map((requirement) => {
          const current = bindingByKey.get(requirement.key) ?? null
          return (
            <div key={requirement.key} className="space-y-1.5">
              <div className="flex min-h-5 items-center gap-2">
                <label className="text-xs font-medium text-foreground">
                  {requirement.label || requirement.key}
                </label>
                <Badge variant={requirement.required ? 'default' : 'secondary'} className="h-5">
                  {requirement.required ? t('requiredCredential') : t('optionalCredential')}
                </Badge>
              </div>
              {requirement.description ? (
                <p className="line-clamp-2 moldy-ui-caption leading-4 text-muted-foreground">
                  {requirement.description}
                </p>
              ) : null}
              <CredentialPicker
                value={current}
                onChange={(next) => handleCredentialChange(requirement.key, next)}
                definitionKeys={[requirement.definition_key]}
                disabled={pending}
                placeholder={t('credentialPlaceholder')}
              />
              <p className="font-mono moldy-ui-micro text-muted-foreground/80">
                {requirement.definition_key}
              </p>
            </div>
          )
        })}
      </div>
    </section>
  )
}
