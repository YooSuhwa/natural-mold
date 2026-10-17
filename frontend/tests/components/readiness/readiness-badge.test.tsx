import { ReadinessBadge } from '@/components/readiness/readiness-badge'
import { render, screen } from '../../test-utils'

describe('ReadinessBadge (S21 badge rules)', () => {
  it('shows nothing for a ready agent or an omitted one', () => {
    const { container } = render(
      <>
        <ReadinessBadge summary={{ overall: 'ready', counts: { ready: 3 } }} />
        <ReadinessBadge summary={null} />
      </>,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('counts my connections, excluded items and names the blocking reason', () => {
    render(
      <>
        <ReadinessBadge
          summary={{ overall: 'needs_connection', counts: { ready: 2, connect_by_me: 1 } }}
        />
        <ReadinessBadge summary={{ overall: 'limited', counts: { ready: 1, no_permission: 2 } }} />
        <ReadinessBadge summary={{ overall: 'blocked', counts: { owner_must_connect: 1 } }} />
      </>,
    )
    expect(screen.getByText('연결 필요 1')).toBeInTheDocument()
    expect(screen.getByText('2개 제외')).toBeInTheDocument()
    expect(screen.getByText('사용 불가 · 소유자 연결 필요')).toBeInTheDocument()
  })
})
