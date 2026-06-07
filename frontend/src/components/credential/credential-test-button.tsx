'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { Loader2, Activity } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { useTestCredential, usePreviewTestCredential } from '@/lib/hooks/use-credential-test'

interface CredentialTestButtonProps {
  /** Either a saved credential ID, or undefined for preview-test mode. */
  credentialId?: string
  /** For preview-test, the definition + raw form data. */
  preview?: { definition_key: string; data: Record<string, unknown> }
  size?: 'sm' | 'default'
  variant?: 'outline' | 'default' | 'ghost'
  label?: string
  onResult?: (success: boolean) => void
}

export function CredentialTestButton({
  credentialId,
  preview,
  size = 'sm',
  variant = 'outline',
  label,
  onResult,
}: CredentialTestButtonProps) {
  const t = useTranslations('credentials.testButton')
  const test = useTestCredential()
  const previewTest = usePreviewTestCredential()
  const [pending, setPending] = useState(false)

  async function handleClick() {
    setPending(true)
    try {
      const result = credentialId
        ? await test.mutateAsync(credentialId)
        : preview
          ? await previewTest.mutateAsync(preview)
          : null
      if (!result) return
      if (result.success) {
        toast.success(result.message || t('succeeded'))
      } else {
        toast.error(result.message || t('failed'))
      }
      onResult?.(result.success)
    } catch (e) {
      const msg = e instanceof Error ? e.message : t('failed')
      toast.error(msg)
      onResult?.(false)
    } finally {
      setPending(false)
    }
  }

  return (
    <Button
      type="button"
      variant={variant}
      size={size}
      disabled={pending || (!credentialId && !preview)}
      onClick={handleClick}
    >
      {pending ? <Loader2 className="size-3.5 animate-spin" /> : <Activity className="size-3.5" />}
      {label ?? t('label')}
    </Button>
  )
}
