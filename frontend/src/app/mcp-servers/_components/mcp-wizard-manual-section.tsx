'use client'

import { Plus, X } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { McpTransport } from '@/lib/types/mcp'

import type {
  McpWizardFormPatch,
  McpWizardFormState,
  McpWizardKeyValueRow,
} from './mcp-wizard-form-state'

type McpWizardManualSectionProps = {
  readonly state: McpWizardFormState
  readonly onChange: (patch: McpWizardFormPatch) => void
  readonly onTransportChange: (transport: McpTransport) => void
  readonly onAddArg: () => void
}

export function McpWizardManualSection({
  state,
  onChange,
  onTransportChange,
  onAddArg,
}: McpWizardManualSectionProps) {
  const t = useTranslations('mcp.wizard.manual')
  const isHttp = state.transport === 'sse' || state.transport === 'streamable_http'
  return (
    <section className="space-y-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {state.registryKey ? t('override') : t('manual')}
      </h3>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <label htmlFor="mcp-name">
            {t('name')} <span className="text-destructive">*</span>
          </label>
          <Input
            id="mcp-name"
            value={state.name}
            onChange={(event) => onChange({ name: event.target.value })}
          />
        </div>
        <div className="space-y-1.5">
          <label htmlFor="mcp-desc">{t('description')}</label>
          <Input
            id="mcp-desc"
            value={state.description}
            onChange={(event) => onChange({ description: event.target.value })}
          />
        </div>
      </div>

      <div className="space-y-1.5">
        <label>{t('transport')}</label>
        <div className="flex flex-wrap gap-2">
          {(['stdio', 'sse', 'streamable_http'] as const).map((option) => {
            const active = state.transport === option
            return (
              <button
                key={option}
                type="button"
                onClick={() => onTransportChange(option)}
                className={`rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
                  active
                    ? 'border-primary-strong/60 bg-primary-strong/10 text-primary-strong'
                    : 'border-border hover:bg-muted/50'
                }`}
              >
                {t(`transportOptions.${option}`)}
              </button>
            )
          })}
        </div>
      </div>

      {state.transport === 'stdio' ? (
        <div className="space-y-3">
          <div className="space-y-1.5">
            <label htmlFor="mcp-command">
              {t('command')} <span className="text-destructive">*</span>
            </label>
            <Input
              id="mcp-command"
              value={state.command}
              placeholder={t('commandPlaceholder')}
              onChange={(event) => onChange({ command: event.target.value })}
            />
          </div>

          <div className="space-y-1.5">
            <label>{t('args')}</label>
            <div className="flex flex-wrap gap-1.5">
              {state.args.map((arg, index) => (
                <span
                  key={`${arg}-${index}`}
                  className="inline-flex items-center gap-1 rounded-md bg-muted px-2 py-0.5 font-mono moldy-ui-caption"
                >
                  {arg}
                  <button
                    type="button"
                    aria-label={t('removeArg', { arg })}
                    onClick={() =>
                      onChange({ args: state.args.filter((_, itemIndex) => itemIndex !== index) })
                    }
                    className="text-muted-foreground hover:text-destructive"
                  >
                    <X className="size-3" />
                  </button>
                </span>
              ))}
            </div>
            <div className="flex gap-2">
              <Input
                value={state.argDraft}
                placeholder={t('argsPlaceholder')}
                onChange={(event) => onChange({ argDraft: event.target.value })}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault()
                    onAddArg()
                  }
                }}
              />
              <Button type="button" variant="outline" onClick={onAddArg}>
                {t('add')}
              </Button>
            </div>
            <p className="moldy-ui-caption text-muted-foreground">{t('argsHint')}</p>
          </div>

          <KeyValueRows
            label={t('envVars')}
            rows={state.envVars}
            onRowsChange={(envVars) => onChange({ envVars })}
            keyPlaceholder="GITHUB_TOKEN"
            valuePlaceholder="{{ $credentials.token }}"
          />
        </div>
      ) : null}

      {isHttp ? (
        <div className="space-y-3">
          <div className="space-y-1.5">
            <label htmlFor="mcp-url">
              {t('url')} <span className="text-destructive">*</span>
            </label>
            <Input
              id="mcp-url"
              value={state.url}
              placeholder={t('urlPlaceholder')}
              onChange={(event) => onChange({ url: event.target.value })}
            />
          </div>
          <KeyValueRows
            label={t('headers')}
            rows={state.headers}
            onRowsChange={(headers) => onChange({ headers })}
            keyPlaceholder="Authorization"
            valuePlaceholder="Bearer {{ $credentials.token }}"
          />
        </div>
      ) : null}
    </section>
  )
}

function KeyValueRows({
  label,
  rows,
  onRowsChange,
  keyPlaceholder,
  valuePlaceholder,
}: {
  readonly label: string
  readonly rows: readonly McpWizardKeyValueRow[]
  readonly onRowsChange: (rows: readonly McpWizardKeyValueRow[]) => void
  readonly keyPlaceholder?: string
  readonly valuePlaceholder?: string
}) {
  const t = useTranslations('mcp.wizard.manual')
  function update(index: number, patch: Partial<McpWizardKeyValueRow>) {
    onRowsChange(rows.map((row, itemIndex) => (itemIndex === index ? { ...row, ...patch } : row)))
  }
  function remove(index: number) {
    onRowsChange(rows.filter((_, itemIndex) => itemIndex !== index))
  }
  function add() {
    onRowsChange([...rows, { key: '', value: '' }])
  }
  return (
    <div className="space-y-1.5">
      <label>{label}</label>
      <div className="space-y-1.5">
        {rows.map((row, index) => (
          <div key={index} className="flex gap-2">
            <Input
              value={row.key}
              placeholder={keyPlaceholder}
              onChange={(event) => update(index, { key: event.target.value })}
              className="font-mono text-xs"
            />
            <Input
              value={row.value}
              placeholder={valuePlaceholder}
              onChange={(event) => update(index, { value: event.target.value })}
              className="font-mono text-xs"
            />
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => remove(index)}
              aria-label={t('removeRow')}
            >
              <X className="size-3.5" />
            </Button>
          </div>
        ))}
        <Button type="button" size="sm" variant="outline" onClick={add}>
          <Plus className="size-3.5" /> {t('addRow')}
        </Button>
      </div>
    </div>
  )
}
