'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { DialogShell } from '@/components/shared/dialog-shell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  useGuardrailTemplateMutations,
  useGuardrailTemplateVersions,
} from '@/lib/hooks/use-guardrails'
import type { GuardrailTemplate } from '@/lib/types/guardrails'
import { formatDisplayDateTime } from '@/lib/utils/display-format'
import { useGuardrailErrorMessage } from '../lib/labels'
import { GuardrailRuleList } from './guardrail-rule-list'

interface GuardrailTemplateVersionsProps {
  tenantId: string
  template: GuardrailTemplate
  onClose: () => void
}

/** Published versions of a template; an earlier one can be published again as the next. */
export function GuardrailTemplateVersions({
  tenantId,
  template,
  onClose,
}: GuardrailTemplateVersionsProps) {
  const t = useTranslations('guardrails.templates.versions')
  const tCommon = useTranslations('orgAdmin.common')
  const errorMessage = useGuardrailErrorMessage()
  const versions = useGuardrailTemplateVersions(tenantId, template.id)
  const { revert } = useGuardrailTemplateMutations(tenantId)
  const [open, setOpen] = useState<number | null>(template.published_version)

  function runRevert(target: number) {
    revert.mutate(
      { id: template.id, target, version: template.updated_at },
      {
        onSuccess: () => {
          toast.success(t('reverted', { version: target }))
          onClose()
        },
        onError: (err) => toast.error(errorMessage(err)),
      },
    )
  }

  return (
    <DialogShell open onOpenChange={(next) => (next ? undefined : onClose())} size="lg">
      <DialogShell.Header
        title={t('title', { name: template.name })}
        description={t('description')}
      />
      <DialogShell.Body>
        {versions.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : versions.isError ? (
          <ApiErrorState error={versions.error} onRetry={() => void versions.refetch()} />
        ) : (
          <ul className="divide-y divide-border">
            {(versions.data ?? []).map((item) => {
              const current = item.version === template.published_version
              return (
                <li key={item.version} className="space-y-2 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm text-foreground">v{item.version}</span>
                    {current ? <Badge variant="secondary">{t('current')}</Badge> : null}
                    <span className="moldy-ui-caption min-w-0 flex-1 text-muted-foreground">
                      {t('meta', {
                        date: formatDisplayDateTime(item.published_at),
                        count: item.rules.length,
                      })}
                    </span>
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-expanded={open === item.version}
                      onClick={() => setOpen(open === item.version ? null : item.version)}
                    >
                      {open === item.version ? t('hideRules') : t('showRules')}
                    </Button>
                    {current || template.status === 'archived' ? null : (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={revert.isPending}
                        aria-label={t('revertTo', { version: item.version })}
                        onClick={() => runRevert(item.version)}
                      >
                        {t('revert')}
                      </Button>
                    )}
                  </div>
                  {open === item.version ? <GuardrailRuleList rules={item.rules} /> : null}
                </li>
              )
            })}
          </ul>
        )}
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose}>
          {tCommon('close')}
        </Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}
