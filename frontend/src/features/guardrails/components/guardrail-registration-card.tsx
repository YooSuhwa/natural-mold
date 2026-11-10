'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useRegistrationSettings, useUpdateRegistrationSettings } from '@/lib/hooks/use-guardrails'
import type {
  RegistrationKind,
  RegistrationRule,
  RegistrationSettings,
} from '@/lib/types/guardrails'
import { useGuardrailErrorMessage } from '../lib/labels'

const KINDS: RegistrationKind[] = ['mcp_server', 'tool', 'skill', 'agent']
const CHOICES = {
  who: ['members', 'granted'],
  review: ['off', 'on'],
  member_sharing: ['shareable', 'private'],
} as const
type Field = keyof typeof CHOICES
type Rules = Record<RegistrationKind, RegistrationRule>

interface GuardrailRegistrationCardProps {
  tenantId: string
  isAdmin: boolean
}

/**
 * Who may register tools, MCP servers, skills and agents, whether an administrator checks
 * what the others register, and whether they may share it (tool-governance-plan 3.6).
 * Company members read it; company admins change it.
 */
export function GuardrailRegistrationCard({ tenantId, isAdmin }: GuardrailRegistrationCardProps) {
  const t = useTranslations('guardrails.registration')
  const settings = useRegistrationSettings(tenantId)
  return (
    <SettingsSectionCard
      title={t('title')}
      description={isAdmin ? t('description') : t('readOnly')}
    >
      {settings.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : settings.data ? (
        <RegistrationForm
          key={settings.data.updated_at}
          tenantId={tenantId}
          settings={settings.data}
          readOnly={!isAdmin}
        />
      ) : (
        <ApiErrorState error={settings.error} onRetry={() => void settings.refetch()} />
      )}
    </SettingsSectionCard>
  )
}

function rulesOf(settings: RegistrationSettings): Rules {
  const rules = {} as Rules
  for (const kind of KINDS) {
    const { who, review, member_sharing } = settings.kinds[kind]
    rules[kind] = { who, review, member_sharing }
  }
  return rules
}

function RegistrationForm({
  tenantId,
  settings,
  readOnly,
}: {
  tenantId: string
  settings: RegistrationSettings
  readOnly: boolean
}) {
  const t = useTranslations('guardrails.registration')
  const errorMessage = useGuardrailErrorMessage()
  const update = useUpdateRegistrationSettings(tenantId)
  const [rules, setRules] = useState<Rules>(() => rulesOf(settings))
  const fields = (kind: RegistrationKind): Field[] =>
    settings.kinds[kind].reviewable
      ? ['who', 'review', 'member_sharing']
      : ['who', 'member_sharing']
  const dirty = KINDS.some((kind) =>
    fields(kind).some((field) => rules[kind][field] !== settings.kinds[kind][field]),
  )

  function choose(kind: RegistrationKind, field: Field, value: string) {
    setRules((prev) => ({ ...prev, [kind]: { ...prev[kind], [field]: value } }))
  }

  function save() {
    update.mutate(
      { data: { kinds: rules }, version: settings.updated_at },
      {
        onSuccess: () => toast.success(t('saved')),
        onError: (err) => toast.error(errorMessage(err)),
      },
    )
  }

  return (
    <div className="space-y-4">
      <ul className="divide-y divide-border">
        {KINDS.map((kind) => (
          <li key={kind} className="space-y-2 py-3" data-registration-kind={kind}>
            <p className="text-sm font-medium text-foreground">{t(`kind.${kind}`)}</p>
            {fields(kind).map((field) => (
              <div key={field} className="flex flex-wrap items-center gap-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-foreground">{t(`field.${field}`)}</p>
                  <p className="moldy-ui-caption text-muted-foreground">
                    {t(`hint.${field}.${rules[kind][field]}`)}
                  </p>
                </div>
                <fieldset
                  className="flex flex-wrap gap-1.5"
                  aria-label={t('choiceLabel', {
                    kind: t(`kind.${kind}`),
                    field: t(`field.${field}`),
                  })}
                >
                  {CHOICES[field].map((value) => (
                    <Button
                      key={value}
                      size="sm"
                      variant={rules[kind][field] === value ? 'default' : 'outline'}
                      aria-pressed={rules[kind][field] === value}
                      disabled={readOnly}
                      onClick={() => choose(kind, field, value)}
                    >
                      {t(`choice.${field}.${value}`)}
                    </Button>
                  ))}
                </fieldset>
              </div>
            ))}
          </li>
        ))}
      </ul>
      <p className="text-sm text-muted-foreground">
        {t('capabilityNote')}{' '}
        <Link href="/settings/organization/roles" className="text-primary-strong underline">
          {t('rolesLink')}
        </Link>
      </p>
      {readOnly ? null : (
        <Button disabled={!dirty || update.isPending} onClick={save}>
          {t('save')}
        </Button>
      )}
    </div>
  )
}
