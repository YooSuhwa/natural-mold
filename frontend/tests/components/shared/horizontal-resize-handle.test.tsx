import { fireEvent, render, screen } from '@testing-library/react'
import { HorizontalResizeHandle } from '@/components/shared/horizontal-resize-handle'
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

describe('HorizontalResizeHandle', () => {
  beforeEach(() => {
    installAnimationFrameStub()
    document.documentElement.removeAttribute('data-panel-resizing')
  })

  it('renders the shared accessible separator surface', () => {
    render(
      <HorizontalResizeHandle
        ariaLabel="Resize panel"
        className="base-class"
        collapseThreshold={180}
        maxWidth={420}
        minWidth={224}
        onCollapse={() => {}}
        onCommitWidth={() => {}}
        onPreviewWidth={() => {}}
        side="left"
        variantClassName="variant-class"
        width={256}
      />,
    )

    const handle = screen.getByRole('separator', { name: 'Resize panel' })
    expect(handle).toHaveAttribute('aria-orientation', 'vertical')
    expect(handle).toHaveAttribute('aria-valuemin', '224')
    expect(handle).toHaveAttribute('aria-valuemax', '420')
    expect(handle).toHaveAttribute('aria-valuenow', '256')
    expect(handle).toHaveAttribute('tabindex', '0')
    expect(handle).toHaveClass('base-class')
    expect(handle).toHaveClass('variant-class')
  })

  it('owns reset and drag data attributes for both panel integrations', () => {
    const onPreviewWidth = vi.fn()
    const onReset = vi.fn()
    render(
      <HorizontalResizeHandle
        ariaLabel="Resize panel"
        collapseThreshold={180}
        maxWidth={420}
        minWidth={224}
        onCollapse={() => {}}
        onCommitWidth={() => {}}
        onPreviewWidth={onPreviewWidth}
        onReset={onReset}
        side="right"
        width={384}
      />,
    )

    const handle = screen.getByRole('separator', { name: 'Resize panel' })
    fireEvent.doubleClick(handle)
    expect(onReset).toHaveBeenCalledTimes(1)

    fireEvent.pointerDown(handle, { clientX: 400, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 620, pointerId: 1 })

    expect(onPreviewWidth).toHaveBeenLastCalledWith(164)
    expect(handle).toHaveAttribute('data-resizing', 'true')
    expect(handle).toHaveAttribute('data-collapse-preview', 'true')
  })

  it('announces a collapsed value with explicit text', () => {
    render(
      <HorizontalResizeHandle
        ariaLabel="Resize panel"
        collapsedValueText="Collapsed"
        collapseThreshold={180}
        maxWidth={420}
        minWidth={224}
        onCollapse={() => {}}
        onCommitWidth={() => {}}
        onPreviewWidth={() => {}}
        side="left"
        width={0}
      />,
    )

    const handle = screen.getByRole('separator', { name: 'Resize panel' })
    expect(handle).toHaveAttribute('aria-valuenow', '0')
    expect(handle).toHaveAttribute('aria-valuetext', 'Collapsed')
  })
})
