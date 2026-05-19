'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
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
        message: 'restricted 게시는 최소 1명의 공유 대상이 필요합니다.',
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
      toast.success('Published to marketplace')
      router.push(`/marketplace/${created.id}`)
    } catch (err) {
      if (err instanceof ApiError) {
        setError({ code: err.code, message: err.message })
        if (err.code === 'marketplace_secret_detected') setStep('review')
        else if (err.code === 'marketplace_acl_required') setStep('visibility')
        else if (err.code === 'marketplace_invalid_visibility') setStep('visibility')
      } else {
        setError({ message: '네트워크 오류가 발생했습니다.' })
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
        title={`Publish ${skill.name}`}
        description="Make this skill discoverable in the marketplace."
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
                <span className="capitalize">{labelForStep(s)}</span>
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
              <p>
                마켓플레이스에 게시되기 전 secret 검사가 자동 실행됩니다. 차단된 파일이 있으면 1단계로
                돌아와 제거 후 다시 시도하세요.
              </p>
              <div className="rounded-md bg-muted p-3 text-xs">
                <p className="font-medium">Skill</p>
                <p>{skill.name}</p>
                <p className="text-muted-foreground">{skill.kind}</p>
              </div>
            </div>
          ) : null}

          {step === 'metadata' ? (
            <div className="space-y-4">
              <div className="space-y-1.5">
                <label htmlFor="pub-name">Name</label>
                <Input id="pub-name" value={name} onChange={(e) => setName(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <label htmlFor="pub-desc">Description</label>
                <Textarea
                  id="pub-desc"
                  rows={3}
                  value={description ?? ''}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <label htmlFor="pub-notes">Release notes (optional)</label>
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
                <label htmlFor="pub-vis">Visibility</label>
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
                    <SelectItem value="private">Private (me only)</SelectItem>
                    <SelectItem value="restricted">Restricted (selected users)</SelectItem>
                    <SelectItem value="public">Public (pending listing)</SelectItem>
                    <SelectItem value="unlisted">Unlisted (link only)</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {visibility === 'restricted' ? (
                <div className="space-y-1.5">
                  <label htmlFor="pub-acl">Shared user IDs (comma-separated)</label>
                  <Input
                    id="pub-acl"
                    value={aclInput}
                    onChange={(e) => setAclInput(e.target.value)}
                    placeholder="00000000-0000-…, …"
                  />
                  <p className="text-xs text-muted-foreground">
                    Phase 1: user lookup picker는 다음 슬라이스에서 추가됩니다. 임시로 user UUID를 직접
                    입력하세요.
                  </p>
                </div>
              ) : null}
              {visibility === 'public' ? (
                <div className="rounded-md bg-status-warn/10 p-3 text-xs text-status-warn">
                  공개 publish 후에도 카탈로그 검색 노출은 운영자 승인이 필요합니다.
                </div>
              ) : null}
            </div>
          ) : null}

          {step === 'confirm' ? (
            <div className="space-y-2 text-sm">
              <p>다음을 수행합니다:</p>
              <ul className="list-inside list-disc text-muted-foreground">
                <li>마켓플레이스 item 생성</li>
                <li>새 version snapshot 생성</li>
                <li>가시성: <span className="font-medium text-foreground">{visibility}</span></li>
                {visibility === 'restricted' ? (
                  <li>ACL: {aclInput.split(',').filter(Boolean).length} user(s)</li>
                ) : null}
              </ul>
            </div>
          ) : null}

          {step === 'done' ? (
            <div className="space-y-3 text-center">
              <p className="text-base font-medium">Published</p>
              <p className="text-sm text-muted-foreground">
                Redirecting to the marketplace detail page…
              </p>
            </div>
          ) : null}
        </DialogShell.Body>
      </DialogShell.Split>

      <DialogShell.Footer>
        {isLast ? (
          <Button onClick={() => onOpenChange(false)}>Close</Button>
        ) : (
          <>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            {stepIndex > 0 ? (
              <Button variant="outline" onClick={goBack}>
                Back
              </Button>
            ) : null}
            <Button onClick={goNext} disabled={publish.isPending}>
              {step === 'confirm'
                ? publish.isPending
                  ? 'Publishing…'
                  : 'Publish'
                : 'Next'}
            </Button>
          </>
        )}
      </DialogShell.Footer>
    </DialogShell>
  )
}

function labelForStep(step: Step): string {
  if (step === 'review') return 'Review'
  if (step === 'metadata') return 'Metadata'
  if (step === 'visibility') return 'Visibility'
  if (step === 'confirm') return 'Confirm'
  return 'Done'
}
