'use client'

import { useState, type FormEvent } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { SsoMode, SsoPolicy } from '@/lib/types/sso'
import { useTenantSsoMutations, useTenantSsoPolicy } from '@/lib/hooks/use-tenant-sso'
import { useSsoErrorText } from '../lib/sso-error-text'

const MODES: readonly SsoMode[] = ['off', 'optional', 'required']

/** S30 "SSO 필수": needs an active connection, a verified domain and 1–2 break-glass admins. */
export function CompanySsoPolicy({ tenantId }: { tenantId: string }) {
  const t = useTranslations('orgAdmin.sso.policy')
  const policy = useTenantSsoPolicy(tenantId)
  return (
    <SettingsSectionCard title={t('title')} description={t('description')}>
      {policy.data ? (
        <PolicyForm key={policy.data.mode} tenantId={tenantId} policy={policy.data} />
      ) : null}
    </SettingsSectionCard>
  )
}

function PolicyForm({ tenantId, policy }: { tenantId: string; policy: SsoPolicy }) {
  const t = useTranslations('orgAdmin.sso.policy')
  const errorText = useSsoErrorText()
  const { putPolicy } = useTenantSsoMutations(tenantId)
  const [mode, setMode] = useState<SsoMode>(policy.mode)
  const [breakGlass, setBreakGlass] = useState(policy.break_glass_user_ids.join(', '))

  function submit(event: FormEvent) {
    event.preventDefault()
    const ids = breakGlass
      .split(',')
      .map((id) => id.trim())
      .filter(Boolean)
    putPolicy.mutate(
      { mode, breakGlass: ids },
      {
        onSuccess: () => toast.success(t('saved')),
        onError: (error) => toast.error(errorText(error)),
      },
    )
  }

  return (
    <form onSubmit={submit} className="grid gap-3" noValidate>
      <div className="flex gap-2" role="radiogroup" aria-label={t('mode')}>
        {MODES.map((option) => (
          <Button
            key={option}
            type="button"
            size="sm"
            role="radio"
            aria-checked={mode === option}
            variant={mode === option ? 'default' : 'outline'}
            onClick={() => setMode(option)}
          >
            {t(`modes.${option}`)}
          </Button>
        ))}
      </div>
      <div className="grid gap-1">
        <label htmlFor="sso-break-glass" className="text-sm font-medium">
          {t('breakGlass')}
        </label>
        <Input
          id="sso-break-glass"
          value={breakGlass}
          onChange={(event) => setBreakGlass(event.target.value)}
        />
        <p className="text-xs text-muted-foreground">{t('breakGlassHint')}</p>
      </div>
      {mode === 'required' ? (
        <p className="text-sm text-muted-foreground" data-testid="sso-required-notice">
          {t('requiredNotice', { count: policy.password_members })}
        </p>
      ) : null}
      <div>
        <Button type="submit" disabled={putPolicy.isPending}>
          {t('save')}
        </Button>
      </div>
    </form>
  )
}
