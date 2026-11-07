'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { Loader2Icon, NetworkIcon } from 'lucide-react'
import { toast } from 'sonner'

import { DialogShell } from '@/components/shared/dialog-shell'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { ApiError } from '@/lib/api/client'
import { useSession } from '@/lib/auth/session'
import { useMemoryVectorStatus, useUpdateMemoryVector } from '@/lib/hooks/use-memory'
import type { MemoryVectorModel, MemoryVectorStatus } from '@/lib/types'
import { formatDisplayNumber } from '@/lib/utils/display-format'
import { SelectField } from './memory-form-fields'

const OFF = 'off'

type PendingChange =
  | { kind: 'choose'; model: MemoryVectorModel; count: number; recounted: boolean }
  | { kind: 'off' }

/** Memories to embed again for `target`: only the current model's vectors are counted. */
function reembedCount(status: MemoryVectorStatus, target: MemoryVectorModel): number {
  return status.model?.id === target.id ? status.total - status.embedded : status.total
}

/** The server's count from a `MEMORY_VECTOR_COUNT_CHANGED` message ("…(12개)…"). */
function countFromMessage(message: string): number | null {
  const match = /(\d+)/.exec(message)
  return match ? Number(match[1]) : null
}

/**
 * Plan 15 Q14 "기억 검색 벡터 모델": off or one registered embedding model, chosen for the
 * company (a personal tenant's owner sees the same item). Changing it re-embeds the
 * memories after a confirmation that names how many; keyword recall carries on meanwhile.
 */
export function MemoryVectorCard() {
  const t = useTranslations('appSettings.memory.vector')
  const status = useMemoryVectorStatus()
  const update = useUpdateMemoryVector()
  const { data: user } = useSession()
  const [pending, setPending] = useState<PendingChange | null>(null)
  const data = status.data

  function handleSelect(value: string) {
    if (!data) return
    if (value === OFF) {
      if (data.model !== null) setPending({ kind: 'off' })
      return
    }
    const target = data.candidates.find((candidate) => candidate.id === value)
    if (!target || target.id === data.model?.id) return
    setPending({
      kind: 'choose',
      model: target,
      count: reembedCount(data, target),
      recounted: false,
    })
  }

  function handleChoose(model: MemoryVectorModel, count: number) {
    update.mutate(
      { model_id: model.id, confirm_count: count },
      {
        onSuccess: () => {
          setPending(null)
          toast.success(t('saved'))
        },
        onError: async (error) => {
          if (error instanceof ApiError && error.code === 'MEMORY_VECTOR_COUNT_CHANGED') {
            // The count moved under us: re-read and ask again with the server's number.
            const fresh = await status.refetch()
            const serverCount = countFromMessage(error.message)
            const next = serverCount ?? (fresh.data ? reembedCount(fresh.data, model) : count)
            setPending({ kind: 'choose', model, count: next, recounted: true })
            return
          }
          setPending(null)
          toast.error(error instanceof Error ? error.message : t('failed'))
        },
      },
    )
  }

  function handleTurnOff(dropVectors: boolean) {
    update.mutate(
      { model_id: null, confirm_count: 0, drop_vectors: dropVectors },
      {
        onSuccess: () => {
          setPending(null)
          toast.success(t('turnedOff'))
        },
        onError: (error) => {
          setPending(null)
          toast.error(error instanceof Error ? error.message : t('failed'))
        },
      },
    )
  }

  return (
    <SettingsSectionCard
      title={
        <span className="flex items-center gap-2">
          <NetworkIcon className="size-4" aria-hidden />
          {t('title')}
        </span>
      }
      description={t('description')}
      actions={
        data ? (
          <Badge variant={data.model ? 'default' : 'outline'}>
            {data.model ? t('onBadge') : t('offBadge')}
          </Badge>
        ) : null
      }
    >
      {status.isLoading ? (
        <p className="text-sm text-muted-foreground">{t('loading')}</p>
      ) : !data ? (
        <p className="text-sm text-destructive">{t('loadFailed')}</p>
      ) : (
        <div className="space-y-4" data-testid="memory-vector-card">
          <VectorState status={data} />
          <SelectField
            id="memory-vector-model"
            label={t('select')}
            value={data.model?.id ?? OFF}
            options={[
              { value: OFF, label: t('off') },
              ...data.candidates.map((candidate) => ({
                value: candidate.id,
                label: t('option', {
                  name: candidate.display_name,
                  dimensions: candidate.dimensions,
                }),
              })),
            ]}
            disabled={!data.can_manage || data.candidates.length === 0 || update.isPending}
            onChange={handleSelect}
          />
          {data.candidates.length === 0 ? (
            <p className="text-sm text-muted-foreground" data-testid="memory-vector-no-models">
              {t('noModels')}
              {user?.is_super_user ? (
                <>
                  {' '}
                  <Link href="/settings/models" className="text-primary-strong underline">
                    {t('registerHint')}
                  </Link>
                </>
              ) : null}
            </p>
          ) : null}
          {!data.can_manage ? (
            <p className="text-sm text-muted-foreground" data-testid="memory-vector-read-only">
              {t('readOnly')}
            </p>
          ) : null}
        </div>
      )}
      {pending?.kind === 'choose' ? (
        <ChooseDialog
          model={pending.model}
          count={pending.count}
          recounted={pending.recounted}
          busy={update.isPending || status.isFetching}
          onCancel={() => setPending(null)}
          onConfirm={() => handleChoose(pending.model, pending.count)}
        />
      ) : null}
      {pending?.kind === 'off' ? (
        <TurnOffDialog
          busy={update.isPending}
          onCancel={() => setPending(null)}
          onConfirm={handleTurnOff}
        />
      ) : null}
    </SettingsSectionCard>
  )
}

