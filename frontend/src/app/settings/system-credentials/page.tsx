'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { Plus, Shield, Trash2 } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { PageHeader } from '@/components/shared/page-header'
import { DomainIcon } from '@/components/shared/icon'
import { EmptyState } from '@/components/shared/empty-state'
import { StatusChip } from '@/components/shared/status-chip'
import { CredentialCreateModal } from '@/components/credential/credential-create-modal'
import { useSession } from '@/lib/auth/session'
import {
  useCredentialTypes,
  useDeleteSystemCredential,
  useSystemCredentials,
} from '@/lib/hooks/use-credentials'
import { SettingsShell } from '../_components/settings-shell'

/**
 * System Credentials — operator-managed keys for Fix Agent / builder /
 * image generation. Super_user only:
 *   - cost is on the operator, not whichever user is logged in
 *   - users can't accidentally bind a system key to a personal agent
 *   - rotating system keys doesn't churn user-facing pickers
 *
 * Backend enforces this via ``require_super_user`` on every endpoint;
 * this guard avoids surfacing UI chrome and 403 noise to regular users
 * who land here via a bookmarked URL.
 */
export default function SystemCredentialsPage() {
  const t = useTranslations('systemCredentials')
  const router = useRouter()
  const { data: user, isPending } = useSession()
  const denied = !isPending && !!user && !user.is_super_user

  useEffect(() => {
    if (denied) router.replace('/')
  }, [denied, router])

  if (isPending || denied) {
    return (
      <SettingsShell>
        <p className="text-sm text-muted-foreground">{t('loading')}</p>
      </SettingsShell>
    )
  }

  return (
    <SettingsShell>
      <SystemCredentialsPageInner />
    </SettingsShell>
  )
}

function SystemCredentialsPageInner() {
  const t = useTranslations('systemCredentials')
  const { data: credentials, isLoading } = useSystemCredentials()
  const { data: definitions } = useCredentialTypes()
  const deleteCred = useDeleteSystemCredential()
  const [createOpen, setCreateOpen] = useState(false)

  const definitionLabels = useMemo(() => {
    const map = new Map<string, string>()
    definitions?.forEach((d) => map.set(d.key, d.display_name))
    return map
  }, [definitions])

  async function handleDelete(id: string, name: string) {
    if (!confirm(t('confirmDelete', { name }))) return
    try {
      await deleteCred.mutateAsync(id)
      toast.success(t('toast.deleted'))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('toast.deleteFailed'))
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={t('title')}
        description={t('description')}
        action={
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="size-4" />
            {t('add')}
          </Button>
        }
      />

      <div className="moldy-status-surface moldy-status-warn rounded-lg p-3 text-xs">
        <p className="flex items-center gap-2 font-medium">
          <Shield className="size-3.5" />
          {t('operatorOnly.title')}
        </p>
        <p className="moldy-status-muted-text mt-1">{t('operatorOnly.description')}</p>
      </div>

      {isLoading ? (
        <p className="text-sm text-muted-foreground">{t('loading')}</p>
      ) : !credentials || credentials.length === 0 ? (
        <EmptyState
          icon={<Shield className="size-8" />}
          title={t('empty.title')}
          description={t('empty.description')}
          action={
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="size-4" />
              {t('add')}
            </Button>
          }
        />
      ) : (
        <ul className="space-y-2">
          {credentials.map((c) => (
            <li key={c.id} className="flex items-center gap-3 rounded-lg border bg-card p-3">
              <DomainIcon iconId={c.definition_key} className="size-5" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{c.name}</p>
                <p className="truncate text-xs text-muted-foreground">
                  {definitionLabels.get(c.definition_key) ?? c.definition_key}
                  {' · '}
                  {t('fieldCount', { count: c.field_keys.length })}
                </p>
              </div>
              <StatusChip variant={c.status} />
              <Button
                variant="ghost"
                size="sm"
                onClick={() => handleDelete(c.id, c.name)}
                disabled={deleteCred.isPending}
                aria-label={t('deleteNamed', { name: c.name })}
              >
                <Trash2 className="size-4" />
              </Button>
            </li>
          ))}
        </ul>
      )}

      <CredentialCreateModal open={createOpen} onOpenChange={setCreateOpen} mode="system" />
    </div>
  )
}
