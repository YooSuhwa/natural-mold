'use client'

import { useState, type FormEvent } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useTenantDomains, useTenantSsoMutations } from '@/lib/hooks/use-tenant-sso'
import { useSsoErrorText } from '../lib/sso-error-text'

/** S30 domains: add, show the TXT record to publish, verify, remove. */
export function CompanySsoDomains({ tenantId }: { tenantId: string }) {
  const t = useTranslations('orgAdmin.sso.domains')
  const errorText = useSsoErrorText()
  const domains = useTenantDomains(tenantId)
  const { addDomain, verifyDomain, removeDomain } = useTenantSsoMutations(tenantId)
  const [domain, setDomain] = useState('')

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!domain.trim()) return
    addDomain.mutate(domain.trim(), {
      onSuccess: () => setDomain(''),
      onError: (error) => toast.error(errorText(error)),
    })
  }

  const items = domains.data?.items ?? []
  return (
    <SettingsSectionCard title={t('title')} description={t('description')}>
      <form onSubmit={submit} className="mb-4 flex gap-2">
        <label htmlFor="sso-domain" className="sr-only">
          {t('label')}
        </label>
        <Input
          id="sso-domain"
          value={domain}
          placeholder={t('placeholder')}
          onChange={(event) => setDomain(event.target.value)}
        />
        <Button type="submit" disabled={addDomain.isPending || !domain.trim()}>
          {t('add')}
        </Button>
      </form>
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('empty')}</p>
      ) : (
        <ul className="divide-y">
          {items.map((item) => (
            <li key={item.id} className="flex flex-wrap items-center gap-3 py-2.5">
              <span className="font-medium">{item.domain}</span>
              <Badge variant={item.status === 'verified' ? 'default' : 'secondary'}>
                {t(`status.${item.status}`)}
              </Badge>
              {item.status !== 'verified' ? (
                <code className="rounded bg-muted px-1.5 py-0.5 text-xs">{item.txt_record}</code>
              ) : null}
              <span className="ml-auto flex gap-2">
                {item.status !== 'verified' ? (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={verifyDomain.isPending}
                    onClick={() =>
                      verifyDomain.mutate(item.id, {
                        onSuccess: () => toast.success(t('verified')),
                        onError: (error) => toast.error(errorText(error)),
                      })
                    }
                  >
                    {t('verify')}
                  </Button>
                ) : null}
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={removeDomain.isPending}
                  onClick={() =>
                    removeDomain.mutate(item.id, {
                      onError: (error) => toast.error(errorText(error)),
                    })
                  }
                >
                  {t('remove')}
                </Button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </SettingsSectionCard>
  )
}
