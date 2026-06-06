import { describe, expect, it } from 'vitest'
import {
  toggleArtifactListRailState,
  toggleArtifactPreviewRailState,
  type RightRailState,
} from '../chat-right-rail'

describe('artifact right rail toggles', () => {
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
})
