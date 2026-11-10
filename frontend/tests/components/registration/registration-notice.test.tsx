import { http, HttpResponse } from 'msw'

import { RegistrationNotice } from '@/features/registration/components/registration-notice'
import { render, screen, userEvent, waitFor } from '../../test-utils'
import { server } from '../../setup'

const URL = 'http://localhost:8001/api/mcp-servers/mcp-1/registration'

function status(overrides: Record<string, unknown> = {}) {
  return {
    state: 'rejected',
    can_request: true,
    administrators_review_tools: false,
    request: {
      id: 'apr-1',
      status: 'rejected',
      reason: '<b>주소</b>를 사내 [프록시](https://x.test)로 바꿔 주세요',
      requested_at: '2026-11-09T00:00:00Z',
      decided_at: '2026-11-10T00:00:00Z',
    },
    ...overrides,
  }
}

describe('RegistrationNotice', () => {
  it('shows nothing for an approved resource and asks the server nothing', () => {
    const asked = vi.fn()
    server.use(
      http.get(URL, () => {
        asked()
        return HttpResponse.json(status())
      }),
    )
    const { container } = render(
      <RegistrationNotice type="mcp_server" id="mcp-1" state="approved" canEdit />,
    )
    expect(container).toBeEmptyDOMElement()
    expect(asked).not.toHaveBeenCalled()
  })

  it("shows the administrator's note as characters and asks again", async () => {
    const requested = vi.fn()
    const waiting = status({ state: 'pending', can_request: false, request: null })
    server.use(
      http.get(URL, () => HttpResponse.json(requested.mock.calls.length ? waiting : status())),
      http.post(URL, () => {
        requested()
        return HttpResponse.json(waiting)
      }),
    )
    render(<RegistrationNotice type="mcp_server" id="mcp-1" state="rejected" canEdit />)

    const note = await screen.findByText(/<b>주소<\/b>를 사내 \[프록시\]\(https:\/\/x\.test\)/)
    expect(note.querySelector('b, a')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: '다시 승인 요청' }))
    await waitFor(() => expect(requested).toHaveBeenCalledTimes(1))
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: '다시 승인 요청' })).not.toBeInTheDocument(),
    )
  })

  it('someone who cannot edit sees the state only', async () => {
    const asked = vi.fn()
    server.use(
      http.get(URL, () => {
        asked()
        return HttpResponse.json(status())
      }),
    )
    render(<RegistrationNotice type="mcp_server" id="mcp-1" state="pending" canEdit={false} />)

    expect(screen.getByRole('status')).toHaveAttribute('data-registration-notice', 'pending')
    // Long enough for a request to have left, had one been made.
    await new Promise((resolve) => setTimeout(resolve, 100))
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(asked).not.toHaveBeenCalled()
  })
})
