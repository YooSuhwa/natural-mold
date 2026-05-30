'use client'

import { useEffect, useRef, useState } from 'react'
import { CheckIcon, CopyIcon, GlobeIcon, Trash2Icon } from 'lucide-react'
import { useTranslations } from 'next-intl'
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
  const t = useTranslations('chat.share')
  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="md" height="auto">
      <DialogShell.Header
        icon={<GlobeIcon className="size-5" />}
        title={t('title')}
        description={t('description')}
      />
      {/* Body is inert when closed (DialogPortal unmounts) so the inner
          component can freely fetch / mutate without guards. */}
      {open ? <ShareDialogBody conversationId={conversationId} /> : null}
    </DialogShell>
  )
}

function ShareDialogBody({ conversationId }: { conversationId: string }) {
  const t = useTranslations('chat.share')
  const { data: link, isLoading } = useActiveShare(conversationId)
  const create = useCreateShare(conversationId)
  const revoke = useRevokeShare(conversationId)
  const [copied, setCopied] = useState(false)
  // Tracks the "copied" indicator timeout so rapid re-clicks reset cleanly
  // and an unmount during the 2s window doesn't leak a pending setState.
  const copiedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(
    () => () => {
      if (copiedTimerRef.current) clearTimeout(copiedTimerRef.current)
    },
    [],
  )

  const isShared = link != null
  const url = isShared ? buildShareUrl(link.share_token) : ''

  async function handleCopy() {
    if (!url) return
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      toast.success(t('toast.copied'))
      if (copiedTimerRef.current) clearTimeout(copiedTimerRef.current)
      copiedTimerRef.current = setTimeout(() => setCopied(false), 2000)
    } catch {
      toast.error(t('toast.copyFailed'))
    }
  }

  async function handleCreate() {
    try {
      await create.mutateAsync()
      toast.success(t('toast.created'))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('toast.createFailed'))
    }
  }

  async function handleRevoke() {
    try {
      await revoke.mutateAsync()
      toast.success(t('toast.revoked'))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('toast.revokeFailed'))
    }
  }

  return (
    <>
      <DialogShell.Body>
        {isLoading ? (
          <p className="text-sm text-muted-foreground">{t('loading')}</p>
        ) : isShared ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Input
                readOnly
                value={url}
                aria-label={t('linkLabel')}
                className="font-mono text-xs"
              />
              <Button variant="outline" onClick={handleCopy} aria-label={t('copyLink')}>
                {copied ? (
                  <>
                    <CheckIcon className="size-4" />
                    {t('copied')}
                  </>
                ) : (
                  <>
                    <CopyIcon className="size-4" />
                    {t('copy')}
                  </>
                )}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">{t('sharedHint')}</p>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">{t('privateHint')}</p>
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
            {t('revoke')}
          </Button>
        ) : (
          <Button onClick={handleCreate} disabled={create.isPending}>
            <GlobeIcon className="size-4" />
            {t('create')}
          </Button>
        )}
      </DialogShell.Footer>
    </>
  )
}
