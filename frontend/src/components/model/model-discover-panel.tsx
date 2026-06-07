'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { Loader2, Search, Sparkles, Zap } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { SearchInput } from '@/components/shared/search-input'
import { DomainIcon } from '@/components/shared/icon'
import { ModelSourceBadge } from './model-source-badge'
import { ModelConnectionTest } from './model-connection-test'
import { formatTokenPrice } from './model-format'
import { RankingBadge, rankingScoreFor } from './model-rankings'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useCreateModel, useDiscoverModels } from '@/lib/hooks/use-models'
import { useCredentials, useCredentialTypes } from '@/lib/hooks/use-credentials'
import type { Credential, CredentialDefinition } from '@/lib/types/credential'
import type { DiscoveredModel } from '@/lib/types/model'

interface ModelDiscoverPanelProps {
  onComplete?: () => void
}

/**
 * Resource-locator "List" mode body. The user picks an LLM credential, asks
 * the backend to enumerate available models from that provider, then ticks
 * the ones to register. Pricing/source is enriched server-side.
 */
export function ModelDiscoverPanel({ onComplete }: ModelDiscoverPanelProps) {
  const t = useTranslations('model.discoverPanel')
  const { data: credentials } = useCredentials()
  const { data: definitions } = useCredentialTypes()
  const discover = useDiscoverModels()
  const create = useCreateModel()

  const [credentialId, setCredentialId] = useState<string>('')
  const [results, setResults] = useState<DiscoveredModel[] | null>(null)
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [saving, setSaving] = useState(false)
  // model_name → bool: whether the per-row preview test is shown.
  const [testRow, setTestRow] = useState<string | null>(null)

  const llmCredentials = useMemo<Credential[]>(() => {
    if (!credentials || !definitions) return []
    const llmKeys = new Set(
      definitions.filter((d: CredentialDefinition) => d.category === 'llm').map((d) => d.key),
    )
    return credentials.filter((c) => llmKeys.has(c.definition_key))
  }, [credentials, definitions])

  const definitionLabel = (key: string) =>
    definitions?.find((d) => d.key === key)?.display_name ?? key

  const filteredResults = useMemo(() => {
    if (!results) return []
    const q = search.trim().toLowerCase()
    const matched = q
      ? results.filter(
          (r) => r.model_name.toLowerCase().includes(q) || r.display_name.toLowerCase().includes(q),
        )
      : results
    // M11 — Float models with at least one benchmark score above unranked
    // ones so users see the strongest options first. We don't mutate the
    // upstream array; `toSorted` is intentionally avoided for older runtimes.
    return [...matched].sort((a, b) => rankingScoreFor(b.rankings) - rankingScoreFor(a.rankings))
  }, [results, search])

  async function handleDiscover() {
    if (!credentialId) return
    try {
      const models = await discover.mutateAsync(credentialId)
      setResults(models)
      setSelected(new Set())
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('toast.discoveryFailed'))
    }
  }

  function toggleRow(modelName: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(modelName)) next.delete(modelName)
      else next.add(modelName)
      return next
    })
  }

  function toggleAll() {
    const selectable = filteredResults.filter((r) => !r.already_registered)
    if (selectable.every((r) => selected.has(r.model_name))) {
      setSelected((prev) => {
        const next = new Set(prev)
        selectable.forEach((r) => next.delete(r.model_name))
        return next
      })
    } else {
      setSelected((prev) => {
        const next = new Set(prev)
        selectable.forEach((r) => next.add(r.model_name))
        return next
      })
    }
  }

  async function handleSave() {
    if (!results) return
    const picks = results.filter((r) => selected.has(r.model_name))
    if (picks.length === 0) return
    setSaving(true)
    try {
      await Promise.all(
        picks.map((m) =>
          create.mutateAsync({
            provider: m.provider,
            model_name: m.model_name,
            display_name: m.display_name,
            cost_per_input_token: m.cost_per_input_token,
            cost_per_output_token: m.cost_per_output_token,
            context_window: m.context_window,
            max_output_tokens: m.max_output_tokens,
            input_modalities: m.input_modalities,
            output_modalities: m.output_modalities,
            supports_vision: m.supports_vision,
            supports_function_calling: m.supports_function_calling,
            supports_reasoning: m.supports_reasoning,
            source: m.source,
            // Capture user intent: the credential they used to discover
            // becomes the model's default credential. Health panel and
            // future auto-binding flows will respect this.
            default_credential_id: credentialId || null,
          }),
        ),
      )
      toast.success(t('toast.saved', { count: picks.length }))
      onComplete?.()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('toast.saveFailed'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-1 space-y-1.5">
          <label className="text-xs font-medium" htmlFor="discover-cred">
            {t('llmCredential')}
          </label>
          <Select
            value={credentialId}
            onValueChange={(v) => v && setCredentialId(v)}
            disabled={llmCredentials.length === 0}
          >
            <SelectTrigger id="discover-cred" className="w-full">
              {/*
                Base-UI Select.Value receives a function child for custom
                rendering of the selected item — without it, the raw `value`
                (credential UUID) leaks through because our SelectItem
                children are JSX rather than a plain string.
              */}
              <SelectValue
                placeholder={
                  llmCredentials.length === 0 ? t('noCredential') : t('selectCredential')
                }
              >
                {(value: string) => {
                  const selected = llmCredentials.find((c) => c.id === value)
                  if (!selected) {
                    return llmCredentials.length === 0 ? t('noCredential') : t('selectCredential')
                  }
                  return (
                    <span className="inline-flex items-center gap-2">
                      <DomainIcon iconId={selected.definition_key} className="size-4" />
                      <span>{selected.name}</span>
                      <span className="moldy-ui-micro text-muted-foreground">
                        {definitionLabel(selected.definition_key)}
                      </span>
                    </span>
                  )
                }}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {llmCredentials.map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  <span className="inline-flex items-center gap-2">
                    <DomainIcon iconId={c.definition_key} className="size-4" />
                    <span>{c.name}</span>
                    <span className="moldy-ui-micro text-muted-foreground">
                      {definitionLabel(c.definition_key)}
                    </span>
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button onClick={handleDiscover} disabled={!credentialId || discover.isPending}>
          {discover.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Sparkles className="size-4" />
          )}
          {t('discover')}
        </Button>
      </div>

      {llmCredentials.length === 0 && (
        <p className="rounded border border-dashed p-3 text-xs text-muted-foreground">
          {t('emptyCredential')}
        </p>
      )}

      {results !== null && results.length === 0 && (
        <p className="rounded border border-dashed p-6 text-center text-sm text-muted-foreground">
          <Search className="mx-auto mb-2 size-5" /> {t('emptyResults')}
        </p>
      )}

      {results !== null && results.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <SearchInput
              containerClassName="flex-1"
              placeholder={t('searchPlaceholder')}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <Button variant="ghost" size="sm" onClick={toggleAll}>
              {t('toggleAll')}
            </Button>
          </div>

          <div role="list" className="max-h-[44vh] overflow-auto rounded-md border">
            {filteredResults.map((m) => {
              const checked = selected.has(m.model_name)
              const disabled = m.already_registered
              return (
                <label
                  key={m.model_name}
                  role="listitem"
                  className={`flex items-start gap-3 border-b px-3 py-2 last:border-b-0 ${
                    disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer hover:bg-muted/40'
                  }`}
                >
                  <Checkbox
                    checked={checked}
                    disabled={disabled}
                    onCheckedChange={() => toggleRow(m.model_name)}
                    aria-label={t('selectModel', { name: m.display_name })}
                  />
                  <div className="min-w-0 flex-1 space-y-0.5">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium">{m.display_name}</span>
                      <ModelSourceBadge source={m.source} />
                      {disabled && (
                        <span className="moldy-ui-micro text-muted-foreground">
                          {t('alreadyRegistered')}
                        </span>
                      )}
                      <Button
                        type="button"
                        variant="ghost"
                        size="xs"
                        className="ml-auto"
                        onClick={(e) => {
                          e.preventDefault()
                          setTestRow((prev) => (prev === m.model_name ? null : m.model_name))
                        }}
                        aria-label={t('testModel', { name: m.display_name })}
                      >
                        <Zap className="size-3" /> {t('test')}
                      </Button>
                    </div>
                    <p className="truncate font-mono moldy-ui-caption text-muted-foreground">
                      {m.provider} · {m.model_name}
                    </p>
                    <div className="flex flex-wrap gap-x-3 gap-y-0.5 moldy-ui-caption text-muted-foreground">
                      <span>
                        {t('inputPrice', { price: formatTokenPrice(m.cost_per_input_token) })}
                      </span>
                      <span>
                        {t('outputPrice', { price: formatTokenPrice(m.cost_per_output_token) })}
                      </span>
                      {m.context_window && (
                        <span>{t('context', { count: m.context_window.toLocaleString() })}</span>
                      )}
                    </div>
                    {m.rankings && (
                      <div className="mt-1 flex flex-wrap items-center gap-1">
                        <RankingBadge rankingKey="lmarena" value={m.rankings.lmarena} />
                        <RankingBadge rankingKey="livebench" value={m.rankings.livebench} />
                        <RankingBadge rankingKey="aa_index" value={m.rankings.aa_index} />
                      </div>
                    )}
                    {testRow === m.model_name && credentialId && (
                      <div className="mt-2">
                        <ModelConnectionTest
                          key={`${m.model_name}-${credentialId}`}
                          mode="preview"
                          provider={m.provider}
                          modelName={m.model_name}
                          credentialId={credentialId}
                          modelLabel={m.display_name}
                          autoStart
                          showCostBanner={false}
                        />
                      </div>
                    )}
                  </div>
                </label>
              )
            })}
          </div>

          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>
              {t('selectedSummary', { selected: selected.size, shown: filteredResults.length })}
            </span>
            <Button size="sm" onClick={handleSave} disabled={selected.size === 0 || saving}>
              {saving && <Loader2 className="size-3 animate-spin" />}
              {t('saveSelected')}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
