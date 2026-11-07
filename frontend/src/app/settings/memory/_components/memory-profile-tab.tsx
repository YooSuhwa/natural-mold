'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { AlertTriangleIcon, Loader2Icon, PlusIcon, RotateCcwIcon, Trash2Icon } from 'lucide-react'
import { toast } from 'sonner'

import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { useMemoryProfiles, useUpdateMemoryProfile } from '@/lib/hooks/use-memory'
import { useTriggers } from '@/lib/hooks/use-triggers'
import {
  MEMORY_PROFILE_SECTIONS,
  type MemoryProfile,
  type MemoryProfileLineInput,
  type MemoryProfileLineOrigin,
  type MemoryProfileSection,
} from '@/lib/types'
import { SelectField, type MemoryAgentOption } from './memory-form-fields'

const NONE_AGENT = '__none__'
const NONE_TRIGGER = '__none__'
/** Mirrors the backend's per-line limit (`MEMORY_PROFILE_LINE_TOO_LONG`). */
const LINE_MAX_CHARS = 300

interface DraftLine {
  /** Local React key; stable across edits. */
  key: string
  /** The saved line this draft started from (`null` for a new line). */
  id: string | null
  text: string
  savedText: string | null
  origin: MemoryProfileLineOrigin
  readable: boolean
  /** Trigger memos only (Q13); `null` for the other documents. */
  section: MemoryProfileSection | null
}

function lineSection(profile: MemoryProfile, section?: MemoryProfileSection | null) {
  return profile.kind === 'trigger_memo' ? (section ?? 'basics') : null
}

function toDraft(profile: MemoryProfile): DraftLine[] {
  return profile.lines.map((line) => ({
    key: line.id,
    id: line.id,
    text: line.text,
    savedText: line.text,
    origin: line.origin,
    readable: line.readable,
    section: lineSection(profile, line.section),
  }))
}

/** What the server state looks like to the editor; a change re-seeds a clean draft. */
function profileSignature(profile: MemoryProfile): string {
  return [
    profile.version,
    ...profile.lines.map((line) => `${line.id}:${line.readable ? 1 : 0}`),
  ].join('\u0000')
}

function isDirty(draft: DraftLine[], profile: MemoryProfile): boolean {
  if (draft.length !== profile.lines.length) return true
  return draft.some((line, index) => {
    const saved = profile.lines[index]
    return (
      line.id !== saved.id ||
      line.text !== saved.text ||
      line.section !== lineSection(profile, saved.section)
    )
  })
}

/** Payload lines: blank drafts are dropped; saved lines keep their id (5.12). */
function profileLinesPayload(draft: DraftLine[]): MemoryProfileLineInput[] {
  return draft
    .map((line) => ({
      id: line.id,
      text: line.text.trim(),
      ...(line.section ? { section: line.section } : {}),
    }))
    .filter((line) => line.text.length > 0)
}

/**
 * 내 프로필 탭 (plan 15, 5.9 / 5.12): 내 프로필 문서와 에이전트별 작업 메모 편집.
 * 줄마다 출처(직접 씀 / 프로필에 올림 / 정리 작업)를 보이고, 근거 기억이 철회·삭제돼
 * 실행에서 빠지는 줄을 표시한다. 저장은 편집을 시작한 판(version)을 함께 보낸다.
 */
export function MemoryProfileTab({ agents }: { agents: MemoryAgentOption[] }) {
  const t = useTranslations('appSettings.memory.profile')
  const [agentId, setAgentId] = useState<string | null>(null)
  const profiles = useMemoryProfiles(null)
  const userProfile = profiles.data?.user_profile

  return (
    <div className="space-y-4" data-testid="memory-profile-tab">
      <SettingsSectionCard title={t('userTitle')} description={t('userDescription')}>
        {userProfile ? (
          <ProfileEditor profile={userProfile} agentId={null} />
        ) : (
          <p className="text-sm text-muted-foreground">
            {profiles.isLoading ? t('loading') : t('loadFailed')}
          </p>
        )}
      </SettingsSectionCard>
      <SettingsSectionCard title={t('workTitle')} description={t('workDescription')}>
        <div className="space-y-4">
          <SelectField
            id="memory-profile-agent"
            label={t('agentLabel')}
            value={agentId ?? NONE_AGENT}
            options={[
              { value: NONE_AGENT, label: t('agentNone') },
              ...agents.map((agent) => ({ value: agent.id, label: agent.name })),
            ]}
            onChange={(value) => setAgentId(value === NONE_AGENT ? null : value)}
          />
          {agentId ? <WorkMemoSection key={agentId} agentId={agentId} /> : null}
        </div>
      </SettingsSectionCard>
      {agentId ? <TriggerMemoCard key={agentId} agentId={agentId} /> : null}
    </div>
  )
}

