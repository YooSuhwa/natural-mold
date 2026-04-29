'use client'

import { useTranslations } from 'next-intl'
import { useMiddlewares } from '@/lib/hooks/use-middlewares'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogClose,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Skeleton } from '@/components/ui/skeleton'

interface AddMiddlewareModalProps {
  open: boolean
  onOpenChange: (v: boolean) => void
  selectedMiddlewareTypes: Set<string>
  onToggleMiddleware: (type: string) => void
}

export function AddMiddlewareModal({
  open,
  onOpenChange,
  selectedMiddlewareTypes,
  onToggleMiddleware,
}: AddMiddlewareModalProps) {
  const t = useTranslations('agent.settings')
  const tc = useTranslations('common')
  const { data: middlewares } = useMiddlewares()

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('addMiddlewareDialogTitle')}</DialogTitle>
        </DialogHeader>

        <div className="max-h-96 overflow-y-auto py-2">
          {middlewares ? (
            middlewares.length > 0 ? (
              <div className="space-y-2 rounded-lg border p-3">
                {middlewares.map((mw) => (
                  <label
                    key={mw.type}
                    className="flex cursor-pointer items-center gap-3 text-sm"
                  >
                    <Checkbox
                      checked={selectedMiddlewareTypes.has(mw.type)}
                      onCheckedChange={() => onToggleMiddleware(mw.type)}
                    />
                    <span className="font-medium">{mw.display_name}</span>
                    {mw.description && (
                      <span className="truncate text-xs text-muted-foreground">
                        - {mw.description}
                      </span>
                    )}
                  </label>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">{t('noMiddlewares')}</p>
            )
          ) : (
            <Skeleton className="h-24 w-full" />
          )}
        </div>

        <DialogFooter>
          <DialogClose render={<Button>{tc('done')}</Button>} />
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
