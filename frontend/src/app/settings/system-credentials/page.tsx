'use client'

import { useMemo, useState } from 'react'
import { Plus, Shield, Trash2 } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { PageHeader } from '@/components/shared/page-header'
import { DomainIcon } from '@/components/shared/icon'
import { EmptyState } from '@/components/shared/empty-state'
import { StatusChip } from '@/components/shared/status-chip'
import { CredentialCreateModal } from '@/components/credential/credential-create-modal'
import {
  useCredentialTypes,
  useDeleteSystemCredential,
  useSystemCredentials,
} from '@/lib/hooks/use-credentials'

/**
 * System Credentials — operator-managed keys for Fix Agent / builder /
 * image generation. Distinct from user credentials so that:
 *   - cost is on the operator, not whichever user is logged in
 *   - users can't accidentally bind a system key to a personal agent
 *   - rotating system keys doesn't churn user-facing pickers
 *
 * PoC: no role gate (mock user). Wire to admin role check before multi-user.
 */
export default function SystemCredentialsPage() {
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
    if (!confirm(`Delete system credential "${name}"?`)) return
    try {
      await deleteCred.mutateAsync(id)
      toast.success('System credential deleted')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Delete failed')
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="System Credentials"
        description="Operator-managed keys used by Fix Agent, agent builder, and image generation. Hidden from user-facing pickers."
        action={
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="size-4" /> System credential
          </Button>
        }
      />

      <div className="rounded-lg border bg-amber-50/40 p-3 text-xs text-amber-900 dark:border-amber-900/30 dark:bg-amber-950/20 dark:text-amber-200">
        <p className="flex items-center gap-2 font-medium">
          <Shield className="size-3.5" /> Operator-only
        </p>
        <p className="mt-1 text-amber-800/80 dark:text-amber-200/70">
          These credentials are billed to the operator account. They are
          never surfaced in user agent settings, model Health pickers, or
          MCP wizards. Set up at least one Anthropic credential here so
          Fix Agent and the agent builder can run.
        </p>
      </div>

      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : !credentials || credentials.length === 0 ? (
        <EmptyState
          icon={<Shield className="size-8" />}
          title="No system credentials yet"
          description="Add a system credential (e.g. Anthropic) to enable Fix Agent and the agent builder."
          action={
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="size-4" /> Add system credential
            </Button>
          }
        />
      ) : (
        <ul className="space-y-2">
          {credentials.map((c) => (
            <li
              key={c.id}
              className="flex items-center gap-3 rounded-lg border bg-card p-3"
            >
              <DomainIcon iconId={c.definition_key} className="size-5" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{c.name}</p>
                <p className="truncate text-xs text-muted-foreground">
                  {definitionLabels.get(c.definition_key) ?? c.definition_key}
                  {' · '}
                  {c.field_keys.length} field{c.field_keys.length === 1 ? '' : 's'}
                </p>
              </div>
              <StatusChip variant={c.status} />
              <Button
                variant="ghost"
                size="sm"
                onClick={() => handleDelete(c.id, c.name)}
                disabled={deleteCred.isPending}
              >
                <Trash2 className="size-4" />
              </Button>
            </li>
          ))}
        </ul>
      )}

      <CredentialCreateModal
        open={createOpen}
        onOpenChange={setCreateOpen}
        mode="system"
      />
    </div>
  )
}
