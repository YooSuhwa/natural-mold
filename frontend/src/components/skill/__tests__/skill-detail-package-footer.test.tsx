import { describe, expect, it, vi } from 'vitest'

import { render, screen } from '../../../../tests/test-utils'
import { SkillDetailPackageFooter } from '../skill-detail-package-footer'

function renderFooter() {
  return render(
    <SkillDetailPackageFooter
      confirmingDelete={false}
      deletePending={false}
      savePending={false}
      saveDisabled={false}
      exportHref="http://localhost:8001/api/skills/skill-1/export"
      sizeBytes={100}
      version="1.0.0"
      usedByCount={0}
      onAskDelete={vi.fn()}
      onCancelDelete={vi.fn()}
      onConfirmDelete={vi.fn()}
      onClose={vi.fn()}
      onSave={vi.fn()}
    />,
  )
}

describe('SkillDetailPackageFooter', () => {
  it('renders a downloadable portable skill export action', () => {
    renderFooter()

    const exportAction = screen.getByRole('button', { name: '.skill 내보내기' })
    expect(exportAction).toHaveAttribute('href', 'http://localhost:8001/api/skills/skill-1/export')
    expect(exportAction).toHaveAttribute('download')
  })
})
