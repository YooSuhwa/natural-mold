import { render, screen, userEvent, waitFor } from '../../test-utils'
import { RegisterForm } from '@/components/auth/RegisterForm'

describe('RegisterForm', () => {
  it('submits display_name instead of asking for a legal name', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(<RegisterForm onSubmit={onSubmit} isLoading={false} error={null} />)

    await userEvent.type(screen.getByLabelText('표시 이름'), '체스터')
    await userEvent.type(screen.getByLabelText('이메일'), 'chester@example.com')
    await userEvent.type(screen.getByLabelText('비밀번호'), 'correct horse')
    await userEvent.click(screen.getByRole('checkbox'))
    await userEvent.click(screen.getByRole('button', { name: '가입하기' }))

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith({
        display_name: '체스터',
        email: 'chester@example.com',
        password: 'correct horse',
      })
    })
    expect(screen.getByText('실명을 입력하지 않아도 됩니다.')).toBeInTheDocument()
  })
})
