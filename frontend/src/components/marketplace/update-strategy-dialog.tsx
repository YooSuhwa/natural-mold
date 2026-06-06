'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { DialogShell } from '@/components/shared/dialog-shell'
import { ApiError } from '@/lib/api/client'
import { useUpdateInstallation } from '@/lib/hooks/use-marketplace'
import type { MarketplaceItem, UpdateStrategy } from '@/lib/types/marketplace'
import { cn } from '@/lib/utils'

interface UpdateStrategyDialogProps {
  item: MarketplaceItem | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

interface StrategyOption {
  value: UpdateStrategy
  labelKey: string
  descriptionKey: string
  /** True when this strategy is risky in the dirty case. */
  destructiveWhenDirty?: boolean
}

const STRATEGIES: StrategyOption[] = [
  {
    value: 'overwrite',
    labelKey: 'strategies.overwrite.label',
    descriptionKey: 'strategies.overwrite.description',
    destructiveWhenDirty: true,
  },
  {
    value: 'install_new_copy',
    labelKey: 'strategies.install_new_copy.label',
    descriptionKey: 'strategies.install_new_copy.description',
  },
  {
    value: 'keep_current',
    labelKey: 'strategies.keep_current.label',
    descriptionKey: 'strategies.keep_current.description',
  },
]

export function UpdateStrategyDialog({ item, open, onOpenChange }: UpdateStrategyDialogProps) {
  // remount-on-target swap pattern (AGENTS.md): key 로 state 자동 reset.
  return (
    <UpdateStrategyDialogInner
      key={item?.installation.installation_id ?? 'closed'}
      item={item}
      open={open}
      onOpenChange={onOpenChange}
    />
  )
}

function UpdateStrategyDialogInner({ item, open, onOpenChange }: UpdateStrategyDialogProps) {
  const t = useTranslations('marketplace.updateStrategy')
  // dirty 상태이면 destructive strategy를 1차 선택지로 띄우지 않는다 — 안전한
  // install_new_copy 를 default 로. 사용자가 의식적으로 overwrite 를 선택해야
  // 한다.
  const dirty = !!item?.installation.dirty
  const [strategy, setStrategy] = useState<UpdateStrategy>(dirty ? 'install_new_copy' : 'overwrite')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const installationId = item?.installation.installation_id ?? ''
  const update = useUpdateInstallation(installationId)

  if (!item) return null

  async function handleConfirm() {
    if (!item || !installationId) return
    if (strategy === 'keep_current') {
      onOpenChange(false)
      return
    }
    setErrorMessage(null)
    try {
      await update.mutateAsync({ strategy })
      toast.success(
        strategy === 'overwrite'
          ? t('toast.updated', { name: item.name })
          : t('toast.installedNewCopy', { name: item.name }),
      )
      onOpenChange(false)
    } catch (err) {
      if (err instanceof ApiError) {
        setErrorMessage(
          err.code === 'marketplace_dirty_installation'
            ? t('errors.dirty')
            : err.code === 'marketplace_item_disabled'
              ? t('errors.disabled')
              : err.message || t('errors.failed'),
        )
      } else {
        setErrorMessage(t('errors.network'))
      }
    }
  }

  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="md" height="auto">
      <DialogShell.Header
        title={t('title', { name: item.name })}
        description={dirty ? t('description.dirty') : t('description.default')}
      />

      <DialogShell.Body>
        {errorMessage ? (
          <div className="mb-3 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
            {errorMessage}
          </div>
        ) : null}

        <fieldset className="space-y-2" aria-label={t('ariaLabel')}>
          {STRATEGIES.map((opt) => {
            const selected = strategy === opt.value
            const warning = dirty && opt.destructiveWhenDirty
            return (
              <label
                key={opt.value}
                className={cn(
                  'flex cursor-pointer items-start gap-3 rounded-md border p-3 transition-colors',
                  selected
                    ? 'border-primary-strong/60 bg-primary/10'
                    : 'border-border hover:bg-muted/50',
                )}
              >
                <input
                  type="radio"
                  name="update-strategy"
                  value={opt.value}
                  checked={selected}
                  onChange={() => setStrategy(opt.value)}
                  className="mt-0.5"
                />
                <div className="flex-1 text-sm">
                  <p className="font-medium text-foreground">{t(opt.labelKey)}</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">{t(opt.descriptionKey)}</p>
                  {warning ? (
                    <p className="mt-1 text-xs text-status-warn">{t('dirtyWarning')}</p>
                  ) : null}
                </div>
              </label>
            )
          })}
        </fieldset>

        {item.latest_version ? (
          <p className="mt-3 text-xs text-muted-foreground">
            {t('latestVersion')}{' '}
            <span className="font-medium text-foreground">
              v{item.latest_version.version_label}
            </span>
          </p>
        ) : null}
      </DialogShell.Body>

      <DialogShell.Footer>
        <Button variant="outline" onClick={() => onOpenChange(false)}>
          {t('actions.cancel')}
        </Button>
        <Button onClick={handleConfirm} disabled={update.isPending}>
          {update.isPending ? t('actions.updating') : t('actions.confirm')}
        </Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}
