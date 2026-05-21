'use client'

import { useState } from 'react'
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
  label: string
  description: string
  /** True when this strategy is risky in the dirty case. */
  destructiveWhenDirty?: boolean
}

const STRATEGIES: StrategyOption[] = [
  {
    value: 'overwrite',
    label: 'Overwrite my edits',
    description:
      '설치본을 최신 버전으로 덮어쓰고 직접 편집한 내용은 버립니다. dirty 상태에서는 데이터 손실이 발생합니다.',
    destructiveWhenDirty: true,
  },
  {
    value: 'install_new_copy',
    label: 'Install as a new copy',
    description:
      '현재 설치본은 그대로 두고 별도 새 skill row로 최신 버전을 추가 설치합니다. 두 카피를 모두 관리해야 합니다.',
  },
  {
    value: 'keep_current',
    label: 'Keep current install',
    description:
      '업데이트를 적용하지 않습니다. 다음 update available 상태가 다시 표시될 때까지 그대로 유지.',
  },
]

export function UpdateStrategyDialog({
  item,
  open,
  onOpenChange,
}: UpdateStrategyDialogProps) {
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

function UpdateStrategyDialogInner({
  item,
  open,
  onOpenChange,
}: UpdateStrategyDialogProps) {
  // dirty 상태이면 destructive strategy를 1차 선택지로 띄우지 않는다 — 안전한
  // install_new_copy 를 default 로. 사용자가 의식적으로 overwrite 를 선택해야
  // 한다.
  const dirty = !!item?.installation.dirty
  const [strategy, setStrategy] = useState<UpdateStrategy>(
    dirty ? 'install_new_copy' : 'overwrite',
  )
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
          ? `Updated ${item.name}`
          : `Installed new copy of ${item.name}`,
      )
      onOpenChange(false)
    } catch (err) {
      if (err instanceof ApiError) {
        setErrorMessage(
          err.code === 'marketplace_dirty_installation'
            ? 'dirty 상태입니다. overwrite 또는 install_new_copy 중 하나를 명시적으로 선택해야 합니다.'
            : err.code === 'marketplace_item_disabled'
              ? '원본 marketplace 항목이 비활성화되었습니다.'
              : err.message || '업데이트에 실패했습니다.',
        )
      } else {
        setErrorMessage('네트워크 오류가 발생했습니다. 다시 시도하세요.')
      }
    }
  }

  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="md" height="auto">
      <DialogShell.Header
        title={`Update ${item.name}`}
        description={
          dirty
            ? '설치본을 직접 편집한 흔적이 있습니다. 업데이트 전략을 명시적으로 선택해야 합니다.'
            : '최신 마켓플레이스 버전으로 업데이트합니다.'
        }
      />

      <DialogShell.Body>
        {errorMessage ? (
          <div className="mb-3 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
            {errorMessage}
          </div>
        ) : null}

        <fieldset className="space-y-2" aria-label="Update strategy">
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
                  <p className="font-medium text-foreground">{opt.label}</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">{opt.description}</p>
                  {warning ? (
                    <p className="mt-1 text-xs text-status-warn">
                      ⚠ dirty 상태에서 이 옵션은 직접 편집 내용을 영구히 삭제합니다.
                    </p>
                  ) : null}
                </div>
              </label>
            )
          })}
        </fieldset>

        {item.latest_version ? (
          <p className="mt-3 text-xs text-muted-foreground">
            Latest version:{' '}
            <span className="font-medium text-foreground">
              v{item.latest_version.version_label}
            </span>
          </p>
        ) : null}
      </DialogShell.Body>

      <DialogShell.Footer>
        <Button variant="outline" onClick={() => onOpenChange(false)}>
          Cancel
        </Button>
        <Button onClick={handleConfirm} disabled={update.isPending}>
          {update.isPending ? 'Updating…' : 'Confirm'}
        </Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}
