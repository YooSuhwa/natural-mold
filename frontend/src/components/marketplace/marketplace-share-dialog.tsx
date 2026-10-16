'use client'

import { useTranslations } from 'next-intl'

import { useMarketplaceShareBackend } from '@/components/sharing/marketplace-share-backend'
import { ShareDialogContent, ShareDialogFrame } from '@/components/sharing/resource-share-dialog'
import type { MarketplaceItem } from '@/lib/types/marketplace'

interface MarketplaceShareDialogProps {
  item: MarketplaceItem
  open: boolean
  onOpenChange: (open: boolean) => void
}

function MarketplaceShareBody({ item, onClose }: { item: MarketplaceItem; onClose: () => void }) {
  const t = useTranslations('sharing.notice')
  const backend = useMarketplaceShareBackend(item)
  return (
    <ShareDialogContent
      backend={backend}
      resourceName={item.name}
      onClose={onClose}
      notice={
        <p role="note" className="rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
          {t('marketplace')}
        </p>
      }
    />
  )
}

/** Restricted-item recipients (replaces the old ACL list); ACL API until I-04. */
export function MarketplaceShareDialog({ item, open, onOpenChange }: MarketplaceShareDialogProps) {
  return (
    <ShareDialogFrame open={open} onOpenChange={onOpenChange} resourceName={item.name}>
      <MarketplaceShareBody item={item} onClose={() => onOpenChange(false)} />
    </ShareDialogFrame>
  )
}
