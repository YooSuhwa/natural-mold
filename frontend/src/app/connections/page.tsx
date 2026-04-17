'use client'

import { useState, useMemo } from 'react'
import {
  PlusIcon,
  KeyRoundIcon,
  Trash2Icon,
  PencilIcon,
  Loader2Icon,
  CheckCircleIcon,
  CircleIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import {
  useCredentials,
  useCredentialProviders,
  useDeleteCredential,
} from '@/lib/hooks/use-credentials'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogAction,
  AlertDialogCancel,
} from '@/components/ui/alert-dialog'
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/ui/select'
import { SearchInput } from '@/components/shared/search-input'
import { EmptyState } from '@/components/shared/empty-state'
import { PageHeader } from '@/components/shared/page-header'
import { CredentialFormDialog } from '@/components/tool/credential-form-dialog'
import type { Credential } from '@/lib/types'

export default function ConnectionsPage() {
  const { data: credentials, isLoading } = useCredentials()
  const { data: providers } = useCredentialProviders()
  const deleteCredential = useDeleteCredential()
  const t = useTranslations('connections')
  const tc = useTranslations('common')

  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState('all')
  const [formOpen, setFormOpen] = useState(false)
  const [editingCredential, setEditingCredential] = useState<Credential | null>(null)
  const [deletingTarget, setDeletingTarget] = useState<Credential | null>(null)

  const filteredCredentials = useMemo(() => {
    if (!credentials) return []
    let result = credentials
    if (typeFilter !== 'all') {
      result = result.filter((c) => c.credential_type === typeFilter)
    }
    const q = search.toLowerCase()
    if (q) {
      result = result.filter(
        (c) =>
          c.name.toLowerCase().includes(q) || c.provider_name.toLowerCase().includes(q),
      )
    }
    return result
  }, [credentials, search, typeFilter])

  function openCreate() {
    setEditingCredential(null)
    setFormOpen(true)
  }

  function openEdit(credential: Credential) {
    setEditingCredential(credential)
    setFormOpen(true)
  }

  function getProviderLabel(providerName: string): string {
    const p = providers?.find((pr) => pr.key === providerName)
    return p?.name ?? providerName
  }

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      <PageHeader title={t('pageTitle')} description={t('pageDescription')} />

      <div className="flex flex-wrap items-center gap-3">
        <SearchInput
          containerClassName="flex-1 min-w-[200px]"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t('namePlaceholder')}
        />
        <Select
          value={typeFilter}
          onValueChange={(val) => {
            if (val) setTypeFilter(val)
          }}
        >
          <SelectTrigger className="w-[160px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t('type')}</SelectItem>
            <SelectItem value="api_key">{t('typeApiKey')}</SelectItem>
            <SelectItem value="oauth2">{t('typeOauth2')}</SelectItem>
          </SelectContent>
        </Select>
        <Button onClick={openCreate}>
          <PlusIcon className="size-4" data-icon="inline-start" />
          {t('addNew')}
        </Button>
      </div>

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : filteredCredentials.length > 0 ? (
        <div className="space-y-2">
          {filteredCredentials.map((cred) => (
            <CredentialCard
              key={cred.id}
              credential={cred}
              providerLabel={getProviderLabel(cred.provider_name)}
              onEdit={() => openEdit(cred)}
              onDelete={() => setDeletingTarget(cred)}
              isDeleting={deleteCredential.isPending && deleteCredential.variables === cred.id}
              t={t}
            />
          ))}
        </div>
      ) : (
        <EmptyState
          icon={<KeyRoundIcon className="size-6" />}
          title={t('empty.title')}
          description={t('empty.description')}
        />
      )}

      <CredentialFormDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        editingCredential={editingCredential}
      />

      <AlertDialog
        open={!!deletingTarget}
        onOpenChange={(v) => !v && setDeletingTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('deleteConfirm')}</AlertDialogTitle>
            <AlertDialogDescription>{t('deleteDescription')}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc('cancel')}</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                if (deletingTarget) {
                  deleteCredential.mutate(deletingTarget.id, {
                    onSuccess: () => toast.success(t('toast.deleted')),
                    onError: () => toast.error(t('toast.deleteFailed')),
                  })
                  setDeletingTarget(null)
                }
              }}
            >
              {tc('delete')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function CredentialCard({
  credential,
  providerLabel,
  onEdit,
  onDelete,
  isDeleting,
  t,
}: {
  credential: Credential
  providerLabel: string
  onEdit: () => void
  onDelete: () => void
  isDeleting: boolean
  t: ReturnType<typeof useTranslations<'connections'>>
}) {
  return (
    <Card>
      <CardContent className="flex items-center justify-between py-3">
        <div className="flex items-center gap-3">
          <div className="flex size-9 items-center justify-center rounded-lg bg-muted">
            <KeyRoundIcon className="size-4 text-muted-foreground" />
          </div>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium">{credential.name}</span>
              <Badge variant="outline">{providerLabel}</Badge>
              <Badge variant="secondary" className="text-[10px]">
                {credential.credential_type === 'oauth2' ? 'OAuth2' : 'API Key'}
              </Badge>
              {credential.has_data ? (
                <span className="flex items-center gap-1 text-[10px] text-emerald-600">
                  <CheckCircleIcon className="size-3" />
                  {t('status.configured')}
                </span>
              ) : (
                <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                  <CircleIcon className="size-3" />
                  {t('status.notConfigured')}
                </span>
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              {credential.field_keys.join(', ')}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon-sm" onClick={onEdit}>
            <PencilIcon className="size-4 text-muted-foreground" />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={onDelete}
            disabled={isDeleting}
          >
            {isDeleting ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <Trash2Icon className="size-4 text-muted-foreground" />
            )}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
