'use client'

import { useTranslations } from 'next-intl'
import { Zap } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'

interface Props {
  onlyWithRanking: boolean
  onOnlyWithRankingChange: (value: boolean) => void
  showOperatorControls: boolean
  showHidden: boolean
  onShowHiddenChange: (value: boolean) => void
  selectedCount: number
  onTestSelected: () => void
}

/** Catalog toolbar: ranking filter, operator-only hidden toggle, bulk test. */
export function ModelCatalogToolbar({
  onlyWithRanking,
  onOnlyWithRankingChange,
  showOperatorControls,
  showHidden,
  onShowHiddenChange,
  selectedCount,
  onTestSelected,
}: Props) {
  const t = useTranslations('model')
  return (
    <div className="flex items-center gap-3">
      <label
        htmlFor="only-with-ranking"
        className="inline-flex cursor-pointer items-center gap-2 text-xs text-muted-foreground"
      >
        <Checkbox
          id="only-with-ranking"
          data-testid="only-with-ranking"
          checked={onlyWithRanking}
          onCheckedChange={(v) => onOnlyWithRankingChange(Boolean(v))}
        />
        {t('catalog.filters.hasBenchmark')}
      </label>
      {showOperatorControls && (
        <label
          htmlFor="show-hidden"
          className="inline-flex cursor-pointer items-center gap-2 text-xs text-muted-foreground"
        >
          <Checkbox
            id="show-hidden"
            data-testid="show-hidden"
            checked={showHidden}
            onCheckedChange={(v) => onShowHiddenChange(Boolean(v))}
          />
          {t('catalog.filters.showHidden')}
        </label>
      )}
      {selectedCount > 0 && (
        <Button size="sm" onClick={onTestSelected} data-testid="test-selected">
          <Zap className="size-3.5" />
          {t('catalog.actions.testSelected')}
          <Badge variant="secondary" className="ml-1">
            {selectedCount}
          </Badge>
        </Button>
      )}
    </div>
  )
}
