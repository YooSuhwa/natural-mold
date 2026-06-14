import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEventHandler,
  type PointerEventHandler,
} from 'react'
import {
  clampStableWidth,
  nextRawWidth,
  previewWidth,
  shouldExpandFromCollapsedKey,
  type HorizontalResizeSide,
} from './horizontal-panel-resize-core'
import {
  cancelPreviewFrame,
  requestPreviewFrame,
  setDocumentResizeFlag,
} from './horizontal-panel-resize-dom'
import { DRAG_START_THRESHOLD_PX } from '@/lib/stores/panel-width'

export type { HorizontalResizeSide } from './horizontal-panel-resize-core'

export interface HorizontalPanelResizeOptions {
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
}

export interface HorizontalResizeHandleProps {
  onKeyDown: KeyboardEventHandler<HTMLElement>
  onPointerCancel: PointerEventHandler<HTMLElement>
  onPointerDown: PointerEventHandler<HTMLElement>
  onPointerMove: PointerEventHandler<HTMLElement>
  onPointerUp: PointerEventHandler<HTMLElement>
}

export interface HorizontalPanelResizeResult {
  isCollapsePreview: boolean
  isDragging: boolean
  previewWidth: number | null
  handleProps: HorizontalResizeHandleProps
}

interface ResizeSession {
  expandedFromCollapsed: boolean
  frameId: number | null
  hasDragged: boolean
  latestClientX: number
  pointerId: number
  startWidth: number
  startX: number
}

