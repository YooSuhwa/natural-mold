import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ResourceContextChips } from '../resource-context-chips'

describe('ResourceContextChips', () => {
  it('shows typed provenance and removes the exact selected version', () => {
    const onRemove = vi.fn()
    const reference = {
      kind: 'artifact' as const,
      id: crypto.randomUUID(),
      version_id: crypto.randomUUID(),
      label: 'Quarterly report',
    }
    render(
      <ResourceContextChips
        references={[reference]}
        labels={{
          artifact: 'Artifact',
          conversation: 'Conversation',
          file: 'File',
          skill: 'Skill',
        }}
        removeLabel={(label) => `Remove ${label}`}
        onRemove={onRemove}
      />,
    )

    expect(screen.getByText('Quarterly report')).toBeVisible()
    expect(screen.getByText('Artifact')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Remove Quarterly report' }))
    expect(onRemove).toHaveBeenCalledWith(reference)
  })
})
