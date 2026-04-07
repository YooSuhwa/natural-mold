'use client'

import { useState, useRef } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  SendIcon,
  PenLineIcon,
  LayoutTemplateIcon,
  SparklesIcon,
  ArrowLeftIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'

export default function AgentNewPage() {
  const t = useTranslations('agent.new')
  const router = useRouter()
  const [input, setInput] = useState('')
  const isComposingRef = useRef(false)

  function handleChatSubmit() {
    const text = input.trim()
    if (!text) return
    router.push(`/agents/new/conversational?initialMessage=${encodeURIComponent(text)}`)
  }

  return (
    <div className="flex flex-1 flex-col items-center justify-center overflow-auto p-6">
      <div className="flex w-full max-w-2xl flex-col items-center gap-8">
        {/* Back */}
        <div className="flex w-full">
          <Button variant="ghost" size="sm" onClick={() => router.push('/')}>
            <ArrowLeftIcon className="size-4" data-icon="inline-start" />
            {t('backToHome')}
          </Button>
        </div>

        {/* Hero */}
        <div className="flex flex-col items-center gap-3 text-center">
          <div className="flex size-16 items-center justify-center rounded-2xl bg-primary/10">
            <SparklesIcon className="size-8 text-primary" />
          </div>
          <h1 className="text-2xl font-bold tracking-tight">{t('hero.title')}</h1>
          <p className="text-sm text-muted-foreground">{t('hero.subtitle')}</p>
        </div>

        {/* Chat Input */}
        <div className="relative w-full">
          <div className="rounded-xl border bg-background shadow-sm">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !isComposingRef.current) {
                  e.preventDefault()
                  handleChatSubmit()
                }
              }}
              onCompositionStart={() => {
                isComposingRef.current = true
              }}
              onCompositionEnd={() => {
                isComposingRef.current = false
              }}
              placeholder={t('chatPlaceholder')}
              rows={3}
              className="w-full resize-none rounded-xl bg-transparent px-4 py-3 text-sm leading-relaxed outline-none placeholder:text-muted-foreground"
            />
            <div className="flex justify-end px-3 pb-3">
              <Button
                size="icon"
                variant="ghost"
                className="size-8 rounded-full"
                onClick={handleChatSubmit}
                disabled={!input.trim()}
              >
                <SendIcon className="size-4" />
              </Button>
            </div>
          </div>
        </div>

        {/* Action Cards */}
        <div className="grid w-full gap-4 sm:grid-cols-2">
          <Link href="/agents/new/manual" className="group">
            <Card className="cursor-pointer transition-colors hover:border-primary/40">
              <CardContent className="flex flex-col items-center gap-2 p-6">
                <PenLineIcon className="size-5 text-muted-foreground group-hover:text-primary transition-colors" />
                <span className="text-sm font-medium">{t('manual.title')}</span>
              </CardContent>
            </Card>
          </Link>

          <Link href="/agents/new/template" className="group">
            <Card className="cursor-pointer transition-colors hover:border-primary/40">
              <CardContent className="flex flex-col items-center gap-2 p-6">
                <LayoutTemplateIcon className="size-5 text-muted-foreground group-hover:text-primary transition-colors" />
                <span className="text-sm font-medium">{t('template.title')}</span>
              </CardContent>
            </Card>
          </Link>
        </div>
      </div>
    </div>
  )
}
