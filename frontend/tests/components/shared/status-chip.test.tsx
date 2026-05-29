import { render, screen } from '../../test-utils'
import { StatusChip } from '@/components/shared/status-chip'

describe('StatusChip', () => {
  it('uses Korean default labels', () => {
    render(
      <>
        <StatusChip variant="active" />
        <StatusChip variant="auth_needed" />
        <StatusChip variant="unreachable" />
      </>,
    )

    expect(screen.getByText('활성')).toBeInTheDocument()
    expect(screen.getByText('인증 필요')).toBeInTheDocument()
    expect(screen.getByText('연결 불가')).toBeInTheDocument()
  })
})