export function useHorizontalPanelResize({
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
}: HorizontalPanelResizeOptions): HorizontalPanelResizeResult {
  const sessionRef = useRef<ResizeSession | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [preview, setPreview] = useState<number | null>(null)
  const [isCollapsePreview, setIsCollapsePreview] = useState(false)

  const clearSession = useCallback(() => {
    const session = sessionRef.current
    cancelPreviewFrame(session?.frameId ?? null)
    sessionRef.current = null
    setIsDragging(false)
    setPreview(null)
    setIsCollapsePreview(false)
    setDocumentResizeFlag(false)
  }, [])

  const schedulePreview = useCallback(() => {
    const session = sessionRef.current
    if (!session || session.frameId !== null) return

    session.frameId = requestPreviewFrame(() => {
      const activeSession = sessionRef.current
      if (!activeSession) return

      activeSession.frameId = null
      const rawWidth = nextRawWidth(
        side,
        activeSession.startWidth,
        activeSession.startX,
        activeSession.latestClientX,
      )
      const nextPreviewWidth = previewWidth(rawWidth, minWidth, maxWidth)
      const nextCollapsePreview = rawWidth < collapseThreshold
      const wasCollapsed = activeSession.startWidth < collapseThreshold

      if (wasCollapsed && rawWidth >= collapseThreshold && !activeSession.expandedFromCollapsed) {
        activeSession.expandedFromCollapsed = true
        onPreviewExpandFromCollapsed?.(clampStableWidth(rawWidth, minWidth, maxWidth))
      }

      setPreview(nextPreviewWidth)
      setIsCollapsePreview(nextCollapsePreview)
      onPreviewWidth(nextPreviewWidth)
    })
  }, [collapseThreshold, maxWidth, minWidth, onPreviewExpandFromCollapsed, onPreviewWidth, side])

  const onPointerDown = useCallback<PointerEventHandler<HTMLElement>>(
    (event) => {
      if (event.button !== 0) return

      event.currentTarget.setPointerCapture?.(event.pointerId)
      sessionRef.current = {
        expandedFromCollapsed: false,
        frameId: null,
        hasDragged: false,
        latestClientX: event.clientX,
        pointerId: event.pointerId,
        startWidth: width,
        startX: event.clientX,
      }
    },
    [width],
  )

  const onPointerMove = useCallback<PointerEventHandler<HTMLElement>>(
    (event) => {
      const session = sessionRef.current
      if (!session || session.pointerId !== event.pointerId) return

      const movement = Math.abs(event.clientX - session.startX)
      if (!session.hasDragged && movement < DRAG_START_THRESHOLD_PX) return

      if (!session.hasDragged) {
        session.hasDragged = true
        setIsDragging(true)
        setDocumentResizeFlag(true)
      }

      session.latestClientX = event.clientX
      schedulePreview()
    },
    [schedulePreview],
  )

  const finishPointerResize = useCallback(
    (clientX: number, pointerId: number, element: HTMLElement) => {
      const session = sessionRef.current
      if (!session || session.pointerId !== pointerId) return

      if (element.hasPointerCapture?.(pointerId)) {
        element.releasePointerCapture?.(pointerId)
      }

      if (!session.hasDragged) {
        sessionRef.current = null
        return
      }

      const startedCollapsed = session.startWidth < collapseThreshold
      const rawWidth = nextRawWidth(side, session.startWidth, session.startX, clientX)
      clearSession()

      if (rawWidth < collapseThreshold) {
        onCollapse()
        return
      }

      const nextStableWidth = clampStableWidth(rawWidth, minWidth, maxWidth)
      if (startedCollapsed && onExpandFromCollapsed) {
        onExpandFromCollapsed(nextStableWidth)
        return
      }

      onCommitWidth(nextStableWidth)
    },
    [
      clearSession,
      collapseThreshold,
      maxWidth,
      minWidth,
      onCollapse,
      onCommitWidth,
      onExpandFromCollapsed,
      side,
    ],
  )

  const onPointerUp = useCallback<PointerEventHandler<HTMLElement>>(
    (event) => {
      finishPointerResize(event.clientX, event.pointerId, event.currentTarget)
    },
    [finishPointerResize],
  )

  const onPointerCancel = useCallback<PointerEventHandler<HTMLElement>>(
    (event) => {
      const session = sessionRef.current
      const shouldResetOwnerPreview = session?.hasDragged === true
      const shouldRollbackCollapsedExpansion = session?.expandedFromCollapsed === true
      if (session && event.currentTarget.hasPointerCapture?.(session.pointerId)) {
        event.currentTarget.releasePointerCapture?.(session.pointerId)
      }
      clearSession()
      if (shouldResetOwnerPreview) {
        onCancelPreview?.()
      }
      if (shouldRollbackCollapsedExpansion) {
        onCollapse()
      }
    },
    [clearSession, onCancelPreview, onCollapse],
  )

  const onKeyDown = useCallback<KeyboardEventHandler<HTMLElement>>(
    (event) => {
      const step = event.shiftKey ? 48 : 16
      const collapsed = width < collapseThreshold
      let nextWidth: number | null = null

      if (event.key === 'Enter') {
        event.preventDefault()
        if (collapsed) {
          onExpandFromCollapsed?.(minWidth)
        } else {
          onCollapse()
        }
        return
      }

      if (event.key === 'Home') nextWidth = minWidth
      if (event.key === 'End') nextWidth = maxWidth
      if (event.key === 'ArrowLeft') nextWidth = width + (side === 'right' ? step : -step)
      if (event.key === 'ArrowRight') nextWidth = width + (side === 'left' ? step : -step)

      if (nextWidth === null) return

      event.preventDefault()
      const nextStableWidth = clampStableWidth(nextWidth, minWidth, maxWidth)
      if (collapsed) {
        if (shouldExpandFromCollapsedKey(event.key, side)) {
          onExpandFromCollapsed?.(nextStableWidth)
        }
        return
      }

      onCommitWidth(nextStableWidth)
    },
    [
      collapseThreshold,
      maxWidth,
      minWidth,
      onCollapse,
      onCommitWidth,
      onExpandFromCollapsed,
      side,
      width,
    ],
  )

  useEffect(() => clearSession, [clearSession])

  const handleProps = useMemo<HorizontalResizeHandleProps>(
    () => ({
      onKeyDown,
      onPointerCancel,
      onPointerDown,
      onPointerMove,
      onPointerUp,
    }),
    [onKeyDown, onPointerCancel, onPointerDown, onPointerMove, onPointerUp],
  )

  return {
    isCollapsePreview,
    isDragging,
    previewWidth: preview,
    handleProps,
  }
}
