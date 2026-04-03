'use client'

import { use, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Loader2Icon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { conversationsApi } from '@/lib/api/conversations'

export default function AgentPage({ params }: { params: Promise<{ agentId: string }> }) {
  const { agentId } = use(params)
  const router = useRouter()
  const t = useTranslations('agent.detail')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    async function redirect() {
      try {
        const conversations = await conversationsApi.list(agentId)
        if (cancelled) return
        if (conversations.length > 0) {
          const latest = conversations[0]
          router.replace(`/agents/${agentId}/conversations/${latest.id}`)
        } else {
          const conv = await conversationsApi.create(agentId)
          if (cancelled) return
          router.replace(`/agents/${agentId}/conversations/${conv.id}`)
        }
      } catch {
        if (!cancelled) {
          setError(t('loadFailed'))
        }
      }
    }
    redirect()
    return () => {
      cancelled = true
    }
  }, [agentId, router, t])

  if (error) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <p className="text-sm text-destructive">{error}</p>
      </div>
    )
  }

  return (
    <div className="flex flex-1 items-center justify-center">
      <Loader2Icon className="size-6 animate-spin text-muted-foreground" />
    </div>
  )
}
