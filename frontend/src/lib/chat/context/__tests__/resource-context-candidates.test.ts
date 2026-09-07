import { describe, expect, it } from 'vitest'

import { buildResourceContextCandidates } from '../resource-context-candidates'

describe('buildResourceContextCandidates', () => {
  it('uses only attached files, ready exact artifact versions, linked skills, and owned conversations', () => {
    const attached = {
      source: 'attached',
      id: crypto.randomUUID(),
      name: 'notes.txt',
      mime_type: 'text/plain',
      preview_url: '/preview',
      download_url: '/download',
      created_at: '2026-09-06T00:00:00Z',
      editable: false,
    } as const
    const generated = { ...attached, source: 'generated', id: crypto.randomUUID() } as const
    const artifact = {
      id: crypto.randomUUID(),
      version_id: crypto.randomUUID(),
      display_name: 'report.md',
      mime_type: 'text/markdown',
      status: 'ready',
    } as const
    const skill = { id: crypto.randomUUID(), name: 'Writer' } as const
    const conversation = {
      id: crypto.randomUUID(),
      title: 'Prior chat',
    } as const

    expect(
      buildResourceContextCandidates({
        files: [attached, generated],
        artifacts: [artifact],
        skills: [skill],
        conversations: [conversation],
      }),
    ).toEqual([
      { kind: 'file', id: attached.id, label: 'notes.txt', description: 'text/plain' },
      {
        kind: 'artifact',
        id: artifact.id,
        version_id: artifact.version_id,
        label: 'report.md',
        description: 'text/markdown',
      },
      { kind: 'skill', id: skill.id, label: 'Writer' },
      { kind: 'conversation', id: conversation.id, label: 'Prior chat' },
    ])
  })
})
