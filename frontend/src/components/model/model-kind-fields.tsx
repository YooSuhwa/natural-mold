'use client'

import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger } from '@/components/ui/select'
import {
  DEFAULT_EMBEDDING_DIMENSIONS,
  MAX_EMBEDDING_DIMENSIONS,
  type ModelKind,
} from '@/lib/types/model'

const KINDS: readonly ModelKind[] = ['chat', 'embedding']

export const DEFAULT_DIMENSIONS_INPUT = String(DEFAULT_EMBEDDING_DIMENSIONS)

/** A whole number in 1–4096, or `null` when the field does not hold one. */
export function parseEmbeddingDimensions(value: string): number | null {
  const trimmed = value.trim()
  if (!/^\d+$/.test(trimmed)) return null
  const parsed = Number(trimmed)
  return parsed >= 1 && parsed <= MAX_EMBEDDING_DIMENSIONS ? parsed : null
}

/** The `kind` + `embedding_dimensions` pair the model APIs expect (Q14). */
export function modelKindPayload(
  kind: ModelKind,
  dimensions: string,
): { kind: ModelKind; embedding_dimensions: number | null } {
  return {
    kind,
    embedding_dimensions: kind === 'embedding' ? parseEmbeddingDimensions(dimensions) : null,
  }
}

/** Whether the kind fields can be submitted: an embedding model needs valid dimensions. */
export function modelKindValid(kind: ModelKind, dimensions: string): boolean {
  return kind === 'chat' || parseEmbeddingDimensions(dimensions) !== null
}

/**
 * Memory plan Q14: the model kind selector (대화 모델 / 임베딩 모델) and, for an embedding
 * model, its required vector width. Shared by the operator and company model forms.
 */
export function ModelKindFields({
  idPrefix,
  kind,
  dimensions,
  onKindChange,
  onDimensionsChange,
  disabled,
}: {
  idPrefix: string
  kind: ModelKind
  dimensions: string
  onKindChange: (kind: ModelKind) => void
  onDimensionsChange: (value: string) => void
  disabled?: boolean
}) {
  const t = useTranslations('model.kind')
  const invalid = kind === 'embedding' && parseEmbeddingDimensions(dimensions) === null
  const kindId = `${idPrefix}-kind`
  const dimensionsId = `${idPrefix}-dimensions`
  return (
    <div className="grid grid-cols-2 gap-3">
      <div className="space-y-1.5">
        <label htmlFor={kindId} className="text-xs font-medium">
          {t('field')}
        </label>
        <Select
          value={kind}
          onValueChange={(next) => {
            if (next === 'chat' || next === 'embedding') onKindChange(next)
          }}
          disabled={disabled}
        >
          <SelectTrigger id={kindId} className="w-full" data-testid={`${kindId}-trigger`}>
            <span className="truncate">{t(kind)}</span>
          </SelectTrigger>
          <SelectContent>
            {KINDS.map((value) => (
              <SelectItem key={value} value={value}>
                {t(value)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {kind === 'embedding' ? (
        <div className="space-y-1.5">
          <label htmlFor={dimensionsId} className="text-xs font-medium">
            {t('dimensions')}
            <span className="ml-0.5 text-destructive">*</span>
          </label>
          <Input
            id={dimensionsId}
            type="number"
            inputMode="numeric"
            min={1}
            max={MAX_EMBEDDING_DIMENSIONS}
            step={1}
            required
            value={dimensions}
            disabled={disabled}
            aria-invalid={invalid}
            aria-describedby={`${dimensionsId}-hint`}
            onChange={(e) => onDimensionsChange(e.target.value)}
          />
          <p
            id={`${dimensionsId}-hint`}
            className={`moldy-ui-caption ${invalid ? 'text-destructive' : 'text-muted-foreground'}`}
          >
            {invalid
              ? t('dimensionsInvalid', { max: MAX_EMBEDDING_DIMENSIONS })
              : t('dimensionsHint')}
          </p>
        </div>
      ) : null}
    </div>
  )
}

/** List badge: "대화" or "임베딩 · 1024차원". */
export function ModelKindBadge({
  kind,
  dimensions,
}: {
  kind: ModelKind
  dimensions: number | null
}) {
  const t = useTranslations('model.kind')
  return (
    <Badge variant={kind === 'embedding' ? 'secondary' : 'outline'} data-testid="model-kind-badge">
      {kind === 'embedding' && dimensions !== null
        ? t('badgeEmbedding', { count: dimensions })
        : t(`badge.${kind}`)}
    </Badge>
  )
}
