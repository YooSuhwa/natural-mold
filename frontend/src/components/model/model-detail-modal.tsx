'use client'

import { EyeIcon, WrenchIcon, BrainIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog'
import { Badge } from '@/components/ui/badge'
import { getProviderIcon, formatContextWindow } from '@/lib/utils/provider'
import type { Model } from '@/lib/types'

interface ModelDetailModalProps {
  model: Model
  open: boolean
  onClose: () => void
}

function formatCost(cost: number | null): string {
  if (cost == null) return '\u2014'
  return `$${(cost * 1_000_000).toFixed(2)}`
}

export function ModelDetailModal({ model, open, onClose }: ModelDetailModalProps) {
  const t = useTranslations('model')

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('modelDetail')}</DialogTitle>
          <DialogDescription className="sr-only">{model.display_name}</DialogDescription>
        </DialogHeader>

        <div className="space-y-5">
          {/* Section 1: Overview */}
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-lg bg-muted text-xs font-bold text-muted-foreground">
              {getProviderIcon(model.provider)}
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-base font-semibold">{model.display_name}</span>
                <Badge variant="outline">{model.provider}</Badge>
              </div>
              <p className="text-xs text-muted-foreground">{model.model_name}</p>
            </div>
          </div>

          {/* Section 2: Token & Pricing */}
          <div className="space-y-2">
            <h4 className="text-sm font-medium text-muted-foreground">Token & Pricing</h4>
            <div className="grid grid-cols-2 gap-3">
              <InfoItem
                label={t('maxInputTokens')}
                value={formatContextWindow(model.context_window) ?? '\u2014'}
              />
              <InfoItem
                label={t('maxOutputTokens')}
                value={formatContextWindow(model.max_output_tokens) ?? '\u2014'}
              />
              <InfoItem
                label={t('inputCost')}
                value={
                  model.cost_per_input_token != null
                    ? `${formatCost(model.cost_per_input_token)} ${t('perMillionTokens')}`
                    : '\u2014'
                }
              />
              <InfoItem
                label={t('outputCost')}
                value={
                  model.cost_per_output_token != null
                    ? `${formatCost(model.cost_per_output_token)} ${t('perMillionTokens')}`
                    : '\u2014'
                }
              />
            </div>
          </div>

          {/* Section 3: Capabilities */}
          {(model.supports_vision ||
            model.supports_function_calling ||
            model.supports_reasoning) && (
            <div className="space-y-2">
              <h4 className="text-sm font-medium text-muted-foreground">{t('capabilities')}</h4>
              <div className="flex flex-wrap gap-2">
                {model.supports_vision && (
                  <Badge className="border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-300">
                    <EyeIcon className="mr-1 size-3" />
                    {t('vision')}
                  </Badge>
                )}
                {model.supports_function_calling && (
                  <Badge className="border-green-200 bg-green-50 text-green-700 dark:border-green-800 dark:bg-green-950 dark:text-green-300">
                    <WrenchIcon className="mr-1 size-3" />
                    {t('functionCalling')}
                  </Badge>
                )}
                {model.supports_reasoning && (
                  <Badge className="border-purple-200 bg-purple-50 text-purple-700 dark:border-purple-800 dark:bg-purple-950 dark:text-purple-300">
                    <BrainIcon className="mr-1 size-3" />
                    {t('reasoning')}
                  </Badge>
                )}
              </div>
            </div>
          )}

          {/* Section 4: Modalities */}
          {(model.input_modalities?.length || model.output_modalities?.length) && (
            <div className="space-y-2">
              <h4 className="text-sm font-medium text-muted-foreground">{t('modalities')}</h4>
              <div className="space-y-1.5">
                {model.input_modalities?.length ? (
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground w-10">
                      {t('inputModalities')}
                    </span>
                    <div className="flex flex-wrap gap-1">
                      {model.input_modalities.map((m) => (
                        <Badge key={m} variant="secondary" className="text-[10px]">
                          {m}
                        </Badge>
                      ))}
                    </div>
                  </div>
                ) : null}
                {model.output_modalities?.length ? (
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground w-10">
                      {t('outputModalities')}
                    </span>
                    <div className="flex flex-wrap gap-1">
                      {model.output_modalities.map((m) => (
                        <Badge key={m} variant="secondary" className="text-[10px]">
                          {m}
                        </Badge>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

function InfoItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-muted/50 px-3 py-2">
      <p className="text-[11px] text-muted-foreground">{label}</p>
      <p className="text-sm font-medium">{value}</p>
    </div>
  )
}