/** 예약 메모 (Q13): one schedule's memo, read only by that schedule's runs. */
function TriggerMemoCard({ agentId }: { agentId: string }) {
  const t = useTranslations('appSettings.memory.profile')
  const triggers = useTriggers(agentId)
  const [triggerId, setTriggerId] = useState<string | null>(null)
  const list = triggers.data ?? []
  return (
    <SettingsSectionCard title={t('triggerTitle')} description={t('triggerDescription')}>
      <div className="space-y-4">
        {list.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {triggers.isLoading ? t('loading') : t('triggerEmpty')}
          </p>
        ) : (
          <SelectField
            id="memory-profile-trigger"
            label={t('triggerLabel')}
            value={triggerId ?? NONE_TRIGGER}
            options={[
              { value: NONE_TRIGGER, label: t('triggerNone') },
              ...list.map((trigger) => ({ value: trigger.id, label: trigger.name })),
            ]}
            onChange={(value) => setTriggerId(value === NONE_TRIGGER ? null : value)}
          />
        )}
        {triggerId ? (
          <TriggerMemoSection key={triggerId} agentId={agentId} triggerId={triggerId} />
        ) : null}
      </div>
    </SettingsSectionCard>
  )
}

function TriggerMemoSection({ agentId, triggerId }: { agentId: string; triggerId: string }) {
  const t = useTranslations('appSettings.memory.profile')
  const profiles = useMemoryProfiles(agentId, triggerId)
  const memo = profiles.data?.trigger_memo
  if (!memo) {
    return (
      <p className="text-sm text-muted-foreground">
        {profiles.isLoading ? t('loading') : t('loadFailed')}
      </p>
    )
  }
  return <ProfileEditor profile={memo} agentId={agentId} triggerId={triggerId} />
}

function WorkMemoSection({ agentId }: { agentId: string }) {
  const t = useTranslations('appSettings.memory.profile')
  const profiles = useMemoryProfiles(agentId)
  const workMemo = profiles.data?.work_memo
  if (!workMemo) {
    return (
      <p className="text-sm text-muted-foreground">
        {profiles.isLoading ? t('loading') : t('loadFailed')}
      </p>
    )
  }
  return <ProfileEditor profile={workMemo} agentId={agentId} />
}

