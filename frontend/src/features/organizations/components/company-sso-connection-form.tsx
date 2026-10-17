'use client'

import { useState, type FormEvent } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import type { SsoProtocol } from '@/lib/types/sso'
import { useTenantSsoMutations } from '@/lib/hooks/use-tenant-sso'
import { useSsoErrorText } from '../lib/sso-error-text'

const OIDC_FIELDS = ['issuer', 'client_id', 'client_secret'] as const
const LDAP_FIELDS = [
  'server_url',
  'bind_dn',
  'bind_password',
  'user_base_dn',
  'user_filter',
] as const
type Field = (typeof OIDC_FIELDS)[number] | (typeof LDAP_FIELDS)[number]
const SECRET_FIELDS: ReadonlySet<Field> = new Set(['client_secret', 'bind_password'])

/** New connection (saved as a draft). SAML-only IdPs connect through Keycloak (15.2.2). */
export function CompanySsoConnectionForm({
  tenantId,
  onDone,
}: {
  tenantId: string
  onDone: () => void
}) {
  const t = useTranslations('orgAdmin.sso.form')
  const errorText = useSsoErrorText()
  const { createConnection } = useTenantSsoMutations(tenantId)
  const [protocol, setProtocol] = useState<SsoProtocol>('oidc')
  const [values, setValues] = useState<Partial<Record<Field, string>>>({
    user_filter: '(sAMAccountName={username})',
  })
  const [startTls, setStartTls] = useState(true)
  const [jit, setJit] = useState(false)
  const [trustEmail, setTrustEmail] = useState(false)
  const fields = protocol === 'oidc' ? OIDC_FIELDS : LDAP_FIELDS
  const value = (field: Field) => values[field]?.trim() ?? ''

  function submit(event: FormEvent) {
    event.preventDefault()
    if (fields.some((field) => !value(field))) return
    const common = {
      jit_enabled: jit,
      trust_email_claim: trustEmail,
      default_role: 'member' as const,
    }
    const input =
      protocol === 'oidc'
        ? {
            protocol: 'oidc' as const,
            issuer: value('issuer'),
            client_id: value('client_id'),
            client_secret: value('client_secret'),
            ...common,
          }
        : {
            protocol: 'ldap' as const,
            server_url: value('server_url'),
            start_tls: startTls,
            bind_dn: value('bind_dn'),
            bind_password: value('bind_password'),
            user_base_dn: value('user_base_dn'),
            user_filter: value('user_filter'),
            ...common,
          }
    createConnection.mutate(input, {
      onSuccess: () => {
        toast.success(t('saved'))
        onDone()
      },
      onError: (error) => toast.error(errorText(error)),
    })
  }

  return (
    <form onSubmit={submit} className="mb-4 grid gap-3 rounded-md border p-4" noValidate>
      <div className="flex gap-2" role="radiogroup" aria-label={t('protocol')}>
        {(['oidc', 'ldap'] as const).map((option) => (
          <Button
            key={option}
            type="button"
            size="sm"
            role="radio"
            aria-checked={protocol === option}
            variant={protocol === option ? 'default' : 'outline'}
            onClick={() => setProtocol(option)}
          >
            {t(`protocols.${option}`)}
          </Button>
        ))}
      </div>
      <p className="text-xs text-muted-foreground">{t('samlHint')}</p>
      {fields.map((field) => (
        <div key={field} className="grid gap-1">
          <label htmlFor={`sso-${field}`} className="text-sm font-medium">
            {t(`fields.${field}`)}
          </label>
          <Input
            id={`sso-${field}`}
            type={SECRET_FIELDS.has(field) ? 'password' : 'text'}
            autoComplete="off"
            value={values[field] ?? ''}
            onChange={(event) => setValues((prev) => ({ ...prev, [field]: event.target.value }))}
          />
        </div>
      ))}
      {protocol === 'ldap' ? (
        <label className="flex items-center gap-2 text-sm">
          <Checkbox checked={startTls} onCheckedChange={(next) => setStartTls(next === true)} />
          {t('startTls')}
        </label>
      ) : null}
      <label className="flex items-center gap-2 text-sm">
        <Checkbox checked={jit} onCheckedChange={(next) => setJit(next === true)} />
        {t('jit')}
      </label>
      <label className="flex items-center gap-2 text-sm">
        <Checkbox checked={trustEmail} onCheckedChange={(next) => setTrustEmail(next === true)} />
        {t('trustEmail')}
      </label>
      <div className="flex gap-2">
        <Button type="submit" disabled={createConnection.isPending}>
          {t('save')}
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          {t('cancel')}
        </Button>
      </div>
    </form>
  )
}
