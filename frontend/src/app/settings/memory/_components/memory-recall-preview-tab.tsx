'use client'

import { useState, type FormEvent } from 'react'
import { useTranslations } from 'next-intl'
import { Loader2Icon, SearchIcon } from 'lucide-react'
import { toast } from 'sonner'

import { FormFieldShell } from '@/components/shared/form-field-shell'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { useMemoryRecallPreview } from '@/lib/hooks/use-memory'
import type { MemoryRecallPreview, MemoryRecallPreviewItem } from '@/lib/types'
import { cn } from '@/lib/utils'
import { formatDisplayNumber } from '@/lib/utils/display-format'
import { SelectField, type MemoryAgentOption } from './memory-form-fields'

const NONE_AGENT = '__none__'
const MESSAGE_MAX_CHARS = 2000

function score(value: number | null): string {
  return formatDisplayNumber(value, { maximumFractionDigits: 2, minimumFractionDigits: 2 })
}

/**
 * 회상 미리 보기 탭 (plan 15, 5.9): 질문을 넣으면 채팅 실행과 같은 검색으로 무엇을
 * 떠올리는지 점수와 함께 보여 준다. 기준을 못 넘었거나 예산·범위 상한으로 빠진 후보도
 * 흐리게 보여 준다. 미리 보기는 아무것도 기록하지 않는다.
 */
export function MemoryRecallPreviewTab({ agents }: { agents: MemoryAgentOption[] }) {
  const t = useTranslations('appSettings.memory.preview')
  const preview = useMemoryRecallPreview()
  const [message, setMessage] = useState('')
  const [previous, setPrevious] = useState('')
  const [agentId, setAgentId] = useState<string | null>(null)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    const text = message.trim()
    if (!text) return
    try {
      await preview.mutateAsync({
        message: text,
        agent_id: agentId,
        previous: previous.trim() ? [previous.trim()] : [],
      })
    } catch (error) {
      toast.error(error instanceof Error && error.message ? error.message : t('failed'))
    }
  }

  return (
    <div className="space-y-4" data-testid="memory-recall-preview-tab">
      <SettingsSectionCard title={t('title')} description={t('description')}>
        <form className="space-y-4" onSubmit={handleSubmit}>
          <FormFieldShell id="memory-preview-message" label={t('messageLabel')}>
            <Textarea
              id="memory-preview-message"
              value={message}
              maxLength={MESSAGE_MAX_CHARS}
              placeholder={t('messagePlaceholder')}
              onChange={(event) => setMessage(event.target.value)}
              className="min-h-16"
              data-testid="memory-preview-message"
            />
          </FormFieldShell>
          <div className="grid gap-4 md:grid-cols-2">
            <FormFieldShell
              id="memory-preview-previous"
              label={t('previousLabel')}
              description={t('previousDescription')}
            >
              <Input
                id="memory-preview-previous"
                value={previous}
                maxLength={MESSAGE_MAX_CHARS}
                onChange={(event) => setPrevious(event.target.value)}
              />
            </FormFieldShell>
            <SelectField
              id="memory-preview-agent"
              label={t('agentLabel')}
              value={agentId ?? NONE_AGENT}
              options={[
                { value: NONE_AGENT, label: t('agentNone') },
                ...agents.map((agent) => ({ value: agent.id, label: agent.name })),
              ]}
              onChange={(value) => setAgentId(value === NONE_AGENT ? null : value)}
            />
          </div>
          <div className="flex justify-end">
            <Button
              type="submit"
              disabled={preview.isPending || message.trim().length === 0}
              data-testid="memory-preview-submit"
            >
              {preview.isPending ? (
                <Loader2Icon className="size-4 animate-spin" aria-hidden />
              ) : (
                <SearchIcon className="size-4" aria-hidden />
              )}
              {t('submit')}
            </Button>
          </div>
        </form>
      </SettingsSectionCard>
      {preview.data ? <PreviewResult result={preview.data} /> : null}
    </div>
  )
}

function PreviewResult({ result }: { result: MemoryRecallPreview }) {
  const t = useTranslations('appSettings.memory.preview')
  const analyzer =
    result.analyzer === 'nori' || result.analyzer === 'suffix' ? result.analyzer : 'none'
  return (
    <SettingsSectionCard
      title={t('resultTitle')}
      description={t(`mode.${result.mode}.description`)}
    >
      <div className="space-y-4" data-testid="memory-preview-result" data-mode={result.mode}>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="secondary" data-testid="memory-preview-mode">
            {t(`mode.${result.mode}.label`)}
          </Badge>
          <Badge variant="outline" data-testid="memory-preview-analyzer">
            {t('analyzerLabel', { analyzer: t(`analyzer.${analyzer}`) })}
          </Badge>
          <Badge variant="outline" data-testid="memory-preview-vector">
            {t('vectorLabel', { vector: t(`vector.${result.vector}`) })}
          </Badge>
          <span className="text-xs text-muted-foreground">
            {t('tokens', { tokens: result.tokens, budget: result.budget_tokens })}
          </span>
        </div>

        <div className="space-y-2">
          <p className="text-sm font-medium text-foreground">{t('profileTitle')}</p>
          {result.profile_lines.length === 0 ? (
            <p className="text-xs text-muted-foreground">{t('profileEmpty')}</p>
          ) : (
            <ul className="list-disc space-y-1 pl-5 text-sm text-foreground">
              {result.profile_lines.map((line, index) => (
                <li key={`${index}-${line}`}>{line}</li>
              ))}
            </ul>
          )}
        </div>

        {result.mode === 'fresh' ? (
          <div className="space-y-2">
            <p className="text-sm font-medium text-foreground">{t('candidatesTitle')}</p>
            {result.items.length === 0 ? (
              <p className="text-xs text-muted-foreground">{t('candidatesEmpty')}</p>
            ) : (
              <ol className="space-y-2">
                {result.items.map((item) => (
                  <PreviewItem key={item.id} item={item} />
                ))}
              </ol>
            )}
          </div>
        ) : null}
      </div>
    </SettingsSectionCard>
  )
}

function PreviewItem({ item }: { item: MemoryRecallPreviewItem }) {
  const t = useTranslations('appSettings.memory.preview')
  const tMemory = useTranslations('appSettings.memory')
  const reason = item.included ? 'included' : (item.left_out ?? 'below')
  return (
    <li
      className={cn(
        'space-y-1 rounded-lg border border-border/70 p-3',
        !item.included && 'opacity-60',
      )}
      data-testid={`memory-preview-item-${item.id}`}
      data-included={item.included ? 'true' : 'false'}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={item.included ? 'default' : 'outline'}>{t(`reason.${reason}`)}</Badge>
        {tMemory.has(`kind.${item.kind}`) ? (
          <Badge variant="secondary">{tMemory(`kind.${item.kind}`)}</Badge>
        ) : null}
        {tMemory.has(`scope.${item.scope}`) ? (
          <Badge variant="outline">{tMemory(`scope.${item.scope}`)}</Badge>
        ) : null}
      </div>
      <p className="whitespace-pre-wrap text-sm text-foreground">{item.content}</p>
      <p className="flex flex-wrap gap-x-3 text-xs text-muted-foreground tabular-nums">
        <span>{t('score.keyword', { value: score(item.keyword) })}</span>
        <span>{t('score.cosine', { value: score(item.cosine) })}</span>
        <span>{t('score.final', { value: score(item.final) })}</span>
      </p>
    </li>
  )
}