function ProfileEditor({
  profile,
  agentId,
  triggerId = null,
}: {
  profile: MemoryProfile
  agentId: string | null
  triggerId?: string | null
}) {
  const t = useTranslations('appSettings.memory.profile')
  const save = useUpdateMemoryProfile()
  const [seed, setSeed] = useState(profile)
  const [draft, setDraft] = useState<DraftLine[]>(() => toDraft(profile))
  const [nextKey, setNextKey] = useState(0)
  const dirty = isDirty(draft, seed)

  // Server content moved on (a pin, another tab, a revoked source): re-seed a clean draft,
  // but never overwrite the person's unsaved edits.
  if (profileSignature(profile) !== profileSignature(seed) && !dirty) {
    setSeed(profile)
    setDraft(toDraft(profile))
  }

  function reset() {
    setSeed(profile)
    setDraft(toDraft(profile))
  }

  function updateLine(key: string, text: string) {
    setDraft((lines) => lines.map((line) => (line.key === key ? { ...line, text } : line)))
  }

  function updateSection(key: string, section: MemoryProfileSection) {
    setDraft((lines) => lines.map((line) => (line.key === key ? { ...line, section } : line)))
  }

  function removeLine(key: string) {
    setDraft((lines) => lines.filter((line) => line.key !== key))
  }

  function addLine() {
    setDraft((lines) => [
      ...lines,
      {
        key: `new-${nextKey}`,
        id: null,
        text: '',
        savedText: null,
        origin: 'user',
        readable: true,
        section: lineSection(profile),
      },
    ])
    setNextKey((value) => value + 1)
  }

  async function handleSave() {
    try {
      const saved = await save.mutateAsync({
        kind: profile.kind,
        data: {
          agent_id: agentId,
          ...(triggerId ? { trigger_id: triggerId } : {}),
          lines: profileLinesPayload(draft),
          version: seed.version,
        },
      })
      setSeed(saved)
      setDraft(toDraft(saved))
      toast.success(t('saved'))
    } catch (error) {
      toast.error(error instanceof Error && error.message ? error.message : t('saveFailed'))
    }
  }

  const overBudget = profile.tokens >= profile.budget_tokens
  const unreadable = draft.filter((line) => line.text === line.savedText && !line.readable)

  return (
    <div className="space-y-4" data-testid={`memory-profile-editor-${profile.kind}`}>
      <div className="space-y-1">
        <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
          <span>{t('tokensLabel')}</span>
          <span data-testid={`memory-profile-tokens-${profile.kind}`}>
            {t('tokens', { tokens: profile.tokens, budget: profile.budget_tokens })}
          </span>
        </div>
        <meter
          className="h-2 w-full"
          min={0}
          max={profile.budget_tokens}
          value={Math.min(profile.tokens, profile.budget_tokens)}
          aria-label={t('tokensLabel')}
        />
        {overBudget ? <p className="text-xs text-destructive">{t('overBudget')}</p> : null}
      </div>

      {unreadable.length > 0 ? (
        <p
          className="flex items-start gap-2 rounded-lg border border-border/70 p-3 text-xs text-muted-foreground"
          data-testid="memory-profile-unreadable-notice"
        >
          <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" aria-hidden />
          {t('unreadableNotice')}
        </p>
      ) : null}

      {draft.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('empty')}</p>
      ) : (
        <ol className="space-y-2">
          {draft.map((line, index) => {
            const unchanged = line.text === line.savedText
            const origin: MemoryProfileLineOrigin = unchanged ? line.origin : 'user'
            const skipped = unchanged && !line.readable
            return (
              <li
                key={line.key}
                className="space-y-2 rounded-lg border border-border/70 bg-background p-3"
                data-testid="memory-profile-line"
                data-unreadable={skipped ? 'true' : undefined}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="outline">{t(`origin.${origin}`)}</Badge>
                  {line.section ? (
                    <Select
                      value={line.section}
                      onValueChange={(next) =>
                        next && updateSection(line.key, next as MemoryProfileSection)
                      }
                    >
                      <SelectTrigger
                        size="sm"
                        aria-label={t('sectionLabel', { index: index + 1 })}
                        data-testid="memory-profile-line-section"
                      >
                        <span>{t(`section.${line.section}`)}</span>
                      </SelectTrigger>
                      <SelectContent>
                        {MEMORY_PROFILE_SECTIONS.map((section) => (
                          <SelectItem key={section} value={section}>
                            {t(`section.${section}`)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : null}
                  {skipped ? (
                    <Badge variant="destructive" data-testid="memory-profile-line-unreadable">
                      {t('unreadable')}
                    </Badge>
                  ) : null}
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    className="ml-auto"
                    onClick={() => removeLine(line.key)}
                    aria-label={t('removeLine', { index: index + 1 })}
                    data-testid="memory-profile-remove-line"
                  >
                    <Trash2Icon className="size-4" aria-hidden />
                  </Button>
                </div>
                <Textarea
                  value={line.text}
                  maxLength={LINE_MAX_CHARS}
                  onChange={(event) => updateLine(line.key, event.target.value)}
                  className="min-h-12"
                  aria-label={t('lineLabel', { index: index + 1 })}
                  data-testid="memory-profile-line-input"
                />
              </li>
            )
          })}
        </ol>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={addLine}
          data-testid="memory-profile-add-line"
        >
          <PlusIcon className="size-4" aria-hidden />
          {t('addLine')}
        </Button>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={reset}
            disabled={!dirty || save.isPending}
          >
            <RotateCcwIcon className="size-4" aria-hidden />
            {t('reset')}
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={() => void handleSave()}
            disabled={!dirty || save.isPending}
            data-testid={`memory-profile-save-${profile.kind}`}
          >
            {save.isPending ? <Loader2Icon className="size-4 animate-spin" aria-hidden /> : null}
            {t('save')}
          </Button>
        </div>
      </div>
    </div>
  )
}
