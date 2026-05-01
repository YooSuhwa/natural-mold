'use client'

import { useState } from 'react'
import { CheckIcon, CopyIcon, GlobeIcon, Trash2Icon } from 'lucide-react'
import { toast } from 'sonner'

import { DialogShell } from '@/components/shared/dialog-shell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  useActiveShare,
  useCreateShare,
  useRevokeShare,
} from '@/lib/hooks/use-share'

interface ShareDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  conversationId: string
}

function buildShareUrl(token: string): string {
  // Server-side render returns "" so the input renders empty until hydration —
  // acceptable since the dialog is interactive-only.
  if (typeof window === 'undefined') return ''
  return `${window.location.origin}/shared/${token}`
}

export function ShareDialog({ open, onOpenChange, conversationId }: ShareDialogProps) {
  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="md" height="auto">
      <DialogShell.Header
        icon={<GlobeIcon className="size-5" />}
        title="대화 공유"
        description="누구나 링크로 이 대화를 읽기 전용으로 볼 수 있어요."
      />
      {/* Body is inert when closed (DialogPortal unmounts) so the inner
          component can freely fetch / mutate without guards. */}
      {open ? <ShareDialogBody conversationId={conversationId} /> : null}
    </DialogShell>
  )
}

function ShareDialogBody({ conversationId }: { conversationId: string }) {
  const { data: link, isLoading } = useActiveShare(conversationId)
  const create = useCreateShare(conversationId)
  const revoke = useRevokeShare(conversationId)
  const [copied, setCopied] = useState(false)

  const isShared = link !== null && link !== undefined
  const url = isShared ? buildShareUrl(link.share_token) : ''

  async function handleCopy() {
    if (!url) return
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      toast.success('공유 링크를 복사했어요.')
      setTimeout(() => setCopied(false), 2000)
    } catch {
      toast.error('복사에 실패했습니다.')
    }
  }

  async function handleCreate() {
    try {
      await create.mutateAsync()
      toast.success('공유 링크가 생성됐습니다.')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '공유 링크 생성 실패')
    }
  }

  async function handleRevoke() {
    try {
      await revoke.mutateAsync()
      toast.success('공유를 해제했습니다.')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '공유 해제 실패')
    }
  }

  return (
    <>
      <DialogShell.Body>
        {isLoading ? (
          <p className="text-sm text-muted-foreground">불러오는 중…</p>
        ) : isShared ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Input
                readOnly
                value={url}
                aria-label="공유 링크"
                className="font-mono text-xs"
              />
              <Button variant="outline" onClick={handleCopy} aria-label="링크 복사">
                {copied ? (
                  <>
                    <CheckIcon className="size-4" /> 복사됨
                  </>
                ) : (
                  <>
                    <CopyIcon className="size-4" /> 복사
                  </>
                )}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              링크를 받은 사람은 로그인 없이 이 대화의 메시지를 볼 수 있어요. 새 메시지나
              편집이 일어나도 공개된 스냅샷은 유지됩니다.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              아직 비공개 대화입니다. 공유 링크를 만들면 누구나 읽기 전용으로 접근할 수
              있어요.
            </p>
          </div>
        )}
      </DialogShell.Body>
      <DialogShell.Footer>
        {isShared ? (
          <Button
            variant="outline"
            onClick={handleRevoke}
            disabled={revoke.isPending}
            className="text-destructive hover:text-destructive"
          >
            <Trash2Icon className="size-4" />
            공유 해제
          </Button>
        ) : (
          <Button onClick={handleCreate} disabled={create.isPending}>
            <GlobeIcon className="size-4" />
            공유 링크 만들기
          </Button>
        )}
      </DialogShell.Footer>
    </>
  )
}
