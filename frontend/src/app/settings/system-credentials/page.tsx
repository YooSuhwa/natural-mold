'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
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
  const router = useRouter()
  const { data: user, isPending } = useSession()
  const denied = !isPending && !!user && !user.is_super_user

  useEffect(() => {
    if (denied) router.replace('/')
  }, [denied, router])

  if (isPending || denied) {
    return (
      <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 overflow-auto p-6">
        <p className="text-sm text-muted-foreground">불러오는 중…</p>
      </div>
    )
  }

  return <SystemCredentialsPageInner />
}

function SystemCredentialsPageInner() {
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
    if (!confirm(`시스템 자격증명 "${name}"을 삭제할까요?`)) return
    try {
      await deleteCred.mutateAsync(id)
      toast.success('시스템 자격증명을 삭제했습니다.')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '삭제에 실패했습니다.')
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 overflow-auto p-6">
      <PageHeader
        title="시스템 자격증명"
        description="Fix Agent, 에이전트 빌더, 이미지 생성에 쓰는 운영자 관리 키입니다. 사용자용 선택 목록에는 표시되지 않습니다."
        action={
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="size-4" /> 시스템 자격증명 추가
          </Button>
        }
      />

      <div className="rounded-lg border bg-amber-50/40 p-3 text-xs text-amber-900 dark:border-amber-900/30 dark:bg-amber-950/20 dark:text-amber-200">
        <p className="flex items-center gap-2 font-medium">
          <Shield className="size-3.5" /> 운영자 전용
        </p>
        <p className="mt-1 text-amber-800/80 dark:text-amber-200/70">
          이 자격증명 사용 비용은 운영자 계정으로 비용이 청구됩니다. 사용자 에이전트 설정,
          모델 상태 확인, MCP 마법사에는 노출되지 않습니다. Fix Agent와 에이전트 빌더를
          실행하려면 여기에 최소 하나의 LLM 자격증명을 등록하세요.
        </p>
      </div>

      {isLoading ? (
        <p className="text-sm text-muted-foreground">불러오는 중…</p>
      ) : !credentials || credentials.length === 0 ? (
        <EmptyState
          icon={<Shield className="size-8" />}
          title="아직 시스템 자격증명이 없어요"
          description="Fix Agent와 에이전트 빌더를 사용하려면 LLM용 시스템 자격증명을 추가하세요."
          action={
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="size-4" /> 시스템 자격증명 추가
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
                  {c.field_keys.length}개 필드
                </p>
              </div>
              <StatusChip variant={c.status} />
              <Button
                variant="ghost"
                size="sm"
                onClick={() => handleDelete(c.id, c.name)}
                disabled={deleteCred.isPending}
                aria-label={`${c.name} 삭제`}
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