function VectorState({ status }: { status: MemoryVectorStatus }) {
  const t = useTranslations('appSettings.memory.vector')
  const running = status.job_state === 'queued' || status.job_state === 'running'
  return (
    <div className="space-y-1 text-sm" role="status" data-testid="memory-vector-state">
      {status.model ? (
        <p>
          {t('current', {
            name: status.model.display_name,
            dimensions: status.model.dimensions,
            embedded: formatDisplayNumber(status.embedded),
            total: formatDisplayNumber(status.total),
          })}
        </p>
      ) : (
        <p>{t('currentOff')}</p>
      )}
      {status.model && running ? (
        <p className="text-muted-foreground" data-testid="memory-vector-job">
          {status.job_state === 'running'
            ? t('jobRunning', { progress: status.job_progress ?? 0 })
            : t('jobQueued')}
        </p>
      ) : null}
      {status.model && !status.floor_measured ? (
        <p className="text-muted-foreground" data-testid="memory-vector-unmeasured">
          {t('floorUnmeasured')}
        </p>
      ) : null}
      {status.other_vectors > 0 ? (
        <p className="text-muted-foreground" data-testid="memory-vector-other">
          {status.model
            ? t('otherVectorsReplacing', { count: status.other_vectors })
            : t('otherVectorsKept', { count: status.other_vectors })}
        </p>
      ) : null}
    </div>
  )
}

function ChooseDialog({
  model,
  count,
  recounted,
  busy,
  onCancel,
  onConfirm,
}: {
  model: MemoryVectorModel
  count: number
  recounted: boolean
  busy: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  const t = useTranslations('appSettings.memory.vector.chooseDialog')
  const tCommon = useTranslations('common')
  return (
    <DialogShell open onOpenChange={(open) => !open && onCancel()} size="sm" height="auto">
      <DialogShell.Header
        title={t('title', { name: model.display_name })}
        description={t('model', { name: model.model_name, dimensions: model.dimensions })}
      />
      <DialogShell.Body>
        <div className="space-y-2 text-sm" data-testid="memory-vector-confirm">
          {recounted ? (
            <p className="text-destructive" role="alert">
              {t('recounted')}
            </p>
          ) : null}
          <p>{t('body', { count })}</p>
        </div>
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button type="button" variant="outline" onClick={onCancel} disabled={busy}>
          {tCommon('cancel')}
        </Button>
        <Button type="button" onClick={onConfirm} disabled={busy}>
          {busy ? <Loader2Icon className="size-4 animate-spin" aria-hidden /> : null}
          {t('confirm')}
        </Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}

function TurnOffDialog({
  busy,
  onCancel,
  onConfirm,
}: {
  busy: boolean
  onCancel: () => void
  onConfirm: (dropVectors: boolean) => void
}) {
  const t = useTranslations('appSettings.memory.vector.offDialog')
  const tCommon = useTranslations('common')
  const [dropVectors, setDropVectors] = useState(false)
  return (
    <DialogShell open onOpenChange={(open) => !open && onCancel()} size="sm" height="auto">
      <DialogShell.Header title={t('title')} description={t('description')} />
      <DialogShell.Body>
        <div className="space-y-2" data-testid="memory-vector-off">
          <label htmlFor="memory-vector-drop" className="flex items-center gap-2 text-sm">
            <Checkbox
              id="memory-vector-drop"
              checked={dropVectors}
              onCheckedChange={(value) => setDropVectors(Boolean(value))}
            />
            {t('drop')}
          </label>
          <p className="moldy-ui-caption text-muted-foreground">
            {dropVectors ? t('dropHelp') : t('keepHelp')}
          </p>
        </div>
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button type="button" variant="outline" onClick={onCancel} disabled={busy}>
          {tCommon('cancel')}
        </Button>
        <Button
          type="button"
          variant={dropVectors ? 'destructive' : 'default'}
          onClick={() => onConfirm(dropVectors)}
          disabled={busy}
        >
          {busy ? <Loader2Icon className="size-4 animate-spin" aria-hidden /> : null}
          {t('confirm')}
        </Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}
