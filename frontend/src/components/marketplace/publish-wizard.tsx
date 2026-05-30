'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { DialogShell } from '@/components/shared/dialog-shell'
import { ApiError } from '@/lib/api/client'
import { usePublishSkill } from '@/lib/hooks/use-marketplace'
import type { Skill } from '@/lib/types/skill'
import type { PublishSkillBody } from '@/lib/types/marketplace'
import { cn } from '@/lib/utils'

type Step = 'review' | 'metadata' | 'visibility' | 'confirm' | 'done'

const STEPS: Step[] = ['review', 'metadata', 'visibility', 'confirm', 'done']

interface PublishWizardProps {
  skill: Skill | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function PublishWizard({ skill, open, onOpenChange }: PublishWizardProps) {
  return (
    <PublishWizardInner
      key={skill?.id ?? 'closed'}
      skill={skill}
      open={open}
      onOpenChange={onOpenChange}
    />
  )
}

function PublishWizardInner({ skill, open, onOpenChange }: PublishWizardProps) {
  const t = useTranslations('marketplace.publishWizard')
  const router = useRouter()
  const [step, setStep] = useState<Step>('review')
  const [name, setName] = useState(skill?.name ?? '')
  const [description, setDescription] = useState(skill?.description ?? '')
  const [releaseNotes, setReleaseNotes] = useState('')
  const [visibility, setVisibility] = useState<PublishSkillBody['visibility']>('private')
  const [aclInput, setAclInput] = useState('')
  const [error, setError] = useState<{ code?: string; message: string } | null>(null)
  const publish = usePublishSkill()

  if (!skill) return null

  const stepIndex = STEPS.indexOf(step)
  const isLast = step === 'done'

  async function submit() {
    if (!skill) return
    setError(null)
    const aclUserIds = aclInput
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)

    if (visibility === 'restricted' && aclUserIds.length === 0) {
      setError({
        code: 'marketplace_acl_required',
        message: t('errors.aclRequired'),
      })
      setStep('visibility')
      return
    }

    try {
      const created = await publish.mutateAsync({
        skillId: skill.id,
        body: {
          visibility,
          name: name || skill.name,
          description: description || null,
          release_notes: releaseNotes || null,
          acl_user_ids: aclUserIds,
        },
      })
      setStep('done')
      toast.success(t('toast.published'))
      router.push(`/marketplace/${created.id}`)
    } catch (err) {
      if (err instanceof ApiError) {
        setError({ code: err.code, message: err.message })
        if (err.code === 'marketplace_secret_detected') setStep('review')
        else if (err.code === 'marketplace_acl_required') setStep('visibility')
        else if (err.code === 'marketplace_invalid_visibility') setStep('visibility')
      } else {
        setError({ message: t('errors.network') })
      }
    }
  }

  function goNext() {
    if (step === 'confirm') {
      void submit()
      return
    }
    const next = STEPS[stepIndex + 1]
    if (next) setStep(next)
  }

  function goBack() {
    const prev = STEPS[stepIndex - 1]
    if (prev) setStep(prev)
  }

  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="xl" height="tall">
      <DialogShell.Header
        title={t('title', { name: skill.name })}
        description={t('description')}
      />

      <DialogShell.Split>
        <DialogShell.Sidebar>
          <ol className="space-y-1 text-sm" role="list">
            {STEPS.map((s, i) => (
              <li
                key={s}
                aria-current={s === step ? 'step' : undefined}
                className={cn(
                  'flex items-center gap-2 rounded-md px-2 py-1.5',
                  s === step
                    ? 'bg-primary/15 font-medium text-primary-strong'
                    : 'text-muted-foreground',
                )}
              >
                <span className="inline-flex size-5 items-center justify-center rounded-full bg-muted text-[10px]">
                  {i + 1}
                </span>
                <span className="capitalize">{t(`steps.${s}`)}</span>
              </li>
            ))}
          </ol>
        </DialogShell.Sidebar>

        <DialogShell.Body>
          {error ? (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
              {error.message}
            </div>
          ) : null}

