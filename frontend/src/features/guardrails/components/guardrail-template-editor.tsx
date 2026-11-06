'use client'

import { useState } from 'react'
import { TrashIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { DialogShell } from '@/components/shared/dialog-shell'
import { FormFieldShell } from '@/components/shared/form-field-shell'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { useGuardrailTemplateMutations } from '@/lib/hooks/use-guardrails'
import type {
  GuardrailAction,
  GuardrailPseudonymStyle,
  GuardrailRule,
  GuardrailStage,
  GuardrailTemplate,
} from '@/lib/types/guardrails'
import { actionsFor, GUARDRAIL_STAGES, useGuardrailErrorMessage, useRuleLabel } from '../lib/labels'

const STYLES: readonly GuardrailPseudonymStyle[] = ['tag', 'natural']

interface GuardrailTemplateEditorProps {
  tenantId: string
  template: GuardrailTemplate
  onClose: () => void
}

/**
 * Edits a template's draft: each rule's action and stages, pseudonym options, removal.
 * What a rule detects (packs, entities) stays as copied; publishing makes the draft a version.
 */
export function GuardrailTemplateEditor({
  tenantId,
  template,
  onClose,
}: GuardrailTemplateEditorProps) {
  const t = useTranslations('guardrails')
  const tCommon = useTranslations('orgAdmin.common')
  const ruleLabel = useRuleLabel()
  const errorMessage = useGuardrailErrorMessage()
  const { update } = useGuardrailTemplateMutations(tenantId)
  const [name, setName] = useState(template.name)
  const [description, setDescription] = useState(template.description)
  const [rules, setRules] = useState<GuardrailRule[]>(template.draft_rules)
  const [error, setError] = useState<string | null>(null)
  const stageless = rules.some((rule) => rule.stages.length === 0)
  const badInput = rules.some((rule) => !actionsFor(rule).includes(rule.action))
  const valid = name.trim() !== '' && rules.length > 0 && !stageless && !badInput

  function change(ruleId: string, patch: Partial<GuardrailRule>) {
    setRules((prev) => prev.map((rule) => (rule.id === ruleId ? { ...rule, ...patch } : rule)))
  }

  function toggleStage(rule: GuardrailRule, stage: GuardrailStage, on: boolean) {
    const stages = on ? [...rule.stages, stage] : rule.stages.filter((item) => item !== stage)
    change(rule.id, { stages: GUARDRAIL_STAGES.filter((item) => stages.includes(item)) })
  }

  function save() {
    setError(null)
    update.mutate(
      {
        id: template.id,
        data: { name: name.trim(), description: description.trim(), rules },
        version: template.updated_at,
      },
      {
        onSuccess: () => {
          toast.success(t('templates.editor.saved'))
          onClose()
        },
        onError: (err) => setError(errorMessage(err)),
      },
    )
  }

  return (
    <DialogShell open onOpenChange={(open) => (open ? undefined : onClose())} size="lg">
      <DialogShell.Header
        title={t('templates.editor.title', { name: template.name })}
        description={t('templates.editor.description')}
      />
      <DialogShell.Body>
        <div className="space-y-4">
          <FormFieldShell id="guardrail-editor-name" label={t('templates.editor.name')} required>
            <Input
              id="guardrail-editor-name"
              value={name}
              maxLength={80}
              onChange={(event) => setName(event.target.value)}
            />
          </FormFieldShell>
          <FormFieldShell
            id="guardrail-editor-description"
            label={t('templates.editor.templateDescription')}
          >
            <Textarea
              id="guardrail-editor-description"
              value={description}
              maxLength={500}
              onChange={(event) => setDescription(event.target.value)}
            />
          </FormFieldShell>
          <section className="space-y-3">
            <h3 className="moldy-ui-label text-foreground">{t('templates.editor.rules')}</h3>
            {rules.length === 0 ? (
              <p className="text-sm text-destructive">{t('templates.editor.needRule')}</p>
            ) : null}
            {rules.map((rule) => (
              <RuleEditor
                key={rule.id}
                rule={rule}
                label={ruleLabel(rule.id)}
                onAction={(action) => change(rule.id, { action })}
                onStage={(stage, on) => toggleStage(rule, stage, on)}
                onStyle={(style) => change(rule.id, { pseudonym_style: style })}
                onRestore={(on) => change(rule.id, { restore_for_tools: on })}
                onRemove={() => setRules((prev) => prev.filter((item) => item.id !== rule.id))}
              />
            ))}
          </section>
          {error ? <p className="text-sm text-destructive">{error}</p> : null}
        </div>
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose}>
          {tCommon('cancel')}
        </Button>
        <Button disabled={!valid || update.isPending} onClick={save}>
          {t('templates.editor.save')}
        </Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}

interface RuleEditorProps {
  rule: GuardrailRule
  label: string
  onAction: (action: GuardrailAction) => void
  onStage: (stage: GuardrailStage, on: boolean) => void
  onStyle: (style: GuardrailPseudonymStyle) => void
  onRestore: (on: boolean) => void
  onRemove: () => void
}

function RuleEditor({
  rule,
  label,
  onAction,
  onStage,
  onStyle,
  onRestore,
  onRemove,
}: RuleEditorProps) {
  const t = useTranslations('guardrails')
  const actionId = `guardrail-rule-${rule.id}-action`
  const styleId = `guardrail-rule-${rule.id}-style`
  const restoreId = `guardrail-rule-${rule.id}-restore`
  return (
    <div className="space-y-3 rounded-md border border-border p-3">
      <div className="flex items-center gap-2">
        <p className="min-w-0 flex-1 text-sm text-foreground">{label}</p>
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label={t('templates.editor.removeRule', { rule: label })}
          onClick={onRemove}
        >
          <TrashIcon />
        </Button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <FormFieldShell id={actionId} label={t('templates.editor.action')}>
          <Select
            value={rule.action}
            onValueChange={(value) => onAction(String(value) as GuardrailAction)}
          >
            <SelectTrigger id={actionId} className="w-full">
              <span>{t(`action.${rule.action}`)}</span>
            </SelectTrigger>
            <SelectContent>
              {actionsFor(rule).map((action) => (
                <SelectItem key={action} value={action}>
                  {t(`action.${action}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </FormFieldShell>
        {rule.action === 'pseudonymize' ? (
          <FormFieldShell id={styleId} label={t('templates.editor.style')}>
            <Select
              value={rule.pseudonym_style}
              onValueChange={(value) => onStyle(String(value) as GuardrailPseudonymStyle)}
            >
              <SelectTrigger id={styleId} className="w-full">
                <span>{t(`style.${rule.pseudonym_style}`)}</span>
              </SelectTrigger>
              <SelectContent>
                {STYLES.map((style) => (
                  <SelectItem key={style} value={style}>
                    {t(`style.${style}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FormFieldShell>
        ) : null}
      </div>
      <fieldset className="space-y-1" aria-label={`${label} · ${t('templates.editor.stages')}`}>
        <legend className="moldy-ui-caption text-muted-foreground">
          {t('templates.editor.stages')}
        </legend>
        <div className="flex flex-wrap gap-x-4 gap-y-2">
          {GUARDRAIL_STAGES.map((stage) => (
            <label key={stage} className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={rule.stages.includes(stage)}
                onCheckedChange={(checked) => onStage(stage, checked === true)}
              />
              {t(`stage.${stage}`)}
            </label>
          ))}
        </div>
        {rule.stages.length === 0 ? (
          <p className="text-sm text-destructive">{t('templates.editor.needStage')}</p>
        ) : null}
        {actionsFor(rule).includes(rule.action) ? null : (
          <p className="text-sm text-destructive">{t('templates.editor.inputActions')}</p>
        )}
      </fieldset>
      {rule.action === 'pseudonymize' ? (
        <div className="flex items-start gap-3 text-sm">
          <Switch
            checked={rule.restore_for_tools}
            aria-labelledby={restoreId}
            aria-describedby={`${restoreId}-hint`}
            onCheckedChange={(checked) => onRestore(checked === true)}
          />
          <span>
            <span id={restoreId} className="block text-foreground">
              {t('templates.editor.restoreForTools')}
            </span>
            <span id={`${restoreId}-hint`} className="moldy-ui-caption block text-muted-foreground">
              {t('templates.editor.restoreForToolsHint')}
            </span>
          </span>
        </div>
      ) : null}
    </div>
  )
}
