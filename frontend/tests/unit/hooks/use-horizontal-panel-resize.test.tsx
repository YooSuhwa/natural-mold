import { fireEvent, render, screen } from '@testing-library/react'
import { useHorizontalPanelResize } from '@/hooks/use-horizontal-panel-resize'
import { beforeEach, describe, expect, it, vi } from 'vitest'

function installAnimationFrameStub(): void {
  Object.defineProperty(window, 'requestAnimationFrame', {
    configurable: true,
    value: (callback: FrameRequestCallback) => {
      callback(0)
      return 1
    },
  })
  Object.defineProperty(window, 'cancelAnimationFrame', {
    configurable: true,
    value: () => {},
  })
}

interface ResizeProbeProps {
  side: 'left' | 'right'
  width?: number
  onPreviewWidth?: (width: number) => void
  onCommitWidth?: (width: number) => void
  onCollapse?: () => void
  onExpandFromCollapsed?: (width: number) => void
}

function ResizeProbe({
  side,
  width = 256,
  onPreviewWidth = () => {},
  onCommitWidth = () => {},
  onCollapse = () => {},
  onExpandFromCollapsed,
}: ResizeProbeProps) {
  const resize = useHorizontalPanelResize({
    side,
    width,
    minWidth: 224,
    maxWidth: 420,
    collapseThreshold: 180,
    onPreviewWidth,
    onCommitWidth,
    onCollapse,
    onExpandFromCollapsed,
  })

  return (
    <div
      data-collapse-preview={resize.isCollapsePreview ? 'true' : 'false'}
      data-dragging={resize.isDragging ? 'true' : 'false'}
      data-preview-width={resize.previewWidth ?? ''}
      data-testid="resize-handle"
      role="separator"
      {...resize.handleProps}
    />
  )
}

