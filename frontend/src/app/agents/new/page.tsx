'use client'

import { useState, useRef } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import Image from 'next/image'
import {
  SendIcon,
  PenLineIcon,
  LayoutTemplateIcon,
  SparklesIcon,
  ChevronRightIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'

type ExamplePrompt = { emoji: string; label: string; text: string }

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

  const examples = t.raw('examples.items') as ExamplePrompt[]
  const hasInput = input.trim().length > 0

  return (
    <div className="flex flex-1 flex-col items-center justify-center overflow-auto bg-gradient-to-b from-emerald-50/40 via-background to-background dark:from-emerald-950/15 dark:via-background dark:to-background">
      <style>{`
        @keyframes mold-mascot-float {
          0%, 100% { transform: translateY(0); }
          50%      { transform: translateY(-5px); }
        }
      `}</style>

      <div className="flex w-full max-w-[720px] flex-col gap-7 px-8 py-10">
        <Hero title={t('hero.title')} subtitle={t('hero.subtitle')} />

        <ChatInput
          value={input}
          onChange={setInput}
          onSubmit={handleChatSubmit}
          onCompositionStart={() => {
            isComposingRef.current = true
          }}
          onCompositionEnd={() => {
            isComposingRef.current = false
          }}
          isComposingRef={isComposingRef}
          placeholder={t('chatPlaceholder')}
          hasInput={hasInput}
        />

        <ExamplePrompts heading={t('examples.heading')} items={examples} onPick={setInput} />

        <AltMethods
          dividerLabel={t('altDivider')}
          manualTitle={t('manual.title')}
          manualDescription={t('manual.description')}
          templateTitle={t('template.title')}
          templateDescription={t('template.description')}
        />
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────── Hero

function Hero({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="flex flex-col items-center gap-4 text-center">
      <div className="relative">
        <div
          aria-hidden
          className="absolute"
          style={{
            inset: '15% 5% -5% 5%',
            borderRadius: '50%',
            background: 'radial-gradient(circle, oklch(0.85 0.13 163 / 0.4), transparent 65%)',
            filter: 'blur(16px)',
          }}
        />
        <Image
          src="/agent-create-hero.webp"
          alt="Moldy"
          width={160}
          height={160}
          priority
          draggable={false}
          className="relative select-none"
          style={{
            filter: 'drop-shadow(0 10px 18px oklch(0.4 0.1 163 / 0.22))',
            animation: 'mold-mascot-float 5s ease-in-out infinite',
          }}
        />
      </div>
      <div className="flex flex-col gap-2">
        <h1 className="text-[26px] font-bold leading-snug tracking-tight">{title}</h1>
        <p className="mx-auto max-w-[480px] text-sm leading-relaxed text-muted-foreground">
          {subtitle}
        </p>
      </div>
    </div>
  )
}

// ───────────────────────────────────────────────────────────── Chat input

type ChatInputProps = {
  value: string
  onChange: (v: string) => void
  onSubmit: () => void
  onCompositionStart: () => void
  onCompositionEnd: () => void
  isComposingRef: React.MutableRefObject<boolean>
  placeholder: string
  hasInput: boolean
}

function ChatInput({
  value,
  onChange,
  onSubmit,
  onCompositionStart,
  onCompositionEnd,
  isComposingRef,
  placeholder,
  hasInput,
}: ChatInputProps) {
  return (
    <div
      className={[
        'group relative rounded-2xl border border-border bg-card transition',
        'shadow-[0_2px_8px_-4px_rgba(0,0,0,0.06)]',
        'focus-within:border-emerald-300',
        'dark:focus-within:border-emerald-500/40',
        'focus-within:shadow-[0_0_0_4px_oklch(0.596_0.145_163.225/0.12),0_4px_14px_-8px_oklch(0.4_0.1_163/0.15)]',
      ].join(' ')}
    >
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !isComposingRef.current) {
            e.preventDefault()
            onSubmit()
          }
        }}
        onCompositionStart={onCompositionStart}
        onCompositionEnd={onCompositionEnd}
        placeholder={placeholder}
        rows={4}
        className="min-h-[110px] w-full resize-none rounded-2xl bg-transparent px-5 pb-2 pt-4 text-[14.5px] leading-relaxed text-foreground outline-none placeholder:text-muted-foreground"
      />
      <div className="flex justify-end px-3 pb-3">
        <Button
          type="button"
          size="icon"
          onClick={onSubmit}
          disabled={!hasInput}
          aria-label="에이전트 만들기 시작"
          className={[
            'size-9 rounded-xl transition-colors',
            hasInput
              ? 'bg-[var(--primary-strong)] text-white hover:bg-[var(--primary-strong-hover)]'
              : 'bg-muted text-muted-foreground hover:bg-muted',
          ].join(' ')}
        >
          <SendIcon className="size-4" />
        </Button>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────── Example prompts

function ExamplePrompts({
  heading,
  items,
  onPick,
}: {
  heading: string
  items: ExamplePrompt[]
  onPick: (text: string) => void
}) {
  return (
    <div>
      <div className="mb-2.5 flex items-center gap-1.5">
        <SparklesIcon className="size-3 text-[var(--primary-strong)]" />
        <span className="text-xs font-semibold text-muted-foreground">{heading}</span>
      </div>
      <div className="flex flex-wrap gap-2">
        {items.map((p) => (
          <button
            key={p.label}
            type="button"
            onClick={() => onPick(p.text)}
            className={[
              'inline-flex items-center gap-1.5 rounded-full border px-3 transition-colors',
              'h-8 border-border bg-background text-[13px] text-foreground',
              'hover:border-emerald-200 hover:bg-emerald-50',
              'dark:hover:border-emerald-500/30 dark:hover:bg-emerald-950/30',
            ].join(' ')}
          >
            <span className="text-sm leading-none">{p.emoji}</span>
            <span className="leading-none">{p.label}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

// ───────────────────────────────────────────────────────── Alt methods

function AltMethods({
  dividerLabel,
  manualTitle,
  manualDescription,
  templateTitle,
  templateDescription,
}: {
  dividerLabel: string
  manualTitle: string
  manualDescription: string
  templateTitle: string
  templateDescription: string
}) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <span className="h-px flex-1 bg-border" />
        <span className="text-xs font-medium tracking-wide text-muted-foreground">
          {dividerLabel}
        </span>
        <span className="h-px flex-1 bg-border" />
      </div>
      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
        <AltMethodCard
          href="/agents/new/manual"
          title={manualTitle}
          description={manualDescription}
          icon={<PenLineIcon className="size-4" />}
          iconClassName="bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-300"
        />
        <AltMethodCard
          href="/agents/new/template"
          title={templateTitle}
          description={templateDescription}
          icon={<LayoutTemplateIcon className="size-4" />}
          iconClassName="bg-sky-100 text-sky-600 dark:bg-sky-500/15 dark:text-sky-300"
        />
      </div>
    </div>
  )
}

function AltMethodCard({
  href,
  title,
  description,
  icon,
  iconClassName,
}: {
  href: string
  title: string
  description: string
  icon: React.ReactNode
  iconClassName: string
}) {
  return (
    <Link
      href={href}
      className={[
        'group flex items-center gap-3 rounded-xl border border-border bg-card p-3 transition-colors',
        'hover:border-emerald-300/60 hover:bg-muted/40',
        'dark:hover:border-emerald-500/30',
      ].join(' ')}
    >
      <span
        className={[
          'inline-flex size-8 shrink-0 items-center justify-center rounded-lg',
          iconClassName,
        ].join(' ')}
      >
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-semibold leading-tight">{title}</div>
        <div className="mt-0.5 text-xs text-muted-foreground">{description}</div>
      </div>
      <ChevronRightIcon className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
    </Link>
  )
}