          {step === 'review' ? (
            <div className="space-y-3 text-sm">
              <p>{t('reviewHint')}</p>
              <div className="rounded-md bg-muted p-3 text-xs">
                <p className="font-medium">{t('review.skill')}</p>
                <p>{skill.name}</p>
                <p className="text-muted-foreground">{skill.kind}</p>
              </div>
            </div>
          ) : null}

          {step === 'metadata' ? (
            <div className="space-y-4">
              <div className="space-y-1.5">
                <label htmlFor="pub-name">{t('fields.name')}</label>
                <Input id="pub-name" value={name} onChange={(e) => setName(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <label htmlFor="pub-desc">{t('fields.description')}</label>
                <Textarea
                  id="pub-desc"
                  rows={3}
                  value={description ?? ''}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <label htmlFor="pub-notes">{t('fields.releaseNotes')}</label>
                <Textarea
                  id="pub-notes"
                  rows={3}
                  value={releaseNotes}
                  onChange={(e) => setReleaseNotes(e.target.value)}
                />
              </div>
            </div>
          ) : null}

          {step === 'visibility' ? (
            <div className="space-y-4">
              <div className="space-y-1.5">
                <label htmlFor="pub-vis">{t('fields.visibility')}</label>
                <Select
                  value={visibility}
                  onValueChange={(v: string | null) =>
                    setVisibility((v ?? 'private') as PublishSkillBody['visibility'])
                  }
                >
                  <SelectTrigger id="pub-vis" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="private">{t('visibility.private')}</SelectItem>
                    <SelectItem value="restricted">{t('visibility.restricted')}</SelectItem>
                    <SelectItem value="public">{t('visibility.public')}</SelectItem>
                    <SelectItem value="unlisted">{t('visibility.unlisted')}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {visibility === 'restricted' ? (
                <div className="space-y-1.5">
                  <label htmlFor="pub-acl">{t('fields.acl')}</label>
                  <Input
                    id="pub-acl"
                    value={aclInput}
                    onChange={(e) => setAclInput(e.target.value)}
                    placeholder="00000000-0000-…, …"
                  />
                  <p className="text-xs text-muted-foreground">
                    {t('aclHint')}
                  </p>
                </div>
              ) : null}
              {visibility === 'public' ? (
                <div className="rounded-md bg-status-warn/10 p-3 text-xs text-status-warn">
                  {t('publicHint')}
                </div>
              ) : null}
            </div>
          ) : null}

          {step === 'confirm' ? (
            <div className="space-y-2 text-sm">
              <p>{t('confirmIntro')}</p>
              <ul className="list-inside list-disc text-muted-foreground">
                <li>{t('confirmCreateItem')}</li>
                <li>{t('confirmCreateVersion')}</li>
                <li>
                  {t('visibilityLabel')} <span className="font-medium text-foreground">{visibility}</span>
                </li>
                {visibility === 'restricted' ? (
                  <li>{t('aclSummary', { count: aclInput.split(',').filter(Boolean).length })}</li>
                ) : null}
              </ul>
            </div>
          ) : null}

          {step === 'done' ? (
            <div className="space-y-3 text-center">
              <p className="text-base font-medium">{t('done.title')}</p>
              <p className="text-sm text-muted-foreground">
                {t('done.description')}
              </p>
            </div>
          ) : null}
        </DialogShell.Body>
      </DialogShell.Split>

      <DialogShell.Footer>
        {isLast ? (
          <Button onClick={() => onOpenChange(false)}>{t('actions.close')}</Button>
        ) : (
          <>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              {t('actions.cancel')}
            </Button>
            {stepIndex > 0 ? (
              <Button variant="outline" onClick={goBack}>
                {t('actions.back')}
              </Button>
            ) : null}
            <Button onClick={goNext} disabled={publish.isPending}>
              {step === 'confirm'
                ? publish.isPending
                  ? t('actions.publishing')
                  : t('actions.publish')
                : t('actions.next')}
            </Button>
          </>
        )}
      </DialogShell.Footer>
    </DialogShell>
  )
}