describe('useHorizontalPanelResize', () => {
  beforeEach(() => {
    installAnimationFrameStub()
    document.documentElement.removeAttribute('data-panel-resizing')
  })

  it('resizes a left-side panel with positive horizontal movement', () => {
    const onPreviewWidth = vi.fn()
    const onCommitWidth = vi.fn()
    render(
      <ResizeProbe
        side="left"
        width={256}
        onPreviewWidth={onPreviewWidth}
        onCommitWidth={onCommitWidth}
      />,
    )

    const handle = screen.getByTestId('resize-handle')
    fireEvent.pointerDown(handle, { clientX: 100, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 180, pointerId: 1 })

    expect(onPreviewWidth).toHaveBeenLastCalledWith(336)
    expect(handle).toHaveAttribute('data-dragging', 'true')
    expect(document.documentElement).toHaveAttribute('data-panel-resizing', 'true')

    fireEvent.pointerUp(handle, { clientX: 180, pointerId: 1 })

    expect(onCommitWidth).toHaveBeenLastCalledWith(336)
    expect(document.documentElement).not.toHaveAttribute('data-panel-resizing')
  })

  it('resizes a right-side panel when the left edge moves left', () => {
    const onPreviewWidth = vi.fn()
    const onCommitWidth = vi.fn()
    render(
      <ResizeProbe
        side="right"
        width={384}
        onPreviewWidth={onPreviewWidth}
        onCommitWidth={onCommitWidth}
      />,
    )

    const handle = screen.getByTestId('resize-handle')
    fireEvent.pointerDown(handle, { clientX: 400, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 240, pointerId: 1 })
    fireEvent.pointerUp(handle, { clientX: 240, pointerId: 1 })

    expect(onPreviewWidth).toHaveBeenLastCalledWith(420)
    expect(onCommitWidth).toHaveBeenLastCalledWith(420)
  })

  it('treats tiny pointer movement as a click instead of a resize', () => {
    const onPreviewWidth = vi.fn()
    const onCommitWidth = vi.fn()
    const onCollapse = vi.fn()
    render(
      <ResizeProbe
        side="left"
        onPreviewWidth={onPreviewWidth}
        onCommitWidth={onCommitWidth}
        onCollapse={onCollapse}
      />,
    )

    const handle = screen.getByTestId('resize-handle')
    fireEvent.pointerDown(handle, { clientX: 100, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 103, pointerId: 1 })
    fireEvent.pointerUp(handle, { clientX: 103, pointerId: 1 })

    expect(onPreviewWidth).not.toHaveBeenCalled()
    expect(onCommitWidth).not.toHaveBeenCalled()
    expect(onCollapse).not.toHaveBeenCalled()
  })

  it('previews and commits collapse below the threshold without storing the preview width', () => {
    const onPreviewWidth = vi.fn()
    const onCommitWidth = vi.fn()
    const onCollapse = vi.fn()
    render(
      <ResizeProbe
        side="right"
        width={384}
        onPreviewWidth={onPreviewWidth}
        onCommitWidth={onCommitWidth}
        onCollapse={onCollapse}
      />,
    )

    const handle = screen.getByTestId('resize-handle')
    fireEvent.pointerDown(handle, { clientX: 400, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 620, pointerId: 1 })

    expect(onPreviewWidth).toHaveBeenLastCalledWith(164)
    expect(handle).toHaveAttribute('data-collapse-preview', 'true')

    fireEvent.pointerUp(handle, { clientX: 620, pointerId: 1 })

    expect(onCollapse).toHaveBeenCalledTimes(1)
    expect(onCommitWidth).not.toHaveBeenCalled()
  })

  it('supports keyboard resize and collapsed expansion', () => {
    const onCommitWidth = vi.fn()
    const onCollapse = vi.fn()
    const onExpandFromCollapsed = vi.fn()
    const { rerender } = render(
      <ResizeProbe
        side="left"
        width={256}
        onCommitWidth={onCommitWidth}
        onCollapse={onCollapse}
        onExpandFromCollapsed={onExpandFromCollapsed}
      />,
    )

    const handle = screen.getByTestId('resize-handle')
    fireEvent.keyDown(handle, { key: 'ArrowRight' })
    fireEvent.keyDown(handle, { key: 'Home' })
    fireEvent.keyDown(handle, { key: 'Enter' })

    expect(onCommitWidth).toHaveBeenNthCalledWith(1, 272)
    expect(onCommitWidth).toHaveBeenNthCalledWith(2, 224)
    expect(onCollapse).toHaveBeenCalledTimes(1)

    rerender(
      <ResizeProbe
        side="left"
        width={0}
        onCommitWidth={onCommitWidth}
        onCollapse={onCollapse}
        onExpandFromCollapsed={onExpandFromCollapsed}
      />,
    )
    fireEvent.keyDown(handle, { key: 'Enter' })

    expect(onExpandFromCollapsed).toHaveBeenLastCalledWith(224)
  })

  it('expands a collapsed panel with keyboard growth arrows', () => {
    const onCommitWidth = vi.fn()
    const onExpandFromCollapsed = vi.fn()
    const { rerender } = render(
      <ResizeProbe
        side="left"
        width={0}
        onCommitWidth={onCommitWidth}
        onExpandFromCollapsed={onExpandFromCollapsed}
      />,
    )

    const handle = screen.getByTestId('resize-handle')
    fireEvent.keyDown(handle, { key: 'ArrowRight' })

    expect(onExpandFromCollapsed).toHaveBeenLastCalledWith(224)
    expect(onCommitWidth).not.toHaveBeenCalled()

    rerender(
      <ResizeProbe
        side="right"
        width={0}
        onCommitWidth={onCommitWidth}
        onExpandFromCollapsed={onExpandFromCollapsed}
      />,
    )
    fireEvent.keyDown(handle, { key: 'ArrowLeft' })

    expect(onExpandFromCollapsed).toHaveBeenLastCalledWith(224)
    expect(onCommitWidth).not.toHaveBeenCalled()
  })
})
