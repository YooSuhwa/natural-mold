import { createStore } from 'jotai'
import { beforeEach, describe, expect, it } from 'vitest'
import {
  chatRightRailWidthAtom,
  clampRightRailWidth,
  RIGHT_RAIL_COLLAPSE_THRESHOLD_PX,
  RIGHT_RAIL_WIDTH_DEFAULT_PX,
  RIGHT_RAIL_WIDTH_MAX_PX,
  RIGHT_RAIL_WIDTH_MIN_PX,
  toggleArtifactListRailState,
  toggleArtifactPreviewRailState,
  type RightRailState,
} from '../chat-right-rail'

function stubInnerWidth(width: number): void {
  Object.defineProperty(window, 'innerWidth', {
    configurable: true,
    value: width,
  })
}

describe('artifact right rail toggles', () => {
  beforeEach(() => {
    window.localStorage.clear()
    stubInnerWidth(1366)
  })

  it('closes the rail when the same artifact preview card is clicked again', () => {
    const current: RightRailState = {
      mode: 'artifacts',
      artifacts: {
        conversationId: 'conversation-1',
        selectedArtifactId: 'report',
        view: 'preview',
      },
    }

    expect(
      toggleArtifactPreviewRailState(current, {
        conversationId: 'conversation-1',
        artifactId: 'report',
      }),
    ).toEqual({ mode: 'none' })
  })

  it('switches preview instead of closing when a different artifact card is clicked', () => {
    const current: RightRailState = {
      mode: 'artifacts',
      artifacts: {
        conversationId: 'conversation-1',
        selectedArtifactId: 'report',
        view: 'preview',
      },
    }

    expect(
      toggleArtifactPreviewRailState(current, {
        conversationId: 'conversation-1',
        artifactId: 'chart',
      }),
    ).toEqual({
      mode: 'artifacts',
      artifacts: {
        conversationId: 'conversation-1',
        selectedArtifactId: 'chart',
        view: 'preview',
      },
    })
  })

  it('opens preview from list mode even when the stored selection is the same artifact', () => {
    const current: RightRailState = {
      mode: 'artifacts',
      artifacts: {
        conversationId: 'conversation-1',
        selectedArtifactId: 'report',
        view: 'list',
      },
    }

    expect(
      toggleArtifactPreviewRailState(current, {
        conversationId: 'conversation-1',
        artifactId: 'report',
      }),
    ).toEqual({
      mode: 'artifacts',
      artifacts: {
        conversationId: 'conversation-1',
        selectedArtifactId: 'report',
        view: 'preview',
      },
    })
  })

  it('closes the rail when the open file list button is clicked again', () => {
    const current: RightRailState = {
      mode: 'artifacts',
      artifacts: {
        conversationId: 'conversation-1',
        view: 'list',
      },
    }

    expect(toggleArtifactListRailState(current, 'conversation-1')).toEqual({ mode: 'none' })
  })

  it('switches from preview to file list when the file list button is clicked', () => {
    const current: RightRailState = {
      mode: 'artifacts',
      artifacts: {
        conversationId: 'conversation-1',
        selectedArtifactId: 'report',
        view: 'preview',
      },
    }

    expect(toggleArtifactListRailState(current, 'conversation-1')).toEqual({
      mode: 'artifacts',
      artifacts: {
        conversationId: 'conversation-1',
        view: 'list',
      },
    })
  })

  it('exposes the right rail width contract', () => {
    expect(RIGHT_RAIL_WIDTH_DEFAULT_PX).toBe(384)
    expect(RIGHT_RAIL_WIDTH_MIN_PX).toBe(320)
    expect(RIGHT_RAIL_WIDTH_MAX_PX).toBe(720)
    expect(RIGHT_RAIL_COLLAPSE_THRESHOLD_PX).toBe(280)
  })

  it('clamps right rail width with explicit viewport sizes', () => {
    expect(clampRightRailWidth(900, 1366)).toBe(720)
    expect(clampRightRailWidth(900, 1024)).toBe(504)
    expect(clampRightRailWidth(900, 1000)).toBe(480)
    expect(clampRightRailWidth(120, 1024)).toBe(320)
  })

  it('uses window.innerWidth only when no viewport is passed', () => {
    stubInnerWidth(1024)
    expect(clampRightRailWidth(900)).toBe(504)

    stubInnerWidth(1000)
    expect(clampRightRailWidth(900)).toBe(480)
  })

  it('persists a clamped right rail width preference', () => {
    const store = createStore()

    expect(store.get(chatRightRailWidthAtom)).toBe(384)
    store.set(chatRightRailWidthAtom, 120)

    expect(store.get(chatRightRailWidthAtom)).toBe(320)
    expect(window.localStorage.getItem('moldy.chatRightRail.widthPx')).toBe('320')
  })
})
