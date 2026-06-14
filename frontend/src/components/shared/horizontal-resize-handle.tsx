'use client'

import type { ComponentProps } from 'react'
import {
  useHorizontalPanelResize,
  type HorizontalResizeSide,
} from '@/hooks/use-horizontal-panel-resize'
import { cn } from '@/lib/utils'

interface HorizontalResizeHandleProps extends Omit<
  ComponentProps<'div'>,
  | 'aria-label'
  | 'children'
  | 'onDoubleClick'
  | 'onKeyDown'
  | 'onPointerCancel'
  | 'onPointerDown'
  | 'onPointerMove'
  | 'onPointerUp'
  | 'onReset'
  | 'role'
  | 'tabIndex'
> {
  ariaLabel: string
  side: HorizontalResizeSide
  width: number
  minWidth: number
  maxWidth: number
  collapseThreshold: number
  onPreviewWidth: (width: number) => void
  onCommitWidth: (width: number) => void
  onCollapse: () => void
  onCancelPreview?: () => void
  onExpandFromCollapsed?: (width: number) => void
  onPreviewExpandFromCollapsed?: (width: number) => void
  collapsedValueText?: string
  className?: string
  variantClassName?: string
  onReset?: () => void
}

export function HorizontalResizeHandle({
  ariaLabel,
  side,
  width,
  minWidth,
  maxWidth,
  collapseThreshold,
  onPreviewWidth,
  onCommitWidth,
  onCollapse,
  onCancelPreview,
  onExpandFromCollapsed,
  onPreviewExpandFromCollapsed,
  collapsedValueText,
  className,
  variantClassName,
  onReset,
  ...props
}: HorizontalResizeHandleProps) {
  const resize = useHorizontalPanelResize({
    side,
    width,
    minWidth,
    maxWidth,
    collapseThreshold,
    onPreviewWidth,
    onCommitWidth,
    onCollapse,
    onCancelPreview,
    onExpandFromCollapsed,
    onPreviewExpandFromCollapsed,
  })
  const currentWidth = Math.round(resize.previewWidth ?? width)
  const ariaValueMin = Math.min(minWidth, currentWidth)
  const ariaValueMax = Math.max(maxWidth, currentWidth)
  const isCollapsedValue = currentWidth < collapseThreshold

  return (
    <div
      {...props}
      aria-label={ariaLabel}
      aria-orientation="vertical"
      aria-valuemax={ariaValueMax}
      aria-valuemin={ariaValueMin}
      aria-valuenow={currentWidth}
      aria-valuetext={isCollapsedValue ? collapsedValueText : undefined}
      className={cn('moldy-resize-handle', variantClassName, className)}
      data-collapse-preview={resize.isCollapsePreview ? 'true' : undefined}
      data-resizing={resize.isDragging ? 'true' : undefined}
      data-side={side}
      onDoubleClick={onReset}
      role="separator"
      tabIndex={0}
      {...resize.handleProps}
    />
  )
}
